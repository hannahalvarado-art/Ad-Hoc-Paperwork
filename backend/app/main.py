from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse

from .db import DATABASE_URL, DatabaseUnavailable, connect, init_db, scalar
from .routers import comparison, config, dashboard, overrides, pipeline

ORIGINS = os.environ.get(
    "ADHOC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")


# Whatever went wrong applying the schema, kept so /api/health can report it.
# Printing alone was not enough: a failed migration left the app serving 500s
# from a connected-but-empty database, and the only evidence was a log line.
MIGRATION_ERROR: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Non-fatal: a cold start that cannot reach Postgres should still boot so
    # /api/health can explain why, rather than the whole function 500-ing with
    # no diagnostic. Set ADHOC_SKIP_MIGRATE=1 once the schema is applied to
    # drop the per-cold-start round trip.
    global MIGRATION_ERROR
    if not os.environ.get("ADHOC_SKIP_MIGRATE"):
        try:
            init_db()
            MIGRATION_ERROR = None
        except Exception as exc:
            MIGRATION_ERROR = str(exc)
            print(f"[startup] schema init FAILED: {exc}")
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

@app.exception_handler(DatabaseUnavailable)
def _db_unavailable(request, exc: DatabaseUnavailable):
    """503 with the reason, not 500 with nothing.

    `detail` is the key the frontend's api.js already reads off an error body,
    so this text reaches the dashboard banner unchanged.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": f"Database unavailable. {exc}"},
    )


for r in (dashboard.router, overrides.router, config.router, pipeline.router, comparison.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    """Connectivity *and* readiness.

    Reporting only "connected" was misleading: a reachable database with no
    schema said ok while every other endpoint returned 500. Health now
    distinguishes unconfigured / unreachable / no schema / no data / ready, so
    whatever is actually wrong is named here rather than inferred from logs.
    """
    if not DATABASE_URL:
        return {"status": "error", "db": "unconfigured", "detail": "DATABASE_URL is not set"}
    try:
        with connect() as conn:
            tables = scalar(
                conn,
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'",
            )
            if not tables:
                return {
                    "status": "error",
                    "db": "no schema",
                    "tables": 0,
                    "migration_error": MIGRATION_ERROR,
                    "detail": "Connected, but no tables. The startup migration did not apply.",
                }
            periods = scalar(conn, "SELECT COUNT(*) FROM periods")
            if not periods:
                return {
                    "status": "empty",
                    "db": "connected",
                    "tables": tables,
                    "periods": 0,
                    "detail": "Schema is in place but no data has been loaded. Run seed.py.",
                }
            return {"status": "ok", "db": "connected", "tables": tables, "periods": periods}
    except Exception as exc:  # surfaced as JSON, not a stack trace
        return {"status": "error", "db": "unreachable", "detail": str(exc)}
