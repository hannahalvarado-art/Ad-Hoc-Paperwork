"""Postgres access.

Was stdlib sqlite3. SQLite cannot back a serverless deployment: Vercel's
filesystem is ephemeral and per-invocation, so a written row is gone by the
next request. The pipeline is still set-based SQL rather than an ORM.

The rest of the app was written against the sqlite3 DB-API — `?` placeholders,
`conn.execute(...).fetchone()`, `dict`-like rows. Rewriting all ~40 call sites
by hand would have been a large silent-breakage surface, so `Conn` below keeps
that surface and translates underneath. Three things could not be shimmed and
were changed at the call site instead:

    lastrowid            -> INSERT ... RETURNING id   (runner.py, comparison.py)
    INSERT OR IGNORE/REPLACE -> ON CONFLICT ...       (runner.py, pipeline.py)
    LIKE                 -> ILIKE                     (reporting.py)

That last one is a behaviour fix, not a port artifact: SQLite's LIKE is
case-insensitive for ASCII but Postgres' is not, so a plain translation would
have quietly made the dashboard's search box case-sensitive.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Neon/Supabase/Vercel Postgres all expose this. Use the *pooled* connection
# string in serverless: every invocation opens its own connection and the
# direct endpoint will exhaust its limit under any real concurrency.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Advisory-lock key for migrations. Arbitrary, just has to be stable.
_MIGRATION_LOCK = 8_412_776_301


def _dsn() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at a Postgres instance "
            "(Neon, Vercel Postgres, Supabase, or a local server)."
        )
    return DATABASE_URL


# ---------------------------------------------------------------- placeholders
# The codebase uses both sqlite3 styles: positional `?` and named `:name`.
# psycopg wants `%s` and `%(name)s`. Rewriting has to skip anything inside a
# string literal or a line comment, otherwise a `?` in user-facing SQL text
# would be mangled. `%` already in the SQL must be doubled so psycopg does not
# read it as the start of its own placeholder.
_TOKENS = re.compile(
    r"""
      (?P<squote>'(?:[^']|'')*')      # 'string literal', '' escapes
    | (?P<dquote>"(?:[^"]|"")*")      # "identifier"
    | (?P<comment>--[^\n]*)           # -- line comment
    | (?P<cast>::)                    # Postgres cast — NOT a named parameter
    | (?P<named>:[A-Za-z_][A-Za-z0-9_]*)
    | (?P<qmark>\?)
    | (?P<percent>%)
    """,
    re.VERBOSE,
)


def _translate(sql: str) -> str:
    """`?` -> `%s`, `:name` -> `%(name)s`, bare `%` -> `%%`.

    Two things here are easy to get wrong and both bite only at runtime:

      * `::` has to be consumed before `:name` can match, or `x::text` is read
        as a parameter called `text` and the cast silently disappears.
      * psycopg scans the whole query string for its own placeholders, so a `%`
        inside a *string literal* still has to be doubled — it is not protected
        by the quotes the way it is in SQL itself.
    """

    def sub(m: re.Match[str]) -> str:
        kind = m.lastgroup
        if kind == "qmark":
            return "%s"
        if kind == "named":
            return f"%({m.group(0)[1:]})s"
        if kind == "percent":
            return "%%"
        # Literals, identifiers, comments and casts keep their text, but any
        # `%` inside them still needs escaping from psycopg's parser.
        return m.group(0).replace("%", "%%")

    return _TOKENS.sub(sub, sql)


class Conn:
    """sqlite3-shaped facade over a psycopg connection.

    Only the surface this codebase actually uses: execute, executemany, and
    cursors that fetchone/fetchall into dicts.
    """

    def __init__(self, raw: psycopg.Connection):
        self._raw = raw

    def execute(self, sql: str, params: Any = ()) -> psycopg.Cursor:
        cur = self._raw.cursor(row_factory=dict_row)
        cur.execute(_translate(sql), params or None)
        return cur

    def executemany(self, sql: str, seq: Any) -> None:
        seq = list(seq)
        if not seq:
            return
        with self._raw.cursor() as cur:
            cur.executemany(_translate(sql), seq)

    def executescript(self, sql: str) -> None:
        with self._raw.cursor() as cur:
            cur.execute(sql)  # DDL: no placeholders, no translation

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    @property
    def raw(self) -> psycopg.Connection:
        return self._raw

    def __enter__(self) -> "Conn":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def connect() -> Conn:
    # autocommit mirrors sqlite3's isolation_level=None: statements land
    # immediately unless tx() has opened an explicit block.
    return Conn(psycopg.connect(_dsn(), autocommit=True))


def init_db() -> None:
    """Apply schema.sql. Idempotent (every statement is IF NOT EXISTS).

    Guarded by an advisory lock because on Vercel several cold starts can race
    here at once, and concurrent CREATE TABLE IF NOT EXISTS on the same table
    deadlocks rather than no-opping.
    """
    with connect() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.executescript(SCHEMA_PATH.read_text())
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))


@contextmanager
def tx(conn: Conn) -> Iterator[Conn]:
    """Explicit transaction, matching the old BEGIN/COMMIT/ROLLBACK block."""
    with conn.raw.transaction():
        yield conn


def get_conn() -> Iterator[Conn]:
    """FastAPI dependency. One connection per request, closed on the way out."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def rows(conn: Conn, sql: str, params: Any = ()) -> list[dict]:
    with conn.execute(sql, params) as cur:
        return [dict(r) for r in cur.fetchall()]


def one(conn: Conn, sql: str, params: Any = ()) -> dict | None:
    with conn.execute(sql, params) as cur:
        r = cur.fetchone()
    return dict(r) if r else None


def scalar(conn: Conn, sql: str, params: Any = ()) -> Any:
    with conn.execute(sql, params) as cur:
        r = cur.fetchone()
    return next(iter(r.values())) if r else None


def setting(conn: Conn, key: str, default: str = "") -> str:
    v = scalar(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return v if v is not None else default


def resolve_period(conn: Conn, label: str | None) -> dict:
    """Named period, or the most recent one."""
    if label:
        p = one(conn, "SELECT * FROM periods WHERE label = ?", (label,))
        if not p:
            raise KeyError(f"No such period: {label}")
        return p
    p = one(conn, "SELECT * FROM periods ORDER BY label DESC LIMIT 1")
    if not p:
        raise KeyError("No periods loaded. Run seed.py first.")
    return p
