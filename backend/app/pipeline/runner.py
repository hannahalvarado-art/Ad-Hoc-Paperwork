"""Runs the three stages as one transaction.

The Node version handed off through files: v2 -> v3 -> v4 -> v5, each script
re-reading and re-writing the whole dataset. Intermediate state now lives in
memory and only the final result is persisted, so there is no way to end up
with a v4 on disk that disagrees with the v5 next to it.

A run is atomic: either the period's events are fully replaced or the previous
set is left untouched.
"""

from __future__ import annotations

import json

from ..db import rows, tx
from . import stage1_mapping, stage2_contracts, stage3_exclusions

EVENT_COLUMNS = [
    "period_id", "run_id", "raw_event_id", "source_customer", "billing_customer",
    "salesforce_account", "salesforce_account_id", "customer_mapping_applied",
    "mapping_reason", "csm", "worker_name", "seso_worker_id", "paperwork_name",
    "packet_id", "num_src", "sent_date", "signed_date", "sender_name",
    "contract_ids", "contract_name", "sf_price", "flag", "has_active",
    "excluded", "exclusion_reason",
]


def run_pipeline(conn, period_id: int, source: str = "pipeline") -> dict:
    raw = rows(conn, "SELECT * FROM raw_events WHERE period_id = ?", (period_id,))
    if not raw:
        raise ValueError(
            "No raw events for this period. Load june_adhoc_v2.json via "
            "POST /api/ingest/raw first."
        )

    s1 = stage1_mapping.run(conn, raw)
    s2, tally = stage2_contracts.run(conn, s1)
    s3 = stage3_exclusions.run(conn, s2)

    stats = {
        "stage1_mapping": stage1_mapping.stats(s1),
        "stage2_contracts": stage2_contracts.stats(s2, tally),
        "stage3_exclusions": stage3_exclusions.stats(s3),
    }

    with tx(conn):
        cur = conn.execute(
            "INSERT INTO pipeline_runs (period_id, source, status) VALUES (?, ?, 'running')",
            (period_id, source),
        )
        run_id = cur.lastrowid

        conn.execute("DELETE FROM events WHERE period_id = ?", (period_id,))
        placeholders = ", ".join("?" for _ in EVENT_COLUMNS)
        conn.executemany(
            f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES ({placeholders})",
            [
                tuple(
                    period_id if c == "period_id"
                    else run_id if c == "run_id"
                    else e.get(c)
                    for c in EVENT_COLUMNS
                )
                for e in s3
            ],
        )
        conn.execute(
            "UPDATE pipeline_runs SET status='ok', finished_at=datetime('now'), stats=? WHERE id=?",
            (json.dumps(stats), run_id),
        )

    return {"run_id": run_id, "events": len(s3), "stats": stats}


def ingest_raw(conn, period_id: int, records: list[dict], replace: bool = True) -> int:
    """Load june_adhoc_v2.json-shaped records."""
    cols = [
        "period_id", "enterprise_name", "account_id", "csm", "sf_price",
        "worker_name", "seso_worker_id", "paperwork_name", "packet_id",
        "num_src", "sent_date", "signed_date", "sender_name", "contract_ids",
        "has_active",
    ]

    def coerce(r: dict) -> tuple:
        price = r.get("sf_price")
        price = None if price in ("", None) else float(price)
        return (
            period_id,
            r.get("enterprise_name"),
            r.get("account_id") or None,
            r.get("csm") or None,
            price,
            r.get("worker_name"),
            str(r.get("seso_worker_id") or ""),
            r.get("paperwork_name"),
            str(r.get("packet_id") or ""),
            int(r.get("num_src") or 1),
            r.get("sent_date"),
            r.get("signed_date"),
            r.get("sender_name"),
            r.get("contract_ids"),
            int(r.get("has_active") or 0),
        )

    with tx(conn):
        if replace:
            conn.execute("DELETE FROM raw_events WHERE period_id = ?", (period_id,))
        conn.executemany(
            f"INSERT OR REPLACE INTO raw_events ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [coerce(r) for r in records],
        )
    return len(records)


def ingest_contract_lookup(conn, pairs: list[tuple[str, str]]) -> int:
    """Load contract_lookup.csv rows: packet_id -> contract_names."""
    with tx(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO contract_lookup (packet_id, contract_names) VALUES (?, ?)",
            pairs,
        )
    return len(pairs)
