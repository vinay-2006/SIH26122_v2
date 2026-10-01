import os
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Must run before any backend module import below, since shared/db.py and
# others read DATABASE_URL/SUPABASE_*/LLM_* from the environment at import
# time (module-level os.getenv(...) calls). Loading .env after those
# imports would leave them permanently unset for the life of the process.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import (
    # Existing V6/V7 routers
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

    # Operations Intelligence
    contractors,
    work_packages,
    quality,

    # Execution Intelligence / V7
    stages,
    reopen,
    progress,
    impact,
    memory,
    dossier,
    agent,
    issues,
    notifications,
    project_dashboard,
    batches,
)

from backend.shared.db import init_db


def _warm_active_index() -> None:
    """
    Rebuild the in-memory FAISS index for the active schedule and load the
    embedding model off the request path.

    The index does not survive a restart, so warming it during startup avoids
    making the first /match request pay the model/index initialization cost.

    Startup must never fail because FAISS/model warm-up failed.
    """
    try:
        from backend.shared import schedule_index
        from backend.shared.schedule_repository import get_active_schedule

        active = get_active_schedule()

        if active is not None:
            schedule_index.build_index(active.schedule_id)
            print(
                f"FAISS index warmed for active schedule "
                f"{active.schedule_id}"
            )

    except Exception as e:
        # Never block application startup because index warm-up failed.
        print(f"Warning: FAISS warm-up skipped ({e})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    Performs lightweight database initialization and starts FAISS warm-up
    asynchronously so application startup is not blocked.
    """
    try:
        init_db()
    except Exception as e:
        print(
            f"Warning: Database initialization skipped on startup ({e})"
        )

    threading.Thread(
        target=_warm_active_index,
        daemon=True,
    ).start()

    yield


app = FastAPI(
    title="SIH26122 Infrastructure Progress Tracking API",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Application dependencies
# ---------------------------------------------------------------------------

from fastapi.responses import JSONResponse

from backend.shared.config import settings
from backend.shared.db import get_connection
from backend.shared.rule_extraction import fallback_enabled


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Root / Health
# ---------------------------------------------------------------------------

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
    return {
        "status": "ok",
    }


@app.get("/health/db")
def health_db():
    """
    Isolated infrastructure connectivity check executing SELECT 1 against
    PostgreSQL.

    Returns:
        200 when the database is reachable.
        503 when the database connection fails.

    Credentials and connection details are never exposed.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()

        return {
            "database": "healthy",
        }

    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "database": "unhealthy",
                "status": "connection_failed",
            },
        )


@app.get("/health/ai")
def health_ai():
    """
    Isolated AI provider configuration check.

    This endpoint does not perform a network LLM call and never exposes
    provider credentials.
    """
    return {
        "provider": settings.LLM_PROVIDER,
        "configured": settings.is_llm_configured(),
        "fallback_available": True,
        "deterministic_fallback_enabled": fallback_enabled(),
    }


# ===========================================================================
# Existing V6/V7 routers
# ===========================================================================

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


# ===========================================================================
# Operations Intelligence
# ===========================================================================
#
# Owned/introduced by the operations-intelligence branch.
#
# These routers cover:
#   - Contractors
#   - Work Packages
#   - Quality / ITP / Hold Points
#
# They are intentionally retained alongside the execution-intelligence
# routers below. They are independent router registrations.
# ===========================================================================

app.include_router(contractors.router)
app.include_router(work_packages.router)
app.include_router(quality.router)


# ===========================================================================
# Execution Intelligence / V7
# ===========================================================================
#
# These routers cover:
#   - Stages / execution state
#   - Reopen / rework
#   - Weighted progress
#   - Compound impact
#   - Institutional memory
#   - Audit dossier
#   - Supervising agent
# ===========================================================================

app.include_router(stages.router)
app.include_router(reopen.router)
app.include_router(progress.router)
app.include_router(impact.router)
app.include_router(memory.router)
app.include_router(dossier.router)
app.include_router(agent.router)
app.include_router(issues.router)
app.include_router(notifications.router)
app.include_router(project_dashboard.router)
app.include_router(batches.router)