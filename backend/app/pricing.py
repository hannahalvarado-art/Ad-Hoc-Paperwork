"""The pricing hierarchy.

Port of eff() from june_adhoc_dashboard.html, moved server-side.

    1. Salesforce contracted Ad Hoc price from a Closed-Won opportunity
    2. else an approved CSM override  -> CSM_CONFIRMED_PRICE (may legitimately be $0)
    3. else CSM_CONFIRM_PRICE        -> held, never auto-$0, never a borrowed price

In the original this ran in the browser and was called separately by
renderKpis / renderSummary / renderDetail. Any drift between those three
call sites was a silent reconciliation error, so it now runs in exactly one
place and every endpoint reads the result.

Money is Decimal throughout. The JS used floats, which is survivable at
$4/packet but not something to carry into a billing system.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CENTS = Decimal("0.01")

# Flags produced by the pipeline stages
FLAG_OK = "OK"
FLAG_CSM_CONFIRM = "CSM_CONFIRM_PRICE"
FLAG_CSM_CONFIRMED = "CSM_CONFIRMED_PRICE"  # derived, never stored
FLAG_OUTLIER = "PRICE_OUTLIER_REVIEW"
FLAG_MISSING = "MISSING_SALESFORCE_ACCOUNT"
FLAG_ENTITY_REVIEW = "ENTITY_BILLING_REVIEW"
FLAG_EXCLUDED = "CUSTOMER_EXCLUDED"

PRICED_FLAGS = {FLAG_OK, FLAG_CSM_CONFIRMED}

FLAG_LABELS = {
    FLAG_OK: "Billable",
    FLAG_CSM_CONFIRMED: "CSM Confirmed",
    FLAG_CSM_CONFIRM: "CSM Confirm Price",
    FLAG_OUTLIER: "Price outlier",
    FLAG_ENTITY_REVIEW: "Entity billing review",
    FLAG_MISSING: "Missing account",
    FLAG_EXCLUDED: "Customer excluded",
}


def money(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v)).quantize(CENTS, rounding=ROUND_HALF_UP)


def effective(event: dict, overrides: dict[str, dict]) -> dict:
    """Resolve one event's price, charge, pricing source and effective flag.

    `overrides` is keyed by salesforce_account_id.
    Returns floats for JSON transport; totals are summed as Decimal upstream.
    """
    base = event["flag"]
    sf_price = money(event.get("sf_price"))

    if base == FLAG_EXCLUDED:
        return _result(FLAG_EXCLUDED, None, None, "—", "n/a")

    if base == FLAG_OK:
        return _result(
            FLAG_OK,
            sf_price,
            sf_price,
            "Salesforce contracted",
            f"Configured (${sf_price:,.2f})" if sf_price is not None else "Configured",
        )

    if base == FLAG_CSM_CONFIRM:
        ov = overrides.get(event.get("salesforce_account_id") or "")
        if ov:
            p = money(ov["confirmed_unit_price"])
            return _result(
                FLAG_CSM_CONFIRMED, p, p, "CSM Confirmed Override", "Not Configured",
                override=ov,
            )
        # Held. Not $0.
        return _result(FLAG_CSM_CONFIRM, None, None, "", "Not Configured")

    # Outlier / missing account / entity review: no confident price.
    return _result(
        base,
        sf_price,
        None,
        "",
        "No account" if base == FLAG_MISSING else "Held",
    )


def _result(flag, price, charge, source, sf_status, override=None) -> dict:
    return {
        "flag": flag,
        "label": FLAG_LABELS.get(flag, flag),
        "price": float(price) if price is not None else None,
        "charge": float(charge) if charge is not None else None,
        "charge_decimal": charge,
        "pricing_source": source,
        "sf_pricing_status": sf_status,
        "override": override,
    }


def load_overrides(conn) -> dict[str, dict]:
    cur = conn.execute("SELECT * FROM price_overrides")
    return {r["sf_account_id"]: dict(r) for r in cur.fetchall()}


def total(charges) -> Decimal:
    out = Decimal("0")
    for c in charges:
        if c is not None:
            out += c
    return out.quantize(CENTS, rounding=ROUND_HALF_UP)
