"""Billing period lifecycle: month arithmetic, status transitions, immutability.

A billing period is identified by (billing_type, year, month). Ad Hoc Paperwork
is the first billing type; the column exists so Worker Onboarding or H-2A Filing
can be added later without a second periods table, an alternative period id, or
a copy of this lifecycle.

The one rule everything else in this module exists to serve: **a CLOSED period
does not change.** Automated refreshes skip it, price confirmations do not reach
back into it, and usage reruns refuse it. Reopening is a deliberate, audited,
admin-only act — never a side effect of some other operation.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Any

from .db import Conn, one, rows

BILLING_TYPE_ADHOC = "ADHOC_PAPERWORK"

# Human labels for the billing types this framework can carry. Only the first is
# implemented; the rest are here so the enum has one home when they arrive.
BILLING_TYPES = {
    BILLING_TYPE_ADHOC: "Ad Hoc Paperwork",
}

PROCESSING = "PROCESSING"
IN_REVIEW = "IN_REVIEW"
READY_TO_BILL = "READY_TO_BILL"
CLOSED = "CLOSED"
FAILED = "FAILED"

STATUSES = (PROCESSING, IN_REVIEW, READY_TO_BILL, CLOSED, FAILED)

# Statuses a period can be worked on in. Everything else is read-only to the
# normal workflow.
OPEN_STATUSES = (PROCESSING, IN_REVIEW, READY_TO_BILL, FAILED)


class PeriodClosed(RuntimeError):
    """An attempt to modify a CLOSED period.

    Surfaced as 409 rather than 403: the caller is not unauthorised, the period
    is in a state that does not accept the write. The message names the period
    and the way out, because "forbidden" alone tells whoever is looking at the
    dashboard nothing about what to do next.
    """


class PeriodNotFound(KeyError):
    pass


def now_utc() -> str:
    """Matches the 'YYYY-MM-DD HH24:MI:SS' TEXT convention the schema uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------- month arithmetic
def prior_month(today: date | None = None) -> tuple[int, int]:
    """The calendar month before `today`. Run on 2 Aug 2026 -> (2026, 7).

    Taken from a date rather than 'subtract 30 days' so it is correct in
    February and on the 31st, and so a job that retries at 00:05 on the 3rd
    still targets the same month as its first attempt on the 2nd.
    """
    d = today or datetime.now(timezone.utc).date()
    return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)


def month_bounds(year: int, month: int) -> tuple[str, str]:
    """Inclusive first and last day, as ISO dates."""
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def month_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def month_name(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def parse_label(label: str) -> tuple[int, int]:
    """'2026-07' -> (2026, 7). Raises ValueError on anything else."""
    try:
        year_s, month_s = label.split("-")
        year, month = int(year_s), int(month_s)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Expected a period like '2026-07', got {label!r}") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"Month out of range in {label!r}")
    return year, month


# --------------------------------------------------------------- period access
def get_period(
    conn: Conn, year: int, month: int, billing_type: str = BILLING_TYPE_ADHOC
) -> dict | None:
    return one(
        conn,
        "SELECT * FROM periods WHERE billing_type = ? AND year = ? AND month = ?",
        (billing_type, year, month),
    )


def get_or_create_period(
    conn: Conn,
    year: int,
    month: int,
    billing_type: str = BILLING_TYPE_ADHOC,
    created_by: str = "system",
    run_source: str = "manual",
) -> tuple[dict, bool]:
    """Locate the period, or create it. Returns (period, created).

    ON CONFLICT DO NOTHING against the (billing_type, year, month) unique index
    rather than SELECT-then-INSERT: the scheduled job and a manual run can hit
    this at the same moment, and a check-then-act would let both create a row
    for the same month.

    A newly created period starts PROCESSING because the only reason to create
    one is that a run is about to populate it.
    """
    start, end = month_bounds(year, month)
    label = month_label(year, month)

    # RETURNING tells us whether this call did the insert: ON CONFLICT DO
    # NOTHING yields no row when the period already existed. Comparing
    # created_at to updated_at would not work — one is set by the database
    # clock and the other by the application's, so they rarely match even on a
    # fresh row.
    cur = conn.execute(
        """INSERT INTO periods
             (label, name, basis, billing_type, year, month, period_start,
              period_end, status, created_by, run_source, updated_at)
           VALUES (?, ?, 'sent_date', ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (billing_type, year, month) DO NOTHING
           RETURNING id""",
        (
            label, month_name(year, month), billing_type, year, month,
            start, end, PROCESSING, created_by, run_source, now_utc(),
        ),
    )
    created = cur.fetchone() is not None

    period = get_period(conn, year, month, billing_type)
    if period is None:  # pragma: no cover - the insert above guarantees a row
        raise PeriodNotFound(f"Could not create period {label}")
    return period, created


def list_periods(conn: Conn, billing_type: str | None = None) -> list[dict]:
    if billing_type:
        return rows(
            conn,
            "SELECT * FROM periods WHERE billing_type = ? ORDER BY label DESC",
            (billing_type,),
        )
    return rows(conn, "SELECT * FROM periods ORDER BY label DESC")


# ------------------------------------------------------------------ immutability
def is_closed(period: dict) -> bool:
    return period.get("status") == CLOSED or bool(period.get("closed"))


def assert_open(period: dict, action: str = "modify") -> None:
    """Gate every mutating path through here.

    Called at the service boundary rather than inside each SQL helper, so the
    refusal happens before any partial work — a run that is going to be rejected
    should not first spend a minute pulling usage from the warehouse.
    """
    if is_closed(period):
        raise PeriodClosed(
            f"{period['name']} is closed and cannot be changed "
            f"(attempted: {action}). Closed periods are the billed record. "
            f"An administrator can reopen it explicitly if it genuinely needs "
            f"to change."
        )


def set_status(
    conn: Conn,
    period_id: int,
    status: str,
    actor: str = "system",
    closed_at: str | None = None,
) -> None:
    """Move a period's status, keeping the legacy `closed` column in step."""
    if status not in STATUSES:
        raise ValueError(f"Unknown period status {status!r}")
    conn.execute(
        "UPDATE periods SET status = ?, closed = ?, closed_at = ?, "
        "updated_at = ?, run_source = COALESCE(run_source, ?) WHERE id = ?",
        (
            status,
            1 if status == CLOSED else 0,
            closed_at if status == CLOSED else None,
            now_utc(),
            actor,
            period_id,
        ),
    )


def public(period: dict) -> dict[str, Any]:
    """The period as the frontend consumes it."""
    return {
        "id": period["id"],
        "label": period["label"],
        "name": period["name"],
        "billing_type": period.get("billing_type", BILLING_TYPE_ADHOC),
        "billing_type_label": BILLING_TYPES.get(
            period.get("billing_type", BILLING_TYPE_ADHOC),
            period.get("billing_type", ""),
        ),
        "year": period.get("year"),
        "month": period.get("month"),
        "period_start": period.get("period_start"),
        "period_end": period.get("period_end"),
        "basis": period.get("basis", "sent_date"),
        "status": period.get("status", IN_REVIEW),
        "closed": is_closed(period),
        "read_only": is_closed(period),
        "created_at": period.get("created_at"),
        "updated_at": period.get("updated_at"),
        "closed_at": period.get("closed_at"),
        "created_by": period.get("created_by"),
        "run_source": period.get("run_source"),
    }
