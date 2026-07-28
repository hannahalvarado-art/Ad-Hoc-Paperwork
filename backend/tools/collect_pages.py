#!/usr/bin/env python3
"""Merge paged Keboola query results into one extract, and report coverage.

Development scaffolding, not part of the application. It exists because the
warehouse credentials are not configured yet, so July's rows were pulled
through the Keboola MCP in pages; this stitches those pages back together and
says exactly which packet ids are still missing so the next page can be aimed
at the gap. Once SNOWFLAKE_* is set, `app/sources/keboola.py` fetches the same
rows in one call and none of this is needed.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

TOOL_RESULTS = Path(
    r"C:\Users\hshaw\.claude\projects\c--Users-hshaw-Downloads-adhoc-billing"
    r"\f92095dc-e771-4bb6-82b0-3d0ad4f63916\tool-results"
)
OUT = Path(__file__).resolve().parent.parent / "data" / "july_2026_packets.csv"
KEEP = ["packet_id", "seso_worker_id", "paperwork_name", "sent_date",
        "signed_date", "contract_ids", "preparer_uuid"]


def load() -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for f in sorted(TOOL_RESULTS.glob("mcp-claude_ai_Keboola-query_data-*.txt")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        text = payload.get("csv_data") or ""
        if not text.strip():
            continue
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "packet_id" not in reader.fieldnames:
            continue
        # Only the narrow packet-facts pages. Earlier exploratory queries used a
        # different column set and would contribute rows with the preparer
        # missing, which silently costs those rows their sender name.
        if "preparer_uuid" not in reader.fieldnames:
            continue
        for row in reader:
            pid = (row.get("packet_id") or "").strip()
            if pid:
                merged[pid] = {k: (row.get(k) or "").strip() for k in KEEP}
    # An existing output file is a page too, so reruns accumulate.
    if OUT.exists():
        for row in csv.DictReader(io.StringIO(OUT.read_text(encoding="utf-8"))):
            pid = (row.get("packet_id") or "").strip()
            if pid and pid not in merged:
                merged[pid] = {k: (row.get(k) or "").strip() for k in KEEP}
    return merged


def main() -> int:
    expected = int(sys.argv[1]) if len(sys.argv) > 1 else 1749
    merged = load()
    ids = sorted(merged, key=int)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=KEEP)
        w.writeheader()
        for pid in ids:
            w.writerow(merged[pid])

    print(f"collected {len(ids)} of {expected} packets -> {OUT}")
    if ids:
        print(f"  id range {ids[0]} .. {ids[-1]}")
        print(f"  RESUME AFTER: {ids[-1]}")
    if len(ids) < expected:
        print(f"  still missing {expected - len(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
