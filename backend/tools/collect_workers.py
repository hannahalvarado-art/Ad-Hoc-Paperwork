#!/usr/bin/env python3
"""Merge paged worker-dimension results. Companion to collect_pages.py.

Same reason for existing: the warehouse credentials are not configured yet, so
the July dimension rows were pulled through the Keboola MCP in pages. Reports
the highest id collected so the next page can resume from it.
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
OUT = Path(__file__).resolve().parent.parent / "data" / "july_2026_workers.csv"
KEEP = ["seso_worker_id", "worker_name", "enterprise_id", "has_active"]


def main() -> int:
    expected = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    merged: dict[str, dict] = {}

    if OUT.exists():
        for row in csv.DictReader(OUT.open(encoding="utf-8")):
            merged[row["seso_worker_id"]] = {k: (row.get(k) or "") for k in KEEP}

    for f in sorted(TOOL_RESULTS.glob("mcp-claude_ai_Keboola-query_data-*.txt")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        text = payload.get("csv_data") or ""
        reader = csv.DictReader(io.StringIO(text))
        names = reader.fieldnames or []
        if "seso_worker_id" not in names or "enterprise_id" not in names:
            continue
        for row in reader:
            wid = (row.get("seso_worker_id") or "").strip()
            if wid:
                merged[wid] = {k: (row.get(k) or "").strip() for k in KEEP}

    ids = sorted(merged, key=int)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=KEEP)
        w.writeheader()
        for wid in ids:
            w.writerow(merged[wid])

    print(f"collected {len(ids)} workers -> {OUT}")
    if ids:
        print(f"  RESUME AFTER: {ids[-1]}")
    if expected and len(ids) < expected:
        print(f"  still missing {expected - len(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
