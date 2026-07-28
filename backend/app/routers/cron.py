"""The scheduled monthly job.

NOT ENABLED. `vercel.json` carries no `crons` entry, so nothing calls this on a
schedule; the route exists so the wiring can be tested and so enabling it later
is a configuration change rather than new code. See README for the entry to add
once July has been validated by hand.

Authorisation is a shared secret, not a session. Vercel Cron sends
`Authorization: Bearer $CRON_SECRET`, and there is no user to authenticate. If
CRON_SECRET is unset the endpoint refuses everything — an unauthenticated route
that reruns billing and messages @csms is not something to leave open by
default.
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import periods
from ..db import Conn, get_conn
from ..periods import PeriodClosed
from ..services import monthly

router = APIRouter(prefix="/api/cron", tags=["cron"])

CRON_SECRET = os.environ.get("CRON_SECRET", "")
CRON_ENABLED = os.environ.get("ADHOC_CRON_ENABLED") == "1"


def _authorize(authorization: str | None) -> None:
    if not CRON_SECRET:
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET is not set, so the scheduled endpoint is disabled.",
        )
    expected = f"Bearer {CRON_SECRET}"
    # compare_digest so a wrong secret cannot be discovered by timing.
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Bad or missing cron credentials.")


@router.post("/monthly")
def monthly_job(
    year: int | None = None,
    month: int | None = None,
    authorization: str | None = Header(default=None),
    conn: Conn = Depends(get_conn),
):
    """Process the prior calendar month.

    Idempotent by construction: it resolves to the same period, merges usage
    rather than appending it, and the once-only notification index means a
    retry after a partial failure cannot notify @csms twice.
    """
    _authorize(authorization)
    if not CRON_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "The scheduled monthly job is disabled in this environment. "
                "Set ADHOC_CRON_ENABLED=1 once the workflow has been validated "
                "by hand."
            ),
        )

    y, m = (year, month) if year and month else periods.prior_month()
    try:
        result = monthly.run_period(
            conn, y, m, actor="system:cron", run_type="scheduled", notify=True
        )
    except PeriodClosed as exc:
        # Not an error: a closed month is the correct place to stop.
        return {"status": "skipped", "reason": str(exc), "period": f"{y:04d}-{m:02d}"}
    except monthly.RunFailed as exc:
        # The period is marked FAILED and no notification was sent. 500 so the
        # platform records a failed invocation and retries visibly.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "ok",
        "period": result["period"]["label"],
        "events": result["events"],
        "notification": result.get("notification", {}).get("status"),
    }


@router.get("/status")
def cron_status():
    """Whether the schedule would run, without running it."""
    year, month = periods.prior_month()
    return {
        "enabled": CRON_ENABLED,
        "secret_configured": bool(CRON_SECRET),
        "would_process": f"{year:04d}-{month:02d}",
        "note": (
            "No `crons` entry exists in vercel.json, so nothing is scheduled "
            "regardless of these flags."
        ),
    }
