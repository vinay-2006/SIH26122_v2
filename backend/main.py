import os
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Must run before any backend module import below, since shared/db.py and
# others read DATABASE_URL/SUPABASE_*/LLM_* from the environment at import
# time (module-level `os.getenv(...)` calls) — loading .env after those
# imports would leave them permanently unset for the life of the process.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import (
    schedules,
    intake,
    matching,
    checks,
    decisions,
    export,
    auth,
    dashboard,
    activities,
    schedule,
    mock_p6,
    graph,
    investigation,
    summary,
    reports,
    claim_graph,
    projects,
    stages,
    reopen,
    progress,
    impact,
)
from backend.shared.db import init_db


def _warm_active_index() -> None:
    """Rebuild the in-memory FAISS index for the active schedule and load the embedding
    model off the request path (the index does not survive a restart), so the first
    /match after startup is not slow."""
    try:
        from backend.shared import schedule_index
        from backend.shared.schedule_repository import get_active_schedule

        active = get_active_schedule()
        if active is not None:
            schedule_index.build_index(active.schedule_id)
            print(f"FAISS index warmed for active schedule {active.schedule_id}")
    except Exception as e:  # never block startup
        print(f"Warning: FAISS warm-up skipped ({e})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database initialization skipped on startup ({e})")
    threading.Thread(target=_warm_active_index, daemon=True).start()
    yield


app = FastAPI(
    title="SIH26122 Infrastructure Progress Tracking API",
    version="0.1.0",
    lifespan=lifespan,
)

from fastapi.responses import JSONResponse
from backend.shared.config import settings
from backend.shared.db import get_connection
from backend.shared.rule_extraction import fallback_enabled

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "status": "online",
        "app": "SIH26122 Infrastructure Progress Tracking API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "frontend": "http://127.0.0.1:5173",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    """
    Isolated infrastructure connectivity check executing SELECT 1 against PostgreSQL.
    Returns HTTP 200 with status healthy on success, or HTTP 503 without exposing credentials.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return {"database": "healthy"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"database": "unhealthy", "status": "connection_failed"},
        )


@app.get("/health/ai")
def health_ai():
    """
    Isolated AI provider configuration check.
    Safely reports provider status without performing network LLM calls or exposing keys.
    """
    return {
        "provider": settings.LLM_PROVIDER,
        "configured": settings.is_llm_configured(),
        "fallback_available": True,
        "deterministic_fallback_enabled": fallback_enabled(),
    }


app.include_router(schedules.router)
app.include_router(intake.router)
app.include_router(matching.router)
app.include_router(checks.router)
app.include_router(decisions.router)
app.include_router(export.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(activities.router)
app.include_router(schedule.router)
app.include_router(mock_p6.router)
app.include_router(graph.router)
app.include_router(investigation.router)
app.include_router(summary.router)
app.include_router(reports.router)
app.include_router(claim_graph.router)
app.include_router(projects.router)
app.include_router(stages.router)
app.include_router(reopen.router)
app.include_router(progress.router)
app.include_router(impact.router)
