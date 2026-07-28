#!/usr/bin/env python3
"""Join the paged July dimension files into one usage extract.

Development scaffolding. This performs, in Python, exactly the joins that
`app/sources/adhoc_usage.sql` performs in Snowflake — the fact table was pulled
in pages because the warehouse credentials are not configured yet, and the
dimensions came down separately. Once SNOWFLAKE_* is set, the source adapter
does all of this in one query and this script is dead weight.

    python tools/build_extract.py
    python tools/load_extract.py --period 2026-07 --csv data/july_2026_usage.csv
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

PACKETS = DATA / "july_2026_packets.csv"
WORKERS = DATA / "july_2026_workers.csv"
WORKERS_TAIL = DATA / "july_workers_tail.csv"
ENTERPRISES = DATA / "july_2026_enterprises.csv"
CONTRACTS = DATA / "july_2026_contracts.csv"
SENDERS = DATA / "july_2026_senders.csv"
OUT = DATA / "july_2026_usage.csv"

FIELDS = [
    "enterprise_name", "account_id", "csm", "sf_price", "worker_name",
    "seso_worker_id", "paperwork_name", "packet_id", "sent_date",
    "signed_date", "sender_name", "contract_ids", "contract_name", "has_active",
]


def read(path: Path, key: str) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r[key]: r for r in csv.DictReader(path.open(encoding="utf-8"))}


def main() -> int:
    workers = read(WORKERS, "seso_worker_id")
    workers.update(read(WORKERS_TAIL, "seso_worker_id"))
    enterprises = read(ENTERPRISES, "enterprise_id")
    contracts = read(CONTRACTS, "contract_ids")
    senders = read(SENDERS, "preparer_uuid")

    rows, dropped = [], 0
    for p in csv.DictReader(PACKETS.open(encoding="utf-8")):
        w = workers.get(p["seso_worker_id"])
        ent = enterprises.get((w or {}).get("enterprise_id", "")) if w else None
        if not ent or not ent.get("enterprise_name"):
            # Mirrors the SQL's `WHERE e.legal_name IS NOT NULL`: a packet whose
            # worker has no enterprise cannot be attributed to a customer, so it
            # is not usage anyone can be billed for.
            dropped += 1
            continue
        contract = contracts.get(p["contract_ids"], {})
        rows.append(
            {
                "enterprise_name": ent["enterprise_name"],
                "account_id": ent.get("account_id", ""),
                "csm": ent.get("csm", ""),
                "sf_price": ent.get("sf_price", ""),
                "worker_name": w.get("worker_name", ""),
                "seso_worker_id": p["seso_worker_id"],
                "paperwork_name": p["paperwork_name"],
                "packet_id": p["packet_id"],
                "sent_date": p["sent_date"],
                "signed_date": p["signed_date"],
                "sender_name": senders.get(p["preparer_uuid"], {}).get("sender_name", ""),
                "contract_ids": p["contract_ids"],
                "contract_name": contract.get("contract_name", ""),
                "has_active": w.get("has_active", "0"),
            }
        )

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} usage rows -> {OUT}")
    if dropped:
        print(f"  dropped {dropped} packet(s) with no resolvable enterprise")
    by_cust: dict[str, int] = {}
    for r in rows:
        by_cust[r["enterprise_name"]] = by_cust.get(r["enterprise_name"], 0) + 1
    for name, n in sorted(by_cust.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
