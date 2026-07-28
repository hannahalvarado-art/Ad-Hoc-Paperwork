"""The approved CSM override layer.

Replaces localStorage['adhoc_csm_overrides_v1']. Salesforce is still never
written to — an override lives only here, and every confirm/revoke/import
appends to override_audit so a revoked price can still be explained later.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from .. import audit, auth, periods, reporting
from ..db import Conn, get_conn, one, resolve_period, rows, tx
from ..models import OverrideCreate, OverrideImport
from ..services import summaries

router = APIRouter(prefix="/api", tags=["overrides"])


def _recalculate_open_periods(conn: Conn, actor: str) -> list[str]:
    """Re-price every period that is still open, and no others.

    A confirmed price is permanent and applies to future months automatically —
    but a CLOSED period is the billed record and must not move. summaries.rebuild
    already refuses closed periods; they are filtered here too so the audit note
    can say exactly which months changed.
    """
    touched = []
    for p in periods.list_periods(conn):
        if periods.is_closed(p):
            continue
        summaries.rebuild(conn, p["id"], actor=actor)
        touched.append(p["label"])
    return touched


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/review-queue")
def review_queue(period: str | None = None, conn: Conn = Depends(get_conn)):
    try:
        p = resolve_period(conn, period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    q = reporting.review_queue(conn, p["id"])
    return {
        "accounts": q,
        "confirmed": sum(1 for a in q if a["confirmed"]),
        "total": len(q),
    }


@router.get("/overrides")
def list_overrides(conn: Conn = Depends(get_conn)):
    return rows(conn, "SELECT * FROM price_overrides ORDER BY billing_customer")


@router.put("/overrides")
def upsert_override(
    body: OverrideCreate,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    acct = one(conn, "SELECT * FROM sf_accounts WHERE account_id = ?", (body.sf_account_id,))
    if not acct:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown Salesforce account {body.sf_account_id}. Add it under Accounts first.",
        )
    if acct["adhoc_price"] is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This account already has a Salesforce Ad Hoc price "
                f"(${acct['adhoc_price']:,.2f}). Salesforce wins over an override."
            ),
        )

    previous = one(
        conn, "SELECT * FROM price_overrides WHERE sf_account_id = ?", (body.sf_account_id,)
    )
    record = {
        "sf_account_id": body.sf_account_id,
        "sf_account_name": body.sf_account_name or acct["name"],
        "billing_customer": body.billing_customer or acct["name"],
        "confirmed_unit_price": float(body.confirmed_unit_price),
        # Authenticated identity, not a request field.
        "confirmed_by": user.email,
        "confirmed_at": _now(),
        "confirmation_source": "CSM",
        "effective_date": body.effective_date,
        "note": body.note or "",
    }

    with tx(conn):
        conn.execute(
            """INSERT INTO price_overrides
               (sf_account_id, sf_account_name, billing_customer, confirmed_unit_price,
                confirmed_by, confirmed_at, confirmation_source, effective_date, note)
               VALUES (:sf_account_id, :sf_account_name, :billing_customer, :confirmed_unit_price,
                       :confirmed_by, :confirmed_at, :confirmation_source, :effective_date, :note)
               ON CONFLICT(sf_account_id) DO UPDATE SET
                 sf_account_name=excluded.sf_account_name,
                 billing_customer=excluded.billing_customer,
                 confirmed_unit_price=excluded.confirmed_unit_price,
                 confirmed_by=excluded.confirmed_by,
                 confirmed_at=excluded.confirmed_at,
                 effective_date=excluded.effective_date,
                 note=excluded.note""",
            record,
        )
        conn.execute(
            "INSERT INTO override_audit (sf_account_id, action, unit_price, actor, note, payload) "
            "VALUES (?, 'confirmed', ?, ?, ?, ?)",
            (
                record["sf_account_id"],
                record["confirmed_unit_price"],
                record["confirmed_by"],
                record["note"],
                json.dumps(record),
            ),
        )
        # In the same transaction as the price it describes. Outside it, a
        # failure in between would commit a price change with no audit row —
        # which is exactly what happened once during development.
        audit.record(
            conn,
            audit.PRICE_CHANGED if previous else audit.PRICE_CONFIRMED,
            user.email,
            customer=record["billing_customer"],
            account_id=record["sf_account_id"],
            entity="price_overrides",
            entity_id=record["sf_account_id"],
            previous_value=previous["confirmed_unit_price"] if previous else None,
            new_value=record["confirmed_unit_price"],
            note=f"Effective {record['effective_date']}.",
        )

    # Permanent pricing: applies to every open period immediately and to future
    # ones automatically. Closed periods are skipped, which is the rule that
    # stops a confirmation rewriting a month that was already billed.
    #
    # Outside the transaction on purpose: summaries are derived, rebuilding is
    # idempotent, and a slow recalculation should not hold a lock on the
    # pricing table.
    touched = _recalculate_open_periods(conn, user.email)
    return {
        **record,
        "pricing_status": "CSM_CONFIRMED_PRICE",
        "recalculated_periods": touched,
        # Salesforce genuinely has no price and the UI must keep saying so.
        "sf_pricing_status": "Not Configured",
        "pricing_source": "CSM Confirmed Override",
    }


@router.delete("/overrides/{sf_account_id}")
def revoke_override(
    sf_account_id: str,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    existing = one(
        conn, "SELECT * FROM price_overrides WHERE sf_account_id = ?", (sf_account_id,)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="No override on that account.")

    with tx(conn):
        conn.execute("DELETE FROM price_overrides WHERE sf_account_id = ?", (sf_account_id,))
        conn.execute(
            "INSERT INTO override_audit (sf_account_id, action, unit_price, actor, payload) "
            "VALUES (?, 'revoked', ?, ?, ?)",
            (
                sf_account_id,
                existing["confirmed_unit_price"],
                user.email,
                json.dumps(existing),
            ),
        )
        audit.record(
            conn, audit.PRICE_REVOKED, user.email,
            customer=existing.get("billing_customer"), account_id=sf_account_id,
            entity="price_overrides", entity_id=sf_account_id,
            previous_value=existing["confirmed_unit_price"], new_value=None,
        )

    touched = _recalculate_open_periods(conn, user.email)
    return {
        "revoked": sf_account_id,
        "events_returned_to_review": True,
        "recalculated_periods": touched,
    }


@router.get("/overrides/export")
def export_overrides(conn: Conn = Depends(get_conn)):
    """Same envelope the dashboard's Export button produced, so existing
    files round-trip through the importer below."""
    return {
        "schema": "adhoc_csm_pricing_overrides",
        "version": 1,
        "generated_at": _now(),
        "overrides": rows(conn, "SELECT * FROM price_overrides ORDER BY sf_account_id"),
    }


@router.post("/overrides/import")
def import_overrides(
    body: OverrideImport,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    imported, skipped = 0, []
    with tx(conn):
        for o in body.overrides:
            acct_id = o.get("sf_account_id")
            price = o.get("confirmed_unit_price")
            if not acct_id or price is None:
                skipped.append({"record": o, "why": "missing sf_account_id or price"})
                continue
            if not one(conn, "SELECT 1 FROM sf_accounts WHERE account_id = ?", (acct_id,)):
                skipped.append({"record": o, "why": f"unknown account {acct_id}"})
                continue
            payload = {
                "sf_account_id": acct_id,
                "sf_account_name": o.get("sf_account_name", ""),
                "billing_customer": o.get("billing_customer", ""),
                "confirmed_unit_price": float(price),
                "confirmed_by": o.get("confirmed_by") or "imported",
                "confirmed_at": o.get("confirmed_at") or _now(),
                "confirmation_source": o.get("confirmation_source") or "CSM",
                "effective_date": o.get("effective_date") or _now()[:10],
                "note": o.get("note", ""),
            }
            conn.execute(
                """INSERT INTO price_overrides
                   (sf_account_id, sf_account_name, billing_customer, confirmed_unit_price,
                    confirmed_by, confirmed_at, confirmation_source, effective_date, note)
                   VALUES (:sf_account_id, :sf_account_name, :billing_customer,
                           :confirmed_unit_price, :confirmed_by, :confirmed_at,
                           :confirmation_source, :effective_date, :note)
                   ON CONFLICT(sf_account_id) DO UPDATE SET
                     confirmed_unit_price=excluded.confirmed_unit_price,
                     confirmed_by=excluded.confirmed_by,
                     confirmed_at=excluded.confirmed_at,
                     effective_date=excluded.effective_date,
                     note=excluded.note""",
                payload,
            )
            conn.execute(
                "INSERT INTO override_audit (sf_account_id, action, unit_price, actor, payload) "
                "VALUES (?, 'imported', ?, ?, ?)",
                (acct_id, payload["confirmed_unit_price"], payload["confirmed_by"], json.dumps(payload)),
            )
            imported += 1

    touched = _recalculate_open_periods(conn, user.email) if imported else []
    if imported:
        audit.record(
            conn, audit.PRICE_CONFIRMED, user.email, entity="price_overrides",
            new_value={"imported": imported},
            note=f"Bulk import. Recalculated: {', '.join(touched) or 'no open periods'}.",
        )
    return {"imported": imported, "skipped": skipped, "recalculated_periods": touched}


@router.get("/overrides/audit")
def override_audit(limit: int = 200, conn: Conn = Depends(get_conn)):
    # Named for the table, not the route: a function called `audit` here shadows
    # the `audit` module imported at the top, and every audit.record() call in
    # this file then fails at runtime rather than at import.
    return rows(
        conn,
        "SELECT id, sf_account_id, action, unit_price, actor, note, created_at "
        "FROM override_audit ORDER BY id DESC LIMIT ?",
        (limit,),
    )
