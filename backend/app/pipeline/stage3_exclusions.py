"""Stage 3 — customer exclusions.

Port of 03_customer_exclusion.js. Excluded events are retained (never
deleted) so the audit trail survives; they are just held out of every
billable total.

One robustness change: the JS matched EXCLUDED_CUSTOMERS keys against
source_customer with exact string equality, which is why the dict key had
to be spelled 'Peri & Sons Farms, Inc.' while the Salesforce account is
'Peri & Sons Farm, Inc.'. A single stray character in either place would
have silently billed an excluded customer. Matching now normalises
punctuation and whitespace the same way hex_comparison_analysis.js already
did with its nCust() helper — so the rule holds even if someone retypes the
name with a different comma.
"""

from __future__ import annotations

import re

from ..db import rows
from ..pricing import FLAG_EXCLUDED


def normalise(s: str | None) -> str:
    """nCust() from hex_comparison_analysis.js."""
    s = (s or "").lower()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def load_exclusions(conn) -> dict[str, str]:
    return {
        normalise(r["source_customer"]): r["reason"]
        for r in rows(conn, "SELECT * FROM excluded_customers WHERE active = 1")
    }


def run(conn, events: list[dict]) -> list[dict]:
    excl = load_exclusions(conn)
    out: list[dict] = []
    for r in events:
        e = dict(r)
        reason = excl.get(normalise(r["source_customer"]))
        if reason:
            e["flag"] = FLAG_EXCLUDED
            e["excluded"] = 1
            e["exclusion_reason"] = reason
        else:
            e["excluded"] = 0
            e["exclusion_reason"] = ""
        out.append(e)
    return out


def stats(out: list[dict]) -> dict:
    """The REQUESTED TOTALS + RULE-STILL-APPLIED blocks from the original."""
    billable = [r for r in out if not r["excluded"]]
    excluded = [r for r in out if r["excluded"]]

    by: dict[str, int] = {}
    for r in billable:
        by[r["flag"]] = by.get(r["flag"], 0) + 1

    unresolved = sum(
        1
        for r in billable
        if r["flag"]
        in {
            "MISSING_SALESFORCE_ACCOUNT",
            "ENTITY_BILLING_REVIEW",
            "PRICE_OUTLIER_REVIEW",
        }
    )

    return {
        "billable_events": len(billable),
        "excluded_events": len(excluded),
        "billable_flags": by,
        "billing_customers": len({r["billing_customer"] for r in billable}),
        "customers_with_ok": len(
            {r["billing_customer"] for r in billable if r["flag"] == "OK"}
        ),
        "csm_confirm_accounts": len(
            {
                r["salesforce_account_id"] or r["billing_customer"]
                for r in billable
                if r["flag"] == "CSM_CONFIRM_PRICE"
            }
        ),
        "other_unresolved": unresolved,
        # Duplicate rule: contract-only duplicates collapsed upstream.
        "contract_only_dupes_removed": sum(int(r["num_src"] or 1) - 1 for r in out),
        # No-active-contract rule: these are included, unlike the Hex logic.
        "no_active_included": sum(1 for r in billable if not r["has_active"]),
        "no_active_excluded": sum(1 for r in excluded if not r["has_active"]),
    }
