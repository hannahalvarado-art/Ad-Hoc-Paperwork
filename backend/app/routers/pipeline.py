from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..db import Conn, get_conn, one, resolve_period, tx
from ..models import ContractLookupIngest, PeriodCreate, RawIngest
from ..pipeline import runner

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.post("/periods")
def create_period(body: PeriodCreate, conn: Conn = Depends(get_conn)):
    with tx(conn):
        conn.execute(
            "INSERT INTO periods (label, name, basis) VALUES (?, ?, ?) "
            "ON CONFLICT (label) DO NOTHING",
            (body.label, body.name, body.basis),
        )
    return one(conn, "SELECT * FROM periods WHERE label = ?", (body.label,))


@router.post("/ingest/raw")
def ingest_raw(body: RawIngest, conn: Conn = Depends(get_conn)):
    """Load june_adhoc_v2.json-shaped records for a period."""
    try:
        p = resolve_period(conn, body.period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    n = runner.ingest_raw(conn, p["id"], body.records, replace=body.replace)
    return {"period": p["label"], "ingested": n, "next": "POST /api/pipeline/run"}


@router.post("/ingest/raw-file")
async def ingest_raw_file(
    period: str,
    file: UploadFile = File(...),
    conn: Conn = Depends(get_conn),
):
    try:
        p = resolve_period(conn, period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        records = json.loads((await file.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"That file isn't valid JSON: {exc}") from exc
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Expected a JSON array of event records.")
    n = runner.ingest_raw(conn, p["id"], records)
    return {"period": p["label"], "ingested": n}


@router.post("/ingest/contract-lookup")
async def ingest_contract_lookup(
    body: ContractLookupIngest | None = None,
    file: UploadFile | None = File(None),
    conn: Conn = Depends(get_conn),
):
    """contract_lookup.csv: packet_id, contract_names."""
    pairs: list[tuple[str, str]] = []

    if file is not None:
        text = (await file.read()).decode("utf-8-sig")
    elif body and body.csv_text:
        text = body.csv_text
    elif body and body.pairs:
        pairs = [(str(a), str(b)) for a, b in body.pairs]
        text = ""
    else:
        raise HTTPException(status_code=400, detail="Provide a CSV file, csv_text, or pairs.")

    if text:
        reader = csv.reader(io.StringIO(text))
        next(reader, None)  # header
        for row in reader:
            if len(row) >= 2 and row[0].strip():
                pairs.append((row[0].strip(), row[1].strip()))

    n = runner.ingest_contract_lookup(conn, pairs)
    return {"loaded": n}


@router.post("/pipeline/run")
def run(period: str | None = None, conn: Conn = Depends(get_conn)):
    """Stage 1 -> 2 -> 3, atomically, replacing the period's events."""
    try:
        p = resolve_period(conn, period)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return {"period": p["label"], **runner.run_pipeline(conn, p["id"])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
