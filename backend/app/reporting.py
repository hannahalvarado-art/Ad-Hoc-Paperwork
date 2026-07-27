"""Read models for the dashboard.

Each of these was a render* function in the HTML that re-derived pricing from
the inlined DATA array. They now share one pricing pass, so a KPI and the
table under it cannot disagree.
"""

from __future__ import annotations

from decimal import Decimal

from .db import rows
from .pricing import (
    FLAG_CSM_CONFIRM,
    FLAG_CSM_CONFIRMED,
    FLAG_EXCLUDED,
    PRICED_FLAGS,
    effective,
    load_overrides,
    total,
)

SORTABLE = {
    "billing_customer": "billing_customer",
    "source_customer": "source_customer",
    "worker_name": "worker_name",
    "paperwork_name": "paperwork_name",
    "sent_date": "sent_date",
    "signed_date": "signed_date",
    "flag": "flag",
}


def _load(conn, period_id: int) -> tuple[list[dict], dict]:
    ev = rows(conn, "SELECT * FROM events WHERE period_id = ?", (period_id,))
    return ev, load_overrides(conn)


def decorate(conn, period_id: int) -> list[dict]:
    """Every event with its effective pricing attached."""
    ev, ovr = _load(conn, period_id)
    out = []
    for r in ev:
        e = effective(r, ovr)
        out.append({**r, "effective": e})
    return out


# ------------------------------------------------------------------ KPIs
def kpis(conn, period_id: int) -> dict:
    ev = decorate(conn, period_id)
    billable = [r for r in ev if r["effective"]["flag"] != FLAG_EXCLUDED]
    excluded = [r for r in ev if r["effective"]["flag"] == FLAG_EXCLUDED]
    priced = [r for r in billable if r["effective"]["flag"] in PRICED_FLAGS]
    confirmed = [r for r in billable if r["effective"]["flag"] == FLAG_CSM_CONFIRMED]
    awaiting = [r for r in billable if r["effective"]["flag"] == FLAG_CSM_CONFIRM]

    return {
        "billable_events": len(billable),
        "excluded_events": len(excluded),
        "expected_total": float(total(r["effective"]["charge_decimal"] for r in priced)),
        "priced_events": len(priced),
        "confirmed_events": len(confirmed),
        "awaiting_csm_events": len(awaiting),
        "awaiting_csm_accounts": len(
            {r["salesforce_account_id"] for r in awaiting if r["salesforce_account_id"]}
        ),
        "billing_customers": len({r["billing_customer"] for r in billable}),
        "workers": len({r["seso_worker_id"] for r in billable}),
    }


# ----------------------------------------------------- summary by customer
def summary(conn, period_id: int) -> dict:
    ev = [r for r in decorate(conn, period_id) if r["effective"]["flag"] != FLAG_EXCLUDED]

    buckets: dict[str, dict] = {}
    for r in ev:
        e = r["effective"]
        b = buckets.setdefault(
            r["billing_customer"],
            {
                "billing_customer": r["billing_customer"],
                "csm": r["csm"] or "—",
                "events": 0,
                "ok": 0,
                "review": 0,
                "_workers": set(),
                "_prices": set(),
                "_flags": set(),
                "_charges": [],
            },
        )
        b["events"] += 1
        b["_workers"].add(r["seso_worker_id"])
        b["_charges"].append(e["charge_decimal"])
        if e["flag"] in PRICED_FLAGS:
            b["ok"] += 1
        else:
            b["review"] += 1
        if e["price"] is not None:
            b["_prices"].add(Decimal(str(e["price"])))
        b["_flags"].add(e["flag"])

    out = []
    for b in buckets.values():
        prices = sorted(b.pop("_prices"))
        flags = b.pop("_flags")
        status = (
            "OK"
            if b["review"] == 0
            else (
                next((f for f in flags if f not in PRICED_FLAGS), FLAG_CSM_CONFIRM)
                if b["ok"] == 0
                else "MIXED"
            )
        )
        out.append(
            {
                **{k: v for k, v in b.items() if not k.startswith("_")},
                "workers": len(b["_workers"]),
                "unit_prices": [float(p) for p in prices],
                "expected": float(total(b["_charges"])),
                "status": status,
            }
        )

    out.sort(key=lambda r: -r["expected"])
    return {
        "rows": out,
        "totals": {
            "events": len(ev),
            "workers": len({r["seso_worker_id"] for r in ev}),
            "expected": float(total(r["effective"]["charge_decimal"] for r in ev)),
            "review": sum(r["review"] for r in out),
        },
    }


# ------------------------------------------------------- excluded rollup
def excluded(conn, period_id: int) -> list[dict]:
    ev = rows(
        conn,
        "SELECT * FROM events WHERE period_id = ? AND excluded = 1",
        (period_id,),
    )
    buckets: dict[str, dict] = {}
    for r in ev:
        b = buckets.setdefault(
            r["source_customer"],
            {
                "source_customer": r["source_customer"],
                "events": 0,
                "_workers": set(),
                "reason": r["exclusion_reason"],
            },
        )
        b["events"] += 1
        b["_workers"].add(r["seso_worker_id"])
    return [
        {
            "source_customer": b["source_customer"],
            "events": b["events"],
            "workers": len(b["_workers"]),
            "reason": b["reason"],
        }
        for b in buckets.values()
    ]


# --------------------------------------------------------- review queue
def review_queue(conn, period_id: int) -> list[dict]:
    """Accounts with no Salesforce Ad Hoc price, grouped for CSM confirmation."""
    ev = rows(
        conn,
        "SELECT * FROM events WHERE period_id = ? AND flag = ? AND excluded = 0",
        (period_id, FLAG_CSM_CONFIRM),
    )
    ovr = load_overrides(conn)

    buckets: dict[str, dict] = {}
    for r in ev:
        k = r["salesforce_account_id"] or ""
        b = buckets.setdefault(
            k,
            {
                "sf_account_id": k,
                "sf_account_name": r["salesforce_account"],
                "billing_customer": r["billing_customer"],
                "csm": r["csm"] or "—",
                "packets": 0,
                "_workers": set(),
            },
        )
        b["packets"] += 1
        b["_workers"].add(r["seso_worker_id"])

    out = []
    for b in buckets.values():
        ov = ovr.get(b["sf_account_id"])
        price = Decimal(str(ov["confirmed_unit_price"])) if ov else None
        out.append(
            {
                **{k: v for k, v in b.items() if not k.startswith("_")},
                "workers": len(b["_workers"]),
                "override": ov,
                "confirmed": ov is not None,
                "period_expected": float(price * b["packets"]) if price is not None else None,
                "reason": "No Ad Hoc Paperwork product found on any opportunity",
            }
        )
    out.sort(key=lambda r: (r["confirmed"], -r["packets"]))
    return out


# ------------------------------------------------------------ event list
def event_list(
    conn,
    period_id: int,
    search: str = "",
    billing_customer: str = "",
    flag: str = "",
    sort: str = "source_customer",
    direction: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Server-side filter, sort and page.

    The original capped the DOM at 1,200 rows and told the user to narrow the
    filters; paging removes that ceiling.

    `flag` filters on the EFFECTIVE flag, so selecting 'CSM Confirmed' finds
    events whose stored flag is still CSM_CONFIRM_PRICE but which an override
    has since released. That means the flag filter is applied in Python, after
    pricing, rather than in SQL.
    """
    where = ["period_id = ?"]
    params: list = [period_id]

    if billing_customer:
        where.append("billing_customer = ?")
        params.append(billing_customer)

    if search:
        cols = [
            "source_customer", "billing_customer", "worker_name",
            "paperwork_name", "contract_name", "salesforce_account",
        ]
        # ILIKE, not LIKE: SQLite's LIKE is case-insensitive for ASCII but
        # Postgres' is not, so a literal port would have silently made the
        # dashboard's search box case-sensitive.
        where.append("(" + " OR ".join(f"COALESCE({c},'') ILIKE ?" for c in cols) + ")")
        params.extend([f"%{search}%"] * len(cols))

    order = SORTABLE.get(sort, "source_customer")
    dir_sql = "DESC" if direction.lower() == "desc" else "ASC"

    candidates = rows(
        conn,
        f"SELECT * FROM events WHERE {' AND '.join(where)} "
        f"ORDER BY {order} {dir_sql}, id ASC",
        params,
    )

    ovr = load_overrides(conn)
    decorated = [{**r, "effective": effective(r, ovr)} for r in candidates]
    if flag:
        decorated = [r for r in decorated if r["effective"]["flag"] == flag]

    grand = rows(conn, "SELECT COUNT(*) AS n FROM events WHERE period_id = ?", (period_id,))[0]["n"]
    page = decorated[offset : offset + limit]

    return {
        "rows": [_public(r) for r in page],
        "matched": len(decorated),
        "total": grand,
        "limit": limit,
        "offset": offset,
    }


def _public(r: dict) -> dict:
    e = r["effective"]
    return {
        "id": r["id"],
        "source_customer": r["source_customer"],
        "billing_customer": r["billing_customer"],
        "salesforce_account": r["salesforce_account"],
        "salesforce_account_id": r["salesforce_account_id"],
        "customer_mapping_applied": bool(r["customer_mapping_applied"]),
        "mapping_reason": r["mapping_reason"],
        "csm": r["csm"],
        "worker_name": r["worker_name"],
        "seso_worker_id": r["seso_worker_id"],
        "paperwork_name": r["paperwork_name"],
        "contract_name": r["contract_name"],
        "contract_ids": r["contract_ids"],
        "sent_date": r["sent_date"],
        "signed_date": r["signed_date"],
        "has_active": bool(r["has_active"]),
        "flag": e["flag"],
        "flag_label": e["label"],
        "unit_price": e["price"],
        "charge": e["charge"],
        "pricing_source": e["pricing_source"],
        "sf_pricing_status": e["sf_pricing_status"],
    }


def billing_customers(conn, period_id: int) -> list[str]:
    return [
        r["billing_customer"]
        for r in rows(
            conn,
            "SELECT DISTINCT billing_customer FROM events WHERE period_id = ? "
            "ORDER BY billing_customer",
            (period_id,),
        )
    ]
