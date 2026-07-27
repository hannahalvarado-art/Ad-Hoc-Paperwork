"""The approved CSM override layer.

Replaces localStorage['adhoc_csm_overrides_v1']. Salesforce is still never
written to — an override lives only here, and every confirm/revoke/import
appends to override_audit so a revoked price can still be explained later.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from .. import reporting
from ..db import get_conn, one, resolve_period, rows, tx
from ..models import OverrideCreate, OverrideImport

router = APIRouter(prefix="/api", tags=["overrides"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/review-queue")
def review_queue(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
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
def list_overrides(conn: sqlite3.Connection = Depends(get_conn)):
    return rows(conn, "SELECT * FROM price_overrides ORDER BY billing_customer")


@router.put("/overrides")
def upsert_override(body: OverrideCreate, conn: sqlite3.Connection = Depends(get_conn)):
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

    record = {
        "sf_account_id": body.sf_account_id,
        "sf_account_name": body.sf_account_name or acct["name"],
        "billing_customer": body.billing_customer or acct["name"],
        "confirmed_unit_price": float(body.confirmed_unit_price),
        "confirmed_by": body.confirmed_by,
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

    return record


@router.delete("/overrides/{sf_account_id}")
def revoke_override(
    sf_account_id: str,
    actor: str = "",
    conn: sqlite3.Connection = Depends(get_conn),
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
                actor or None,
                json.dumps(existing),
            ),
        )
    return {"revoked": sf_account_id, "events_returned_to_review": True}


@router.get("/overrides/export")
def export_overrides(conn: sqlite3.Connection = Depends(get_conn)):
    """Same envelope the dashboard's Export button produced, so existing
    files round-trip through the importer below."""
    return {
        "schema": "adhoc_csm_pricing_overrides",
        "version": 1,
        "generated_at": _now(),
        "overrides": rows(conn, "SELECT * FROM price_overrides ORDER BY sf_account_id"),
    }


@router.post("/overrides/import")
def import_overrides(body: OverrideImport, conn: sqlite3.Connection = Depends(get_conn)):
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

    return {"imported": imported, "skipped": skipped}


@router.get("/overrides/audit")
def audit(limit: int = 200, conn: sqlite3.Connection = Depends(get_conn)):
    return rows(
        conn,
        "SELECT id, sf_account_id, action, unit_price, actor, note, created_at "
        "FROM override_audit ORDER BY id DESC LIMIT ?",
        (limit,),
    )
