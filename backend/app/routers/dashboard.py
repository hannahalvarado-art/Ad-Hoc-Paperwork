from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import reporting
from ..db import get_conn, resolve_period, rows

router = APIRouter(prefix="/api", tags=["dashboard"])


def _period(conn: sqlite3.Connection, label: str | None) -> dict:
    try:
        return resolve_period(conn, label)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/periods")
def periods(conn: sqlite3.Connection = Depends(get_conn)):
    return rows(conn, "SELECT * FROM periods ORDER BY label DESC")


@router.get("/kpis")
def kpis(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    p = _period(conn, period)
    return {"period": p, **reporting.kpis(conn, p["id"])}


@router.get("/summary")
def summary(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    p = _period(conn, period)
    return reporting.summary(conn, p["id"])


@router.get("/excluded")
def excluded(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    p = _period(conn, period)
    return reporting.excluded(conn, p["id"])


@router.get("/events")
def events(
    period: str | None = None,
    search: str = "",
    billing_customer: str = "",
    flag: str = "",
    sort: str = "source_customer",
    direction: str = "asc",
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    p = _period(conn, period)
    return reporting.event_list(
        conn,
        p["id"],
        search=search,
        billing_customer=billing_customer,
        flag=flag,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
    )


@router.get("/billing-customers")
def billing_customers(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    p = _period(conn, period)
    return reporting.billing_customers(conn, p["id"])


@router.get("/runs")
def runs(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    p = _period(conn, period)
    return rows(
        conn,
        "SELECT * FROM pipeline_runs WHERE period_id = ? ORDER BY id DESC LIMIT 20",
        (p["id"],),
    )
