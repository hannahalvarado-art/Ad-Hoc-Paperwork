"""Editing the rules that used to require a code change.

CUSTOMER_MAP, EXCLUDED_CUSTOMERS, ACCT, TARGET_PRICE and the OVERLOOK
constants were all literals inside the Node scripts. Adding one mapping meant
editing 01_customer_mapping_and_pricing.js and re-running the chain by hand.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn, rows, tx
from ..models import AccountUpsert, CustomerMapUpsert, ExclusionUpsert, SettingUpsert

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def all_config(conn: sqlite3.Connection = Depends(get_conn)):
    return {
        "accounts": rows(conn, "SELECT * FROM sf_accounts ORDER BY name"),
        "customer_map": rows(conn, "SELECT * FROM customer_map ORDER BY source_customer"),
        "exclusions": rows(conn, "SELECT * FROM excluded_customers ORDER BY source_customer"),
        "entity_split_rules": [
            {
                **r,
                "senders": rows(
                    conn,
                    "SELECT sender_name, resolves_to FROM entity_split_senders WHERE rule_id = ?",
                    (r["id"],),
                ),
            }
            for r in rows(conn, "SELECT * FROM entity_split_rules ORDER BY source_customer")
        ],
        "settings": rows(conn, "SELECT * FROM settings ORDER BY key"),
    }


@router.put("/accounts")
def upsert_account(body: AccountUpsert, conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        conn.execute(
            """INSERT INTO sf_accounts (account_id, name, csm, adhoc_price)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 name=excluded.name, csm=excluded.csm,
                 adhoc_price=excluded.adhoc_price, updated_at=datetime('now')""",
            (body.account_id, body.name, body.csm, body.adhoc_price),
        )
    return {"saved": body.account_id, "rerun_required": True}


@router.put("/customer-map")
def upsert_mapping(body: CustomerMapUpsert, conn: sqlite3.Connection = Depends(get_conn)):
    if not conn.execute(
        "SELECT 1 FROM sf_accounts WHERE account_id = ?", (body.sf_account_id,)
    ).fetchone():
        raise HTTPException(
            status_code=400,
            detail=f"Add Salesforce account {body.sf_account_id} before mapping to it.",
        )
    with tx(conn):
        conn.execute(
            """INSERT INTO customer_map
               (source_customer, billing_customer, sf_account_id, reason, active)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_customer) DO UPDATE SET
                 billing_customer=excluded.billing_customer,
                 sf_account_id=excluded.sf_account_id,
                 reason=excluded.reason, active=excluded.active""",
            (
                body.source_customer,
                body.billing_customer,
                body.sf_account_id,
                body.reason,
                int(body.active),
            ),
        )
    return {"saved": body.source_customer, "rerun_required": True}


@router.delete("/customer-map/{source_customer}")
def delete_mapping(source_customer: str, conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        conn.execute("DELETE FROM customer_map WHERE source_customer = ?", (source_customer,))
    return {"deleted": source_customer, "rerun_required": True}


@router.put("/exclusions")
def upsert_exclusion(body: ExclusionUpsert, conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        conn.execute(
            """INSERT INTO excluded_customers (source_customer, reason, active)
               VALUES (?, ?, ?)
               ON CONFLICT(source_customer) DO UPDATE SET
                 reason=excluded.reason, active=excluded.active""",
            (body.source_customer, body.reason, int(body.active)),
        )
    return {"saved": body.source_customer, "rerun_required": True}


@router.delete("/exclusions/{source_customer}")
def delete_exclusion(source_customer: str, conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        conn.execute("DELETE FROM excluded_customers WHERE source_customer = ?", (source_customer,))
    return {"deleted": source_customer, "rerun_required": True}


@router.put("/settings/{key}")
def upsert_setting(key: str, body: SettingUpsert, conn: sqlite3.Connection = Depends(get_conn)):
    with tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, note) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, note=excluded.note",
            (key, body.value, body.note),
        )
    return {"saved": key, "rerun_required": True}
