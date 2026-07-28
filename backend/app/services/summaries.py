"""Customer-level results for a period, and the accounting rollup over them.

Two concepts that look similar and must not be merged:

    pricing confirmation   permanent, per account, carries across months
    Good to Bill           per customer per month, reconfirmed every month

A CSM confirming $0 for an account settles the price for August too. It does
not approve August. That distinction is the reason approvals are keyed by
period and overrides are not, and it is why `pricing_status` and
`review_status` are separate columns rather than one status field.

Freezing: for an open period these rows are recomputed from live pricing on
every run and every confirmation. For a CLOSED period they are frozen — the
stored unit price and pricing source are what the month closed with, and
`rebuild` refuses to touch them. That is what makes a later price change unable
to rewrite billed history.
"""

from __future__ import annotations

import json
from decimal import Decimal

from ..db import Conn, one, rows
from ..periods import is_closed, now_utc
from ..pricing import (
    FLAG_CSM_CONFIRM,
    FLAG_CSM_CONFIRMED,
    FLAG_EXCLUDED,
    PRICED_FLAGS,
    effective,
    load_overrides,
    total,
)

# Flags that stop a customer being billed and are not a pricing question.
# CSM_CONFIRM_PRICE is deliberately absent: it blocks too, but it is reported
# separately so "waiting on a price" never gets filed under "something is wrong".
BLOCKING_FLAGS = {
    "MISSING_SALESFORCE_ACCOUNT",
    "ENTITY_BILLING_REVIEW",
    "PRICE_OUTLIER_REVIEW",
}

# Customer review statuses.
CSM_REVIEW_REQUIRED = "CSM_REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
GOOD_TO_BILL = "GOOD_TO_BILL"
READY_TO_BILL = "READY_TO_BILL"
CUSTOMER_EXCLUDED = "CUSTOMER_EXCLUDED"


def _bucket_key(r: dict) -> tuple[str, str]:
    return (r["billing_customer"], r.get("salesforce_account_id") or "")


def compute(conn: Conn, period_id: int) -> list[dict]:
    """Derive each customer's position from usage_events + live pricing."""
    ev = rows(
        conn,
        "SELECT * FROM usage_events WHERE period_id = ? AND qualification_status = 'QUALIFIED'",
        (period_id,),
    )
    # Fall back to `events` for a period processed before usage_events existed
    # (June 2026), so historical months still render.
    if not ev:
        ev = rows(conn, "SELECT * FROM events WHERE period_id = ?", (period_id,))

    overrides = load_overrides(conn)
    buckets: dict[tuple[str, str], dict] = {}

    for r in ev:
        eff = effective(r, overrides)
        key = _bucket_key(r)
        b = buckets.setdefault(
            key,
            {
                "billing_customer": r["billing_customer"],
                "salesforce_account_id": r.get("salesforce_account_id") or "",
                "salesforce_account": r.get("salesforce_account") or "",
                "csm": r.get("csm") or "",
                "_sources": set(),
                "_workers": set(),
                "billable_packets": 0,
                "excluded_packets": 0,
                "_charges": [],
                "_prices": set(),
                "_flags": set(),
                "_sources_pricing": set(),
                "_sf_status": set(),
            },
        )
        b["_sources"].add(r.get("source_customer") or "")
        if not b["csm"] and r.get("csm"):
            b["csm"] = r["csm"]

        if eff["flag"] == FLAG_EXCLUDED:
            # Retained for audit, held out of every billable total.
            b["excluded_packets"] += 1
            b["_flags"].add(FLAG_EXCLUDED)
            continue

        b["billable_packets"] += 1
        b["_workers"].add(r.get("seso_worker_id"))
        b["_charges"].append(eff["charge_decimal"])
        b["_flags"].add(eff["flag"])
        if eff["price"] is not None:
            b["_prices"].add(Decimal(str(eff["price"])))
        if eff["pricing_source"]:
            b["_sources_pricing"].add(eff["pricing_source"])
        b["_sf_status"].add(eff["sf_pricing_status"])

    approvals = _approvals(conn, period_id)
    out = []
    for key, b in buckets.items():
        flags = b["_flags"]
        blocking = sorted(flags & BLOCKING_FLAGS)
        needs_price = FLAG_CSM_CONFIRM in flags
        prices = sorted(b["_prices"])

        if b["billable_packets"] == 0 and b["excluded_packets"]:
            pricing_status, review_status = "N/A", CUSTOMER_EXCLUDED
        elif needs_price:
            pricing_status = FLAG_CSM_CONFIRM
            review_status = CSM_REVIEW_REQUIRED
        elif FLAG_CSM_CONFIRMED in flags:
            pricing_status = FLAG_CSM_CONFIRMED
            review_status = READY_TO_BILL
        else:
            pricing_status = "OK"
            review_status = READY_TO_BILL

        if blocking:
            review_status = BLOCKED

        approval = approvals.get(key)
        approved = bool(approval and approval["good_to_bill"])
        if approved and review_status == READY_TO_BILL:
            review_status = GOOD_TO_BILL

        eligible, reason = _eligibility(needs_price, blocking, review_status)

        out.append(
            {
                "billing_customer": b["billing_customer"],
                "salesforce_account_id": b["salesforce_account_id"],
                "salesforce_account": b["salesforce_account"],
                "csm": b["csm"] or "—",
                "source_customers": sorted(s for s in b["_sources"] if s),
                "billable_packets": b["billable_packets"],
                "excluded_packets": b["excluded_packets"],
                "workers": len(b["_workers"]),
                "unit_price": float(prices[0]) if len(prices) == 1 else None,
                "unit_prices": [float(p) for p in prices],
                "pricing_source": " / ".join(sorted(b["_sources_pricing"])) or "—",
                "sf_pricing_status": " / ".join(sorted(s for s in b["_sf_status"] if s)) or "—",
                "expected_amount": float(total(b["_charges"])),
                "pricing_status": pricing_status,
                "exception_status": blocking[0] if blocking else "NONE",
                "blocking_exceptions": blocking,
                "review_status": review_status,
                "good_to_bill": approved,
                "approved_by": approval["approved_by"] if approved else None,
                "approved_at": approval["approved_at"] if approved else None,
                "good_to_bill_eligible": eligible,
                "good_to_bill_blocked_reason": reason,
            }
        )

    out.sort(key=lambda r: (-r["expected_amount"], r["billing_customer"]))
    return out


def _eligibility(needs_price: bool, blocking: list[str], review_status: str) -> tuple[bool, str]:
    """Whether the Good to Bill checkbox is enabled, and why not if it isn't."""
    if review_status == CUSTOMER_EXCLUDED:
        return False, "Good to Bill unavailable — customer is excluded from billing"
    if needs_price:
        return False, "Good to Bill unavailable — pricing confirmation required"
    if blocking:
        pretty = ", ".join(f.replace("_", " ").title() for f in blocking)
        return False, f"Good to Bill unavailable — unresolved billing exception ({pretty})"
    return True, ""


def _approvals(conn: Conn, period_id: int) -> dict[tuple[str, str], dict]:
    return {
        (r["billing_customer"], r["salesforce_account_id"]): r
        for r in rows(
            conn, "SELECT * FROM customer_period_approvals WHERE period_id = ?", (period_id,)
        )
    }


# ------------------------------------------------------------------ persistence
def rebuild(conn: Conn, period_id: int, actor: str = "system") -> dict:
    """Recompute and persist the period's customer summaries.

    A CLOSED period is left exactly as it is and its stored rows are returned,
    so callers that recompute after a price change do not need to know whether
    the period they touched was closed.
    """
    period = one(conn, "SELECT * FROM periods WHERE id = ?", (period_id,))
    if period and is_closed(period):
        return totals(stored(conn, period_id))

    computed = compute(conn, period_id)
    now = now_utc()

    cols = [
        "period_id", "billing_customer", "salesforce_account_id", "salesforce_account",
        "csm", "source_customers", "billable_packets", "workers", "excluded_packets",
        "unit_price", "pricing_source", "sf_pricing_status", "expected_amount",
        "pricing_status", "exception_status", "blocking_exceptions", "review_status",
        "updated_at",
    ]
    updatable = [c for c in cols if c not in ("period_id", "billing_customer", "salesforce_account_id")]

    conn.executemany(
        f"INSERT INTO customer_period_summaries ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT (period_id, billing_customer, salesforce_account_id) DO UPDATE SET "
        + ", ".join(f"{c}=EXCLUDED.{c}" for c in updatable),
        [
            (
                period_id, r["billing_customer"], r["salesforce_account_id"],
                r["salesforce_account"], r["csm"], json.dumps(r["source_customers"]),
                r["billable_packets"], r["workers"], r["excluded_packets"],
                r["unit_price"], r["pricing_source"], r["sf_pricing_status"],
                r["expected_amount"], r["pricing_status"], r["exception_status"],
                json.dumps(r["blocking_exceptions"]), r["review_status"], now,
            )
            for r in computed
        ],
    )

    # A customer whose activity disappeared on a rerun should not linger with
    # last run's numbers.
    keys = {(r["billing_customer"], r["salesforce_account_id"]) for r in computed}
    for existing in rows(
        conn,
        "SELECT id, billing_customer, salesforce_account_id "
        "FROM customer_period_summaries WHERE period_id = ?",
        (period_id,),
    ):
        if (existing["billing_customer"], existing["salesforce_account_id"]) not in keys:
            conn.execute("DELETE FROM customer_period_summaries WHERE id = ?", (existing["id"],))

    return totals(computed)


def stored(conn: Conn, period_id: int) -> list[dict]:
    """The persisted summaries, shaped like `compute` so callers do not branch."""
    approvals = _approvals(conn, period_id)
    out = []
    for r in rows(
        conn,
        "SELECT * FROM customer_period_summaries WHERE period_id = ? "
        "ORDER BY expected_amount DESC, billing_customer",
        (period_id,),
    ):
        key = (r["billing_customer"], r["salesforce_account_id"])
        approval = approvals.get(key)
        approved = bool(approval and approval["good_to_bill"])
        blocking = json.loads(r["blocking_exceptions"] or "[]")
        review_status = r["review_status"]
        if approved and review_status == READY_TO_BILL:
            review_status = GOOD_TO_BILL
        eligible, reason = _eligibility(
            r["pricing_status"] == FLAG_CSM_CONFIRM, blocking, review_status
        )
        out.append(
            {
                **{k: r[k] for k in (
                    "billing_customer", "salesforce_account_id", "salesforce_account",
                    "csm", "billable_packets", "excluded_packets", "workers",
                    "unit_price", "pricing_source", "sf_pricing_status",
                    "expected_amount", "pricing_status", "exception_status",
                )},
                "source_customers": json.loads(r["source_customers"] or "[]"),
                "unit_prices": [r["unit_price"]] if r["unit_price"] is not None else [],
                "blocking_exceptions": blocking,
                "review_status": review_status,
                "good_to_bill": approved,
                "approved_by": approval["approved_by"] if approved else None,
                "approved_at": approval["approved_at"] if approved else None,
                "good_to_bill_eligible": eligible,
                "good_to_bill_blocked_reason": reason,
            }
        )
    return out


def for_period(conn: Conn, period: dict) -> list[dict]:
    """Frozen rows for a closed period, live ones otherwise."""
    return stored(conn, period["id"]) if is_closed(period) else compute(conn, period["id"])


# -------------------------------------------------------------- accounting view
def totals(customers: list[dict]) -> dict:
    """The accounting counts. `customers` is the output of compute/stored."""
    billable = [c for c in customers if c["review_status"] != CUSTOMER_EXCLUDED]
    blocked_pricing = [c for c in billable if c["pricing_status"] == FLAG_CSM_CONFIRM]
    blocked_other = [c for c in billable if c["blocking_exceptions"]]
    approved = [c for c in billable if c["good_to_bill"]]
    # "Not yet approved" counts only customers who *could* be approved, so a
    # customer blocked on pricing is reported once, under pricing, rather than
    # inflating two different queues.
    not_approved = [c for c in billable if not c["good_to_bill"] and c["good_to_bill_eligible"]]

    return {
        "customers": len(billable),
        "customers_ready_for_review": len(
            [c for c in billable if c["review_status"] == CSM_REVIEW_REQUIRED]
        ),
        "customers_good_to_bill": len(approved),
        "customers_not_yet_approved": len(not_approved),
        "blocked_by_pricing": len(blocked_pricing),
        "blocked_by_other_exceptions": len(blocked_other),
        "unresolved_exceptions": sum(len(c["blocking_exceptions"]) for c in billable),
        "total_billable_packets": sum(c["billable_packets"] for c in billable),
        "excluded_packets": sum(c["excluded_packets"] for c in customers),
        "excluded_customers": len(customers) - len(billable),
        "expected_amount": float(
            total(Decimal(str(c["expected_amount"] or 0)) for c in billable)
        ),
        "workers": sum(c["workers"] for c in billable),
    }


def can_mark_ready(customers: list[dict]) -> tuple[bool, str]:
    """Whether the whole period may move to READY_TO_BILL.

    The agreed rule: no unresolved blocking exceptions and no outstanding
    pricing confirmations. Good to Bill approvals are tracked and reported but
    do not gate the period — Accounting sees the not-yet-approved count and
    decides. Both blockers are reported together so closing a month is not a
    sequence of one-error-at-a-time attempts.
    """
    t = totals(customers)
    problems = []
    if t["blocked_by_pricing"]:
        problems.append(f"{t['blocked_by_pricing']} customer(s) still need pricing confirmation")
    if t["blocked_by_other_exceptions"]:
        problems.append(
            f"{t['blocked_by_other_exceptions']} customer(s) have unresolved billing exceptions"
        )
    if problems:
        return False, "; ".join(problems)
    return True, ""
