from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from ..db import get_conn, one, resolve_period, rows, tx
from ..pipeline import hex_comparison
from ..reporting import decorate

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


@router.post("/run")
async def run_comparison(
    period: str | None = None,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Upload the Hex export and reconcile it against the current events."""
    try:
        p = resolve_period(conn, period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        text = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Upload the Hex export as UTF-8 CSV.") from exc

    try:
        hex_rows = hex_comparison.parse_hex_csv(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    events = [
        {**r, "charge": r["effective"]["charge"], "flag": r["effective"]["flag"]}
        for r in decorate(conn, p["id"])
    ]
    if not events:
        raise HTTPException(
            status_code=400,
            detail="No events for this period yet. Run the pipeline before comparing.",
        )

    result = hex_comparison.compare(events, hex_rows, p["label"])

    with tx(conn):
        cur = conn.execute(
            "INSERT INTO comparison_runs (period_id, summary, per_customer) VALUES (?, ?, ?)",
            (p["id"], json.dumps(result["summary"]), json.dumps(result["per_customer"])),
        )
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO comparison_records "
            "(run_id, category, sub, customer, worker, notes, claude_side, hex_side) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    r["category"],
                    r.get("sub", ""),
                    r["customer"],
                    r["worker"],
                    r.get("notes", ""),
                    json.dumps(r["claude"]) if r["claude"] else None,
                    json.dumps(r["hex"]) if r["hex"] else None,
                )
                for r in result["records"]
            ],
        )

    return {"run_id": run_id, "summary": result["summary"], "per_customer": result["per_customer"]}


@router.get("/latest")
def latest(period: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        p = resolve_period(conn, period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = one(
        conn,
        "SELECT * FROM comparison_runs WHERE period_id = ? ORDER BY id DESC LIMIT 1",
        (p["id"],),
    )
    if not run:
        return {"run": None, "summary": None, "per_customer": [], "categories": {}}

    cats = {
        r["category"]: r["n"]
        for r in rows(
            conn,
            "SELECT category, COUNT(*) AS n FROM comparison_records WHERE run_id = ? "
            "GROUP BY category",
            (run["id"],),
        )
    }
    return {
        "run": {"id": run["id"], "created_at": run["created_at"]},
        "summary": json.loads(run["summary"]),
        "per_customer": json.loads(run["per_customer"]),
        "categories": cats,
    }


@router.get("/records")
def records(
    run_id: int | None = None,
    period: str | None = None,
    category: str = "",
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if run_id is None:
        try:
            p = resolve_period(conn, period)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run_id = one(
            conn,
            "SELECT id FROM comparison_runs WHERE period_id = ? ORDER BY id DESC LIMIT 1",
            (p["id"],),
        )
        if not run_id:
            return {"rows": [], "matched": 0}
        run_id = run_id["id"]

    where = ["run_id = ?"]
    params: list = [run_id]
    if category:
        where.append("category = ?")
        params.append(category)

    matched = rows(
        conn, f"SELECT COUNT(*) AS n FROM comparison_records WHERE {' AND '.join(where)}", params
    )[0]["n"]
    page = rows(
        conn,
        f"SELECT * FROM comparison_records WHERE {' AND '.join(where)} "
        f"ORDER BY id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    for r in page:
        r["claude_side"] = json.loads(r["claude_side"]) if r["claude_side"] else None
        r["hex_side"] = json.loads(r["hex_side"]) if r["hex_side"] else None

    return {"rows": page, "matched": matched, "run_id": run_id}
