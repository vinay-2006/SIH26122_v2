"""SIH v2 API (separate FastAPI app; the legacy app in backend/main.py is untouched).
Run:  DB_V2_URL=postgresql://postgres@127.0.0.1:54329/setuai_v2_integ  uvicorn backend.v2.app:app --port 8020"""
from __future__ import annotations

from contextlib import asynccontextmanager

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import errors
from . import jwt_verify
from .db import close_pool, database_url, get_pool, tx
from .routers import auth_local, claims, dashboard, documents, issues, projects, schedules


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        jwt_verify.get_verifier()                                   # fail fast at startup: no verification key / hosted without an issuer
        get_pool()                                                  # ... and: unsafe target / wrong fingerprint / unreachable
        yield
        close_pool()

    app = FastAPI(
        title="SetuAI v2 API", version="0.4.0", lifespan=lifespan,
        description="Project and schedule management (Project Manager), progress claims and evidence (Site Engineer), review and approval (Supervisor), "
                    "ledger-derived progress dashboards. Authenticate with a Supabase-style bearer JWT; project authority comes from an ACTIVE membership. "
                    "Errors are {error: {code, message, details}}. List endpoints return {items, limit, offset, next_offset}.")
    errors.install(app)
    origins = [o.strip() for o in os.environ.get("V2_CORS_ORIGINS", "").split(",") if o.strip()]       # explicit origins only; none by default
    if "*" in origins:
        raise RuntimeError("V2_CORS_ORIGINS must list explicit origins, not '*'")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key"], expose_headers=["Idempotent-Replay", "Content-Disposition"], max_age=600)
    app.include_router(auth_local.router)
    app.include_router(projects.router)
    app.include_router(schedules.router)
    app.include_router(documents.router)
    app.include_router(claims.router)
    app.include_router(issues.router)
    app.include_router(dashboard.router)

    @app.get("/health")
    def health():
        database_url()                                             # refuses an unsafe target
        with tx() as c:
            env = {r["key"]: r["value"] for r in c.execute("select key, value from public._setuai_env").fetchall()}
            n = c.execute("select count(*) n from public.schema_migrations").fetchone()["n"]
            db = c.execute("select current_database() d").fetchone()["d"]
        return {"status": "ok", "database": db, "schema": env.get("schema"), "env": env.get("env"), "migrations": n}
    return app


app = create_app()
