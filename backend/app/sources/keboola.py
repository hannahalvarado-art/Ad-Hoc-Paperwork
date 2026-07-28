"""Usage from the Keboola "Seso Prod" Snowflake warehouse.

The query lives in adhoc_usage.sql next to this file, with the reasoning for
each filter. This module is only the connection and the type coercion.

Connection: a read-only Snowflake user against the Keboola-managed database
(SAPI_10112). The Keboola Storage API is not used because it exports whole
tables and cannot run the join this needs — and the curated
`out.c-data_model.out_ad_hoc_paperwork` table, which could be exported, has no
packet id and so cannot support idempotent merging.

`snowflake-connector-python` is imported lazily. It is a heavy dependency and
the app must still boot without it: an unconfigured or driver-less deployment
should report *why* on /api/health rather than failing to start.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import SourceUnavailable

SQL_PATH = Path(__file__).resolve().parent / "adhoc_usage.sql"

ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "")
USER = os.environ.get("SNOWFLAKE_USER", "")
PASSWORD = os.environ.get("SNOWFLAKE_PASSWORD", "")
WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "")
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "SAPI_10112")
ROLE = os.environ.get("SNOWFLAKE_ROLE", "")

# Guard against a runaway month. Well above any plausible volume (June 2026 was
# 656 rows); this exists so a broken date filter fails loudly instead of trying
# to pull the whole warehouse into a serverless function.
MAX_ROWS = int(os.environ.get("ADHOC_MAX_SOURCE_ROWS", "200000"))


class KeboolaSource:
    name = "keboola"

    def available(self) -> bool:
        return bool(ACCOUNT and USER and PASSWORD)

    def describe(self) -> str:
        if not self.available():
            return "Keboola/Snowflake (not configured)"
        return f"Keboola/Snowflake {DATABASE} as {USER}"

    def _connect(self):
        if not self.available():
            raise SourceUnavailable(
                "The Keboola warehouse is not configured. Set SNOWFLAKE_ACCOUNT, "
                "SNOWFLAKE_USER and SNOWFLAKE_PASSWORD (plus SNOWFLAKE_WAREHOUSE "
                "and SNOWFLAKE_ROLE if your user has no defaults). Until then you "
                "can run a month from an uploaded extract with source=upload."
            )
        try:
            import snowflake.connector  # noqa: PLC0415 - deliberately lazy
        except ImportError as exc:
            raise SourceUnavailable(
                "snowflake-connector-python is not installed, so usage cannot be "
                "pulled from the warehouse. `pip install -r requirements.txt`."
            ) from exc

        opts: dict[str, Any] = {
            "account": ACCOUNT,
            "user": USER,
            "password": PASSWORD,
            "database": DATABASE,
            "client_session_keep_alive": False,
        }
        if WAREHOUSE:
            opts["warehouse"] = WAREHOUSE
        if ROLE:
            opts["role"] = ROLE
        try:
            return snowflake.connector.connect(**opts)
        except Exception as exc:  # driver raises a family of errors
            raise SourceUnavailable(f"Cannot reach Snowflake: {exc}") from exc

    def fetch(self, start: str, end: str) -> list[dict]:
        sql = SQL_PATH.read_text()
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                # Named binds, matching the :start_date / :end_date in the file
                # so the same text runs unchanged in a Snowflake worksheet.
                cur.execute(sql, {"start_date": start, "end_date": end})
                columns = [c[0] for c in cur.description]
                out: list[dict] = []
                for row in cur:
                    if len(out) >= MAX_ROWS:
                        raise SourceUnavailable(
                            f"The warehouse returned more than {MAX_ROWS:,} rows for "
                            f"{start}..{end}. That is far beyond a normal month — "
                            f"refusing to continue rather than persisting it."
                        )
                    out.append(_coerce(dict(zip(columns, row))))
                return out
            finally:
                cur.close()
        finally:
            conn.close()


def _coerce(r: dict) -> dict:
    """Normalise warehouse types to what raw_events stores.

    Snowflake hands back Decimal for NUMBER and date objects for DATE. The
    pipeline compares ids as strings and dates as 'YYYY-MM-DD' text, so
    converting here keeps every downstream comparison honest — a Decimal
    packet id would silently never match a stored string one.
    """
    def text(v: Any) -> str:
        return "" if v is None else str(v)

    def iso(v: Any) -> str | None:
        if v is None:
            return None
        return v.isoformat()[:10] if hasattr(v, "isoformat") else str(v)[:10]

    price = r.get("sf_price")
    return {
        "enterprise_name": text(r.get("enterprise_name")),
        "account_id": text(r.get("account_id")),
        "csm": text(r.get("csm")),
        "sf_price": None if price is None else float(price),
        "worker_name": text(r.get("worker_name")),
        "seso_worker_id": text(r.get("seso_worker_id")),
        "paperwork_name": text(r.get("paperwork_name")),
        "packet_id": text(r.get("packet_id")),
        "num_src": 1,
        "sent_date": iso(r.get("sent_date")),
        "signed_date": iso(r.get("signed_date")),
        "sender_name": text(r.get("sender_name")),
        "contract_ids": text(r.get("contract_ids")),
        "contract_name": text(r.get("contract_name")),
        "has_active": int(r.get("has_active") or 0),
    }
