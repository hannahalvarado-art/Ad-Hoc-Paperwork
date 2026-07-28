"""Billing period lifecycle, accounting controls, approvals and the audit trail.

Every mutating route here takes its actor from the authenticated session. None
of them accept a name in the request body, which is the whole reason the auth
layer exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit, auth, periods, slack
from ..db import Conn, get_conn, one, resolve_period, rows, tx
from ..models import (
    ApprovalUpsert,
    ClosePeriod,
    ReopenPeriod,
    RunPeriodRequest,
)
from ..periods import PeriodClosed
from ..services import monthly, summaries
from ..sources import DEFAULT_SOURCE, get_source

router = APIRouter(prefix="/api", tags=["billing"])


def _period(conn: Conn, label: str | None) -> dict:
    try:
        return resolve_period(conn, label)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _closed(exc: PeriodClosed) -> HTTPException:
    # 409, not 403: the caller is permitted, the period's state does not accept
    # the write. The message names the way out.
    return HTTPException(status_code=409, detail=str(exc))


# ------------------------------------------------------------------- periods
@router.get("/billing-periods")
def list_billing_periods(billing_type: str | None = None, conn: Conn = Depends(get_conn)):
    out = [periods.public(p) for p in periods.list_periods(conn, billing_type)]
    default = None
    try:
        default = resolve_period(conn, None)["label"]
    except KeyError:
        pass
    return {"periods": out, "default": default, "billing_types": periods.BILLING_TYPES}


@router.get("/billing-periods/{label}")
def get_billing_period(label: str, conn: Conn = Depends(get_conn)):
    p = _period(conn, label)
    customers = summaries.for_period(conn, p)
    ready, blocked_reason = summaries.can_mark_ready(customers)
    return {
        "period": periods.public(p),
        "totals": summaries.totals(customers),
        "can_mark_ready": ready,
        "ready_blocked_reason": blocked_reason,
        "latest_run": monthly.latest_run(conn, p["id"]),
        "notifications": slack.history(conn, p["id"]),
    }


@router.post("/billing-periods/run")
def run_billing_period(
    body: RunPeriodRequest,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    """Run or re-run a month.

    The same call the scheduled job makes. `year`/`month` default to the prior
    calendar month, which is what the 2nd-of-the-month job will target.
    """
    year, month = (body.year, body.month) if body.year and body.month else periods.prior_month()
    try:
        return monthly.run_period(
            conn, year, month,
            actor=user.email,
            run_type="manual",
            source_name=body.source,
            notify=body.notify,
            refresh_usage=body.refresh_usage,
        )
    except PeriodClosed as exc:
        raise _closed(exc) from exc
    except monthly.RunFailed as exc:
        # The period is already marked FAILED and the run recorded; 502 because
        # the failure is virtually always the upstream source, not the request.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/billing-periods/{label}/refresh-usage")
def refresh_usage(
    label: str,
    source: str | None = None,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    p = _period(conn, label)
    try:
        result = monthly.run_period(
            conn, p["year"], p["month"], actor=user.email, run_type="manual",
            source_name=source, notify=False, refresh_usage=True,
        )
    except PeriodClosed as exc:
        raise _closed(exc) from exc
    except monthly.RunFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record(
        conn, audit.USAGE_REFRESHED, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"), new_value=result["merge"],
    )
    return result


@router.post("/billing-periods/{label}/refresh-pricing")
def refresh_pricing(
    label: str,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    """Re-apply pricing without re-pulling usage.

    Cheaper than a full run and the right button after a CSM confirms a price
    or a Salesforce rate changes.
    """
    p = _period(conn, label)
    try:
        periods.assert_open(p, "refresh pricing")
    except PeriodClosed as exc:
        raise _closed(exc) from exc
    totals = summaries.rebuild(conn, p["id"], actor=user.email)
    audit.record(
        conn, audit.PRICING_REFRESHED, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"), new_value=totals,
    )
    return {"period": periods.public(p), "totals": totals}


@router.post("/billing-periods/{label}/ready-to-bill")
def mark_ready(
    label: str,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    p = _period(conn, label)
    try:
        periods.assert_open(p, "mark ready to bill")
    except PeriodClosed as exc:
        raise _closed(exc) from exc

    customers = summaries.for_period(conn, p)
    ok, reason = summaries.can_mark_ready(customers)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Cannot mark ready to bill: {reason}")

    periods.set_status(conn, p["id"], periods.READY_TO_BILL, user.email)
    audit.record(
        conn, audit.PERIOD_READY, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"),
        previous_value=p["status"], new_value=periods.READY_TO_BILL,
    )
    return {"period": periods.public(_period(conn, label)), "totals": summaries.totals(customers)}


@router.post("/billing-periods/{label}/close")
def close_period(
    label: str,
    body: ClosePeriod,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    """Close a period. Irreversible without an explicit admin reopen."""
    p = _period(conn, label)
    if periods.is_closed(p):
        raise HTTPException(status_code=409, detail=f"{p['name']} is already closed.")
    # Typing the period label is the explicit confirmation. A boolean would be
    # trivially sent by a mis-aimed click; the label cannot be sent by accident.
    if body.confirm != p["label"]:
        raise HTTPException(
            status_code=400,
            detail=f"To close this period, confirm with its label: {p['label']}.",
        )

    # Freeze the summaries at the values being closed on, so a later price
    # change cannot rewrite what was billed.
    summaries.rebuild(conn, p["id"], actor=user.email)
    conn.execute(
        "UPDATE customer_period_summaries SET frozen = 1 WHERE period_id = ?", (p["id"],)
    )
    periods.set_status(conn, p["id"], periods.CLOSED, user.email, closed_at=periods.now_utc())
    audit.record(
        conn, audit.PERIOD_CLOSED, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"),
        previous_value=p["status"], new_value=periods.CLOSED, note=body.note,
    )
    return {"period": periods.public(_period(conn, label)), "frozen": True}


@router.post("/billing-periods/{label}/reopen")
def reopen_period(
    label: str,
    body: ReopenPeriod,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_admin),
):
    """Admin-only, and the only way a closed period ever changes again."""
    p = _period(conn, label)
    if not periods.is_closed(p):
        raise HTTPException(status_code=409, detail=f"{p['name']} is not closed.")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to reopen a period.")

    periods.set_status(conn, p["id"], periods.IN_REVIEW, user.email)
    conn.execute(
        "UPDATE customer_period_summaries SET frozen = 0 WHERE period_id = ?", (p["id"],)
    )
    audit.record(
        conn, audit.PERIOD_REOPENED, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"),
        previous_value=periods.CLOSED, new_value=periods.IN_REVIEW, note=body.reason,
    )
    return {"period": periods.public(_period(conn, label))}


# --------------------------------------------------------------- notifications
@router.post("/billing-periods/{label}/notify")
def resend_notification(
    label: str,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    """Deliberate resend. Logged as a resend, and not blocked by the once-only
    index that protects the automated send."""
    p = _period(conn, label)
    result = slack.send_review_notification(
        conn, p["id"], actor=user.email, kind="review_resend"
    )
    audit.record(
        conn, audit.NOTIFICATION_RESENT, user.email, period_id=p["id"],
        billing_type=p.get("billing_type"), new_value=result.get("status"),
        note=result.get("error"),
    )
    return result


@router.get("/billing-periods/{label}/notification-preview")
def preview_notification(label: str, conn: Conn = Depends(get_conn)):
    """Exactly what would be sent, without sending it."""
    p = _period(conn, label)
    return {
        "mode": slack.effective_mode(),
        "config": slack.config_status(),
        "message": slack.compose(conn, p["id"]),
        "already_sent": slack.already_sent(conn, p["id"]),
    }


# ------------------------------------------------------ summary by customer
@router.get("/customer-summary")
def customer_summary(
    period: str | None = None,
    good_to_bill: bool | None = None,
    review_status: str = "",
    pricing_status: str = "",
    exception_status: str = "",
    billing_customer: str = "",
    csm: str = "",
    conn: Conn = Depends(get_conn),
):
    p = _period(conn, period)
    out = summaries.for_period(conn, p)

    if good_to_bill is not None:
        out = [r for r in out if r["good_to_bill"] is good_to_bill]
    if review_status:
        out = [r for r in out if r["review_status"] == review_status]
    if pricing_status:
        out = [r for r in out if r["pricing_status"] == pricing_status]
    if exception_status:
        out = (
            [r for r in out if r["blocking_exceptions"]]
            if exception_status == "ANY"
            else [r for r in out if r["exception_status"] == exception_status]
        )
    if billing_customer:
        out = [r for r in out if r["billing_customer"] == billing_customer]
    if csm:
        out = [r for r in out if (r["csm"] or "") == csm]

    return {
        "period": periods.public(p),
        "rows": out,
        # Totals are of the *filtered* set so the footer matches what is shown.
        "totals": summaries.totals(out),
        "csms": sorted({r["csm"] for r in summaries.for_period(conn, p) if r["csm"]}),
    }


@router.get("/accounting")
def accounting(period: str | None = None, conn: Conn = Depends(get_conn)):
    p = _period(conn, period)
    customers = summaries.for_period(conn, p)
    ready, reason = summaries.can_mark_ready(customers)
    source = get_source()
    return {
        "period": periods.public(p),
        "totals": summaries.totals(customers),
        "can_mark_ready": ready,
        "ready_blocked_reason": reason,
        "latest_run": monthly.latest_run(conn, p["id"]),
        "runs": monthly.run_log(conn, p["id"], limit=10),
        "notifications": slack.history(conn, p["id"]),
        "slack": slack.config_status(),
        "usage_source": {
            "name": source.name,
            "default": DEFAULT_SOURCE,
            "available": source.available(),
            "describes": source.describe(),
        },
    }


# --------------------------------------------------------------- Good to Bill
@router.put("/approvals")
def set_approval(
    body: ApprovalUpsert,
    conn: Conn = Depends(get_conn),
    user: auth.User = Depends(auth.require_user),
):
    """Check or clear Good to Bill for one customer in one month."""
    p = _period(conn, body.period)
    try:
        periods.assert_open(p, "change a Good to Bill approval")
    except PeriodClosed as exc:
        raise _closed(exc) from exc

    match = next(
        (
            r for r in summaries.for_period(conn, p)
            if r["billing_customer"] == body.billing_customer
            and r["salesforce_account_id"] == (body.salesforce_account_id or "")
        ),
        None,
    )
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"{body.billing_customer} has no billable activity in {p['name']}.",
        )
    # Re-checked server-side. The UI disables the box, but the rule has to hold
    # for anything calling the API directly.
    if body.good_to_bill and not match["good_to_bill_eligible"]:
        raise HTTPException(status_code=409, detail=match["good_to_bill_blocked_reason"])

    now = periods.now_utc()
    existing = one(
        conn,
        "SELECT * FROM customer_period_approvals WHERE period_id = ? "
        "AND billing_customer = ? AND salesforce_account_id = ?",
        (p["id"], body.billing_customer, body.salesforce_account_id or ""),
    )
    # The approval and its audit row commit together: an approval that exists
    # with nobody recorded as having made it is worse than no approval.
    with tx(conn):
        conn.execute(
            """INSERT INTO customer_period_approvals
                 (period_id, billing_customer, salesforce_account_id, good_to_bill,
                  approved_by, approved_at, revoked_by, revoked_at, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (period_id, billing_customer, salesforce_account_id) DO UPDATE SET
                 good_to_bill = EXCLUDED.good_to_bill,
                 approved_by  = EXCLUDED.approved_by,
                 approved_at  = EXCLUDED.approved_at,
                 revoked_by   = EXCLUDED.revoked_by,
                 revoked_at   = EXCLUDED.revoked_at,
                 note         = EXCLUDED.note""",
            (
                p["id"], body.billing_customer, body.salesforce_account_id or "",
                1 if body.good_to_bill else 0,
                # A revoke keeps who originally approved it — that is the fact
                # the audit trail is about, and overwriting it would erase it.
                user.email if body.good_to_bill else (existing or {}).get("approved_by"),
                now if body.good_to_bill else (existing or {}).get("approved_at"),
                None if body.good_to_bill else user.email,
                None if body.good_to_bill else now,
                body.note or "",
            ),
        )
        audit.record(
            conn,
            audit.GOOD_TO_BILL_SET if body.good_to_bill else audit.GOOD_TO_BILL_REVOKED,
            user.email,
            period_id=p["id"], billing_type=p.get("billing_type"),
            customer=body.billing_customer, account_id=body.salesforce_account_id or "",
            entity="customer_period_approvals",
            previous_value=bool((existing or {}).get("good_to_bill")),
            new_value=body.good_to_bill, note=body.note,
        )
    return {
        "period": p["label"],
        "billing_customer": body.billing_customer,
        "good_to_bill": body.good_to_bill,
        "approved_by": user.email if body.good_to_bill else None,
        "approved_at": now if body.good_to_bill else None,
    }


@router.get("/approvals")
def list_approvals(period: str | None = None, conn: Conn = Depends(get_conn)):
    p = _period(conn, period)
    return rows(
        conn,
        "SELECT * FROM customer_period_approvals WHERE period_id = ? ORDER BY billing_customer",
        (p["id"],),
    )


# ------------------------------------------------------------------ audit trail
@router.get("/audit")
def audit_trail(
    period: str | None = None,
    action: str = "",
    customer: str = "",
    limit: int = Query(200, ge=1, le=1000),
    conn: Conn = Depends(get_conn),
):
    period_id = None
    if period:
        period_id = _period(conn, period)["id"]
    return audit.trail(conn, period_id=period_id, action=action, customer=customer, limit=limit)


@router.get("/usage-events")
def usage_events(
    period: str | None = None,
    qualification: str = "",
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    conn: Conn = Depends(get_conn),
):
    """The persisted usage layer, including rows that stopped qualifying."""
    p = _period(conn, period)
    where = ["period_id = ?"]
    params: list = [p["id"]]
    if qualification:
        where.append("qualification_status = ?")
        params.append(qualification)
    params.extend([limit, offset])
    return {
        "period": periods.public(p),
        "rows": rows(
            conn,
            f"SELECT * FROM usage_events WHERE {' AND '.join(where)} "
            f"ORDER BY billing_customer, sent_date, packet_id LIMIT ? OFFSET ?",
            params,
        ),
    }
