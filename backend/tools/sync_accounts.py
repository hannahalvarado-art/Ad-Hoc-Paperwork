#!/usr/bin/env python3
"""Upsert Salesforce account config from a month's extract.

`sf_accounts` is the app's copy of the ACCT/TARGET_PRICE configuration: account
name, CSM, and the contracted Ad Hoc unit price. It was seeded once from the
June dataset; a new month can introduce accounts that were not in it, and those
accounts need a row before a CSM can attach a price override to them.

A NULL adhoc_price is meaningful and is written as NULL, not skipped: it is what
routes an account to CSM_CONFIRM_PRICE. Overwriting it with 0 would silently
bill that customer nothing.

    python tools/sync_accounts.py --csv data/july_2026_accounts.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect, init_db, tx  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="account_id,account_name,csm,sf_price")
    args = ap.parse_args()

    rows = list(csv.DictReader(Path(args.csv).open(encoding="utf-8-sig")))
    init_db()
    conn = connect()
    try:
        with tx(conn):
            for r in rows:
                price = (r.get("sf_price") or "").strip()
                conn.execute(
                    """INSERT INTO sf_accounts (account_id, name, csm, adhoc_price)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (account_id) DO UPDATE SET
                         name = EXCLUDED.name,
                         csm = EXCLUDED.csm,
                         adhoc_price = EXCLUDED.adhoc_price,
                         updated_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')""",
                    (
                        r["account_id"].strip(),
                        r["account_name"].strip(),
                        (r.get("csm") or "").strip() or None,
                        None if price == "" else float(price),
                    ),
                )
        priced = sum(1 for r in rows if (r.get("sf_price") or "").strip())
        print(
            f"Synced {len(rows)} Salesforce account(s): "
            f"{priced} with an Ad Hoc price, {len(rows) - priced} routing to CSM review"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
