"""The audit trail.

override_audit already existed and covered price confirmations. It is left
alone — those rows are history and the export format reads them — but every new
action writes here instead, so "who changed what about this month's billing" is
one query rather than a union over per-feature tables.

Append-only by convention: nothing in the application issues UPDATE or DELETE
against audit_log. A revoked approval is a second row, not an edit of the first,
which is the whole point — a revoked price stays explainable after the fact.
"""

from __future__ import annotations

import json
from typing import Any

from .db import Conn, rows

# Actions worth being able to filter on later. Free-form strings would drift
# ('good_to_bill' vs 'goodToBill' vs 'GOOD_TO_BILL') and quietly break a filter.
PRICE_CONFIRMED = "csm_price_confirmed"
PRICE_CHANGED = "csm_price_changed"
PRICE_REVOKED = "csm_price_revoked"
GOOD_TO_BILL_SET = "good_to_bill_checked"
GOOD_TO_BILL_REVOKED = "good_to_bill_revoked"
PERIOD_CREATED = "billing_period_created"
PERIOD_RERUN = "billing_period_rerun"
USAGE_REFRESHED = "usage_refreshed"
PRICING_REFRESHED = "pricing_refreshed"
PERIOD_READY = "period_marked_ready_to_bill"
PERIOD_CLOSED = "period_closed"
PERIOD_REOPENED = "period_reopened"
NOTIFICATION_SENT = "slack_notification_sent"
NOTIFICATION_RESENT = "slack_notification_resent"
EXCEPTION_RESOLVED = "exception_resolved"
MANUAL_OVERRIDE = "manual_override"


def _encode(value: Any) -> str | None:
    """Scalars stay readable; structures become JSON.

    A previous_value of `4.0` should read as `4.0` in the log, not `"4.0"` with
    quotes, because these strings are shown to people.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, default=str, sort_keys=True)


def record(
    conn: Conn,
    action: str,
    actor: str,
    *,
    period_id: int | None = None,
    billing_type: str | None = None,
    customer: str | None = None,
    account_id: str | None = None,
    entity: str | None = None,
    entity_id: Any = None,
    previous_value: Any = None,
    new_value: Any = None,
    source: str = "ui",
    note: str | None = None,
) -> None:
    """Write one audit row.

    Deliberately takes an explicit `actor` rather than reaching for a request
    context: the cron path has no user, and passing 'system:cron' explicitly is
    clearer than a helper that silently substitutes one.
    """
    conn.execute(
        """INSERT INTO audit_log
             (actor, action, period_id, billing_type, customer, account_id,
              entity, entity_id, previous_value, new_value, source, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            actor, action, period_id, billing_type, customer, account_id,
            entity, _encode(entity_id), _encode(previous_value),
            _encode(new_value), source, note,
        ),
    )


def trail(
    conn: Conn,
    period_id: int | None = None,
    action: str = "",
    customer: str = "",
    limit: int = 200,
) -> list[dict]:
    where: list[str] = []
    params: list[Any] = []
    if period_id is not None:
        where.append("period_id = ?")
        params.append(period_id)
    if action:
        where.append("action = ?")
        params.append(action)
    if customer:
        where.append("customer = ?")
        params.append(customer)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    return rows(conn, f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ?", params)
