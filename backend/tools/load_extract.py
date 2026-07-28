#!/usr/bin/env python3
"""Load a usage extract CSV into a billing period, then run the monthly workflow.

Development helper for validating a month before the warehouse connection is
configured. It does not contain any billing logic: it stages rows into
raw_events and then calls the same `monthly.run_period` the scheduled job calls,
with source='upload' so the run uses what was just staged.

    python tools/load_extract.py --period 2026-07 --csv path/to/extract.csv
    python tools/load_extract.py --period 2026-07 --json path/to/rows.json

The CSV columns are the ones app/sources/adhoc_usage.sql selects.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import periods  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.services import monthly  # noqa: E402

FIELDS = [
    "enterprise_name", "account_id", "csm", "sf_price", "worker_name",
    "seso_worker_id", "paperwork_name", "packet_id", "sent_date",
    "signed_date", "sender_name", "contract_ids", "contract_name", "has_active",
]


def from_csv(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        price = (r.get("sf_price") or "").strip()
        out.append(
            {
                **{f: (r.get(f) or "").strip() for f in FIELDS},
                "sf_price": None if price == "" else float(price),
                "has_active": int(r.get("has_active") or 0),
                "num_src": 1,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--csv")
    ap.add_argument("--json")
    ap.add_argument("--actor", default="dev@sesolabor.com")
    ap.add_argument("--stage-only", action="store_true",
                    help="load raw_events but do not run the workflow")
    args = ap.parse_args()

    year, month = periods.parse_label(args.period)

    if args.csv:
        records = from_csv(Path(args.csv).read_text(encoding="utf-8-sig"))
    elif args.json:
        records = json.loads(Path(args.json).read_text())
    else:
        return ap.error("Provide --csv or --json")

    init_db()
    conn = connect()
    try:
        period, created = periods.get_or_create_period(
            conn, year, month, created_by=args.actor, run_source="manual"
        )
        periods.assert_open(period, "load a usage extract")
        n = runner.ingest_raw(conn, period["id"], records, replace=True)
        pairs = sorted(
            {
                (str(r["packet_id"]), r["contract_name"])
                for r in records
                if r.get("packet_id") and r.get("contract_name")
            }
        )
        if pairs:
            runner.ingest_contract_lookup(conn, pairs)
        print(
            f"Staged {n} rows into {period['name']} "
            f"({'created' if created else 'existing'}), {len(pairs)} contract names"
        )
        if args.stage_only:
            return 0

        result = monthly.run_period(
            conn, year, month,
            actor=args.actor, run_type="manual", source_name="upload",
            notify=False, refresh_usage=False,
        )
        t = result["totals"]
        m = result["merge"]
        print(
            f"\nRun {result['run_id']}: {result['events']} events "
            f"({m['events_added']} added, {m['events_updated']} updated, "
            f"{m['events_disqualified']} disqualified)"
        )
        print(
            f"  {t['total_billable_packets']} billable packets across "
            f"{t['customers']} customers, ${t['expected_amount']:,.2f} known"
        )
        print(
            f"  {t['blocked_by_pricing']} awaiting CSM price, "
            f"{t['blocked_by_other_exceptions']} blocked by exceptions, "
            f"{t['excluded_packets']} excluded packets"
        )
        print(f"  status -> {result['period']['status']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
