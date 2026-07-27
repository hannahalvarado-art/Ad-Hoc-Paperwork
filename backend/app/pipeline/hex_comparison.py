"""Reconciliation against the legacy Hex report.

Port of hex_comparison_analysis.js. The matching cascade is unchanged:

  1. exact multiset match on customer | worker | document (month-level),
     greedily paired; a differing sent date is recorded as a note, not a miss
  2. residual pass relaxing the document  -> DOCUMENT_TYPE_MISMATCH
  3. residual pass relaxing the customer  -> CUSTOMER_MATCHING_DIFFERENCE
  4. whatever is left -> CLAUDE_ONLY / HEX_ONLY with an attributed reason

Hex bills one row per contract, so its rows are collapsed to canonical events
on customer|worker|document|sent before comparing; the collapsed count is
reported as hex_contract_fanout_rows, which is the legacy over-billing.

Two fixes to the original while porting:

  * it read june_adhoc_v2.json and then referenced r.flag / r.expected_charge,
    which stage 1 had not yet added — so every Claude-side flag and charge in
    the comparison output was undefined. It now reads the final events table.
  * the hand-rolled parseCSV is replaced with csv.reader; the original dropped
    a trailing field on files without a final newline.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")

HEX_COLUMNS = {
    "customer": "Enterprise Name",
    "worker": "Worker Name",
    "paperwork": "Paperwork Name",
    "sender": "Sender Name",
    "sent": "Sent at",
    "signed": "Signed Date",
    "contract": "Active Contract",
}


def n_cust(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[.,]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def n_worker(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def n_doc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def to_la_date(ts: str | None) -> str:
    """toLA() — render a UTC-ish timestamp as a Los Angeles calendar date."""
    s = (ts or "").strip()
    if not s:
        return ""
    s = s.replace(" ", "T", 1)
    s = re.sub(r"\+00$", "+00:00", s)
    s = re.sub(r"Z$", "+00:00", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(LA).date().isoformat()


def parse_hex_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(f or "").strip() for f in (reader.fieldnames or [])]
    missing = [c for c in HEX_COLUMNS.values() if c not in reader.fieldnames]
    if missing:
        raise ValueError(f"Hex export is missing columns: {', '.join(missing)}")

    out = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        out.append(
            {
                "customer": (row[HEX_COLUMNS["customer"]] or "").strip(),
                "worker": (row[HEX_COLUMNS["worker"]] or "").strip(),
                "paperwork": (row[HEX_COLUMNS["paperwork"]] or "").strip(),
                "sender": (row[HEX_COLUMNS["sender"]] or "").strip(),
                "sent": to_la_date(row[HEX_COLUMNS["sent"]]),
                "signed": to_la_date(row[HEX_COLUMNS["signed"]]),
                "contract": (row[HEX_COLUMNS["contract"]] or "").strip(),
            }
        )
    return out


def collapse_hex(hex_rows: list[dict]) -> tuple[list[dict], int]:
    """Collapse Hex's per-contract fan-out into canonical events."""
    buckets: dict[tuple, dict] = {}
    for h in hex_rows:
        key = (n_cust(h["customer"]), n_worker(h["worker"]), n_doc(h["paperwork"]), h["sent"])
        e = buckets.get(key)
        if e is None:
            e = buckets[key] = {**h, "raw_rows": 0, "contracts": set(), "signeds": set()}
        e["raw_rows"] += 1
        e["contracts"].add(h["contract"])
        e["signeds"].add(h["signed"])
    events = list(buckets.values())
    return events, len(hex_rows) - len(events)


def _side_claude(c: dict) -> dict:
    return {
        "paperwork": c["paperwork_name"],
        "sent": c["sent_date"],
        "signed": c["signed_date"],
        "flag": c["flag"],
        "charge": c.get("charge"),
        "active": str(c.get("has_active", "")),
    }


def _side_hex(h: dict) -> dict:
    return {
        "paperwork": h["paperwork"],
        "sent": h["sent"],
        "signed": ",".join(sorted(x for x in h["signeds"] if x)),
        "contracts": " | ".join(sorted(x for x in h["contracts"] if x)),
        "raw_rows": h["raw_rows"],
    }


def compare(events: list[dict], hex_rows: list[dict], period_label: str) -> dict:
    hex_events, fanout = collapse_hex(hex_rows)

    c_index: dict[tuple, list[dict]] = defaultdict(list)
    h_index: dict[tuple, list[dict]] = defaultdict(list)
    for c in events:
        c_index[
            (n_cust(c["source_customer"]), n_worker(c["worker_name"]), n_doc(c["paperwork_name"]))
        ].append(c)
    for h in hex_events:
        h_index[
            (n_cust(h["customer"]), n_worker(h["worker"]), n_doc(h["paperwork"]))
        ].append(h)

    records: list[dict] = []
    matched = exact_day = 0
    c_only: list[dict] = []
    h_only: list[dict] = []

    for key in set(c_index) | set(h_index):
        cs, hs = c_index[key], h_index[key]
        n = min(len(cs), len(hs))
        for i in range(n):
            matched += 1
            c, h = cs[i], hs[i]
            same = c["sent_date"] == h["sent"]
            if same:
                exact_day += 1
            notes = []
            if not same:
                notes.append(f"sent-date field differs (new {c['sent_date']} / Hex {h['sent']})")
            if h["raw_rows"] > 1:
                notes.append(f"Hex billed {h['raw_rows']}x (per contract)")
            records.append(
                {
                    "category": "MATCHED_BETWEEN_SOURCES",
                    "sub": ("DATE_MISMATCH" if not same else ("DUP" if h["raw_rows"] > 1 else "")),
                    "customer": c["source_customer"],
                    "worker": c["worker_name"],
                    "notes": "; ".join(notes),
                    "claude": _side_claude(c),
                    "hex": _side_hex(h),
                }
            )
        c_only.extend(cs[n:])
        h_only.extend(hs[n:])

    # Relaxed residual passes.
    used_c: set[int] = set()
    used_h: set[int] = set()

    def relaxed(mode: str) -> None:
        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for h in h_only:
            if id(h) in used_h:
                continue
            k = (
                (n_cust(h["customer"]), n_worker(h["worker"]))
                if mode == "doc"
                else (n_worker(h["worker"]), n_doc(h["paperwork"]))
            )
            buckets[k].append(h)

        for c in c_only:
            if id(c) in used_c:
                continue
            k = (
                (n_cust(c["source_customer"]), n_worker(c["worker_name"]))
                if mode == "doc"
                else (n_worker(c["worker_name"]), n_doc(c["paperwork_name"]))
            )
            cands = [h for h in buckets.get(k, []) if id(h) not in used_h]
            if not cands:
                continue
            h = cands[0]
            used_c.add(id(c))
            used_h.add(id(h))
            records.append(
                {
                    "category": "DOCUMENT_TYPE_MISMATCH" if mode == "doc" else "CUSTOMER_MATCHING_DIFFERENCE",
                    "sub": "",
                    "customer": c["source_customer"],
                    "worker": c["worker_name"],
                    "notes": "",
                    "claude": _side_claude(c),
                    "hex": _side_hex(h),
                }
            )

    relaxed("doc")
    relaxed("cust")

    final_c = [c for c in c_only if id(c) not in used_c]
    final_h = [h for h in h_only if id(h) not in used_h]
    hex_cust = {n_cust(h["customer"]) for h in hex_events}

    for c in final_c:
        if not c.get("has_active"):
            reason = "Worker has no active contract — Hex excludes, new logic includes"
        elif n_cust(c["source_customer"]) not in hex_cust:
            reason = "Customer absent from Hex"
        else:
            reason = "In-Hex customer and active worker, unmatched (date or name — review)"
        records.append(
            {
                "category": "CLAUDE_ONLY",
                "sub": "",
                "customer": c["source_customer"],
                "worker": c["worker_name"],
                "notes": reason,
                "claude": _side_claude(c),
                "hex": None,
            }
        )

    for h in final_h:
        if h["sent"][:7] != period_label:
            reason = f"Hex sent {h['sent']} (outside {period_label})"
        else:
            reason = "Sent in period per Hex but absent from new logic (date field or exclusion — review)"
        records.append(
            {
                "category": "HEX_ONLY",
                "sub": "",
                "customer": h["customer"],
                "worker": h["worker"],
                "notes": reason,
                "claude": None,
                "hex": _side_hex(h),
            }
        )

    per_customer = _per_customer(events, hex_rows, hex_events, records)

    summary = {
        "new_logic_total": len(events),
        "hex_raw_rows": len(hex_rows),
        "hex_canonical_events": len(hex_events),
        "hex_contract_fanout_rows": fanout,
        "exact_matches": exact_day,
        "matched_month": matched,
        "date_field_diff_on_matched": matched - exact_day,
        "claude_only": len(final_c),
        "hex_only": len(final_h),
        "document_type_mismatch": sum(1 for r in records if r["category"] == "DOCUMENT_TYPE_MISMATCH"),
        "customer_matching_difference": sum(
            1 for r in records if r["category"] == "CUSTOMER_MATCHING_DIFFERENCE"
        ),
        "contract_only_dupes_removed_new_logic": sum(int(c.get("num_src") or 1) - 1 for c in events),
        "workers_included_no_active_contract": sum(1 for c in events if not c.get("has_active")),
        "no_active_and_claude_only": sum(1 for c in final_c if not c.get("has_active")),
        "new_logic_customers": len({n_cust(c["source_customer"]) for c in events}),
        "hex_customers": len(hex_cust),
    }

    return {"summary": summary, "per_customer": per_customer, "records": records}


def _per_customer(events, hex_rows, hex_events, records) -> list[dict]:
    names = {c["source_customer"] for c in events} | {h["customer"] for h in hex_events}
    out = []
    for name in names:
        k = n_cust(name)
        recs = [r for r in records if n_cust(r["customer"]) == k]
        out.append(
            {
                "customer": name,
                "new_events": sum(1 for c in events if n_cust(c["source_customer"]) == k),
                "hex_events": sum(1 for h in hex_events if n_cust(h["customer"]) == k),
                "hex_raw_rows": sum(1 for h in hex_rows if n_cust(h["customer"]) == k),
                "matched": sum(1 for r in recs if r["category"] == "MATCHED_BETWEEN_SOURCES"),
                "claude_only": sum(1 for r in recs if r["category"] == "CLAUDE_ONLY"),
                "hex_only": sum(1 for r in recs if r["category"] == "HEX_ONLY"),
                "other": sum(
                    1
                    for r in recs
                    if r["category"] in {"DOCUMENT_TYPE_MISMATCH", "CUSTOMER_MATCHING_DIFFERENCE"}
                ),
                "no_active": sum(
                    1
                    for c in events
                    if n_cust(c["source_customer"]) == k and not c.get("has_active")
                ),
            }
        )
    out.sort(key=lambda r: -r["new_events"])
    return out
