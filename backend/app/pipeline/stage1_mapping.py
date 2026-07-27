"""Stage 1 — customer mapping and pricing.

Port of 01_customer_mapping_and_pricing.js.

Behaviour preserved exactly; the three hardcoded dicts now come from tables:

    CUSTOMER_MAP  -> customer_map
    TARGET_PRICE  -> sf_accounts.adhoc_price (for mapping targets)
    ACCT          -> sf_accounts
    price > 16    -> settings['price_outlier_threshold']
"""

from __future__ import annotations

from ..db import rows, setting
from ..pricing import (
    FLAG_CSM_CONFIRM,
    FLAG_MISSING,
    FLAG_OK,
    FLAG_OUTLIER,
    money,
)


def load_config(conn) -> dict:
    accounts = {r["account_id"]: r for r in rows(conn, "SELECT * FROM sf_accounts")}
    cmap = {
        r["source_customer"]: r
        for r in rows(conn, "SELECT * FROM customer_map WHERE active = 1")
    }
    threshold = money(setting(conn, "price_outlier_threshold", "16"))
    return {"accounts": accounts, "customer_map": cmap, "outlier_threshold": threshold}


def classify(account_id: str | None, price, threshold) -> str:
    """Unchanged from the JS classify()."""
    if not account_id:
        return FLAG_MISSING
    if price is None:
        return FLAG_CSM_CONFIRM
    if threshold is not None and price > threshold:
        return FLAG_OUTLIER
    return FLAG_OK


def run(conn, raw: list[dict]) -> list[dict]:
    cfg = load_config(conn)
    accounts = cfg["accounts"]
    cmap = cfg["customer_map"]
    threshold = cfg["outlier_threshold"]

    out: list[dict] = []
    for r in raw:
        src = r["enterprise_name"]
        m = cmap.get(src)

        if m:
            # Mapped: billing identity and price both come from the TARGET account.
            target = accounts.get(m["sf_account_id"], {})
            applied = True
            billing = m["billing_customer"]
            sf_id = m["sf_account_id"]
            sf_name = target.get("name", "")
            reason = m["reason"]
            csm = target.get("csm") or ""
            price = money(target.get("adhoc_price"))
        else:
            applied = False
            billing = src
            sf_id = r.get("account_id") or ""
            acct = accounts.get(sf_id) if sf_id else None
            sf_name = (acct or {}).get("name", "") if sf_id else ""
            reason = ""
            csm = r.get("csm") or (acct or {}).get("csm") or ""
            price = money(r.get("sf_price"))

        flag = classify(sf_id or None, price, threshold)

        out.append(
            {
                "raw_event_id": r.get("id"),
                "source_customer": src,
                "billing_customer": billing,
                "salesforce_account": sf_name,
                "salesforce_account_id": sf_id,
                "customer_mapping_applied": 1 if applied else 0,
                "mapping_reason": reason,
                "csm": csm,
                "worker_name": r.get("worker_name"),
                "seso_worker_id": r.get("seso_worker_id"),
                "paperwork_name": r.get("paperwork_name"),
                "packet_id": r.get("packet_id"),
                "num_src": int(r.get("num_src") or 1),
                "sent_date": r.get("sent_date"),
                "signed_date": r.get("signed_date"),
                "sender_name": r.get("sender_name"),
                "contract_ids": r.get("contract_ids"),
                "sf_price": float(price) if price is not None else None,
                "flag": flag,
                "has_active": int(r.get("has_active") or 0),
            }
        )
    return out


def stats(out: list[dict]) -> dict:
    """The console.log validation block from the original script."""
    by: dict[str, int] = {}
    for r in out:
        by[r["flag"]] = by.get(r["flag"], 0) + 1
    mapped = sum(1 for r in out if r["customer_mapping_applied"])
    return {
        "events": len(out),
        "flags": by,
        "mapped": mapped,
        "billing_customers": len({r["billing_customer"] for r in out}),
    }
