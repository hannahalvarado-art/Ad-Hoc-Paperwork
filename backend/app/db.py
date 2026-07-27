"""SQLite access.

Deliberately stdlib sqlite3 rather than an ORM: the pipeline is set-based
transformation logic that reads far better as SQL + dicts than as mapped
objects, and it keeps the dependency list to FastAPI + uvicorn.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("ADHOC_DB", BASE_DIR / "data" / "adhoc.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None,
        # FastAPI runs sync dependencies and sync endpoints in a threadpool
        # without guaranteeing both land on the same worker thread, so a
        # connection opened while resolving get_conn is often used from a
        # different thread than the one that created it. SQLite rejects that by
        # default, and because thread assignment varies per request the failure
        # is intermittent — some endpoints return 200 while others raise
        # ProgrammingError against the same database.
        #
        # This is safe here specifically because get_conn yields a fresh
        # connection per request and closes it in its finally block: the object
        # moves between threads but is never used by two of them at once. Do
        # not promote this to a shared module-level connection, where the flag
        # would stop being a portability fix and start hiding a race.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait rather than failing outright if another connection holds a write
    # lock; WAL allows concurrent readers but only one writer.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. isolation_level=None means we drive BEGIN ourselves."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def rows(conn: sqlite3.Connection, sql: str, params: Any = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: Any = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(conn: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else None


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    v = scalar(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return v if v is not None else default


def resolve_period(conn: sqlite3.Connection, label: str | None) -> dict:
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
