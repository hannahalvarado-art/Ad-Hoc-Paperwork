from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import DB_PATH, init_db
from .routers import comparison, config, dashboard, overrides, pipeline

ORIGINS = os.environ.get(
    "ADHOC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Ad Hoc Paperwork billing reconciliation",
    version="1.0.0",
    description=(
        "Read-only validation. No invoices are issued and Salesforce is never "
        "modified: confirmed prices live in a separate approved-override layer."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ORIGINS if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (dashboard.router, overrides.router, config.router, pipeline.router, comparison.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists()}
