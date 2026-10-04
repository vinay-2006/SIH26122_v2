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
from .routers import auth_local, claims, dashboard, documents, issues, knowledge, projects, schedules


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        jwt_verify.get_verifier()                                   # fail fast at startup: no verification key / hosted without an issuer
        get_pool()                                                  # ... and: unsafe target / wrong fingerprint / unreachable
        if os.environ.get("V2_WARM_MODEL", "1") == "1" and not os.environ.get("PYTEST_CURRENT_TEST"):
            import threading
            from .matching import index as _mi
            threading.Thread(target=_mi.warm_up, name="model-warm-up", daemon=True).start()
        yield
        close_pool()

    hosted = os.environ.get("V2_ALLOW_HOSTED") == "1"
    docs_off = {} if (not hosted or os.environ.get("V2_ENABLE_DOCS") == "1") else {"docs_url": None, "redoc_url": None, "openapi_url": None}     # a hosted API does not publish its route map
    app = FastAPI(
        title="ANVYRA API", version="0.4.0", lifespan=lifespan, **docs_off,
        description="ANVYRA — Where Every Detail Connects. AI-Powered Infrastructure Execution Intelligence. Project and schedule management (Project Manager), progress claims and evidence (Site Engineer), review and approval (Supervisor), "
                    "ledger-derived progress dashboards. Authenticate with a Supabase-style bearer JWT; project authority comes from an ACTIVE membership. "
                    "Errors are {error: {code, message, details}}. List endpoints return {items, limit, offset, next_offset}.")
    errors.install(app)
    from . import request_limit
    request_limit.install(app)                                       # added BEFORE CORS so that the refusal still carries the CORS headers
    origins = [o.strip() for o in os.environ.get("V2_CORS_ORIGINS", "").split(",") if o.strip()]       # explicit origins only; none by default
    if "*" in origins:
        raise RuntimeError("V2_CORS_ORIGINS must list explicit origins, not '*'")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Project-ID", "X-Schedule-ID"], expose_headers=["Idempotent-Replay", "Content-Disposition"], max_age=600)
    app.include_router(auth_local.router)
    app.include_router(projects.router)
    app.include_router(schedules.router)
    app.include_router(documents.router)
    app.include_router(claims.router)
    app.include_router(issues.router)
    app.include_router(dashboard.router)
    app.include_router(knowledge.router)
    from . import compat                                             # the original /api/v1 contract served on v2 data (documented in docs/V2_COMPAT.md, not in the v2 API reference)
    for r in compat.routers():
        app.include_router(r, include_in_schema=False)

    @app.get("/health")
    def health():
        database_url()                                             # refuses an unsafe target
        with tx() as c:
            env = {r["key"]: r["value"] for r in c.execute("select key, value from public._setuai_env").fetchall()}
            n = c.execute("select count(*) n from public.schema_migrations").fetchone()["n"]
            db = c.execute("select current_database() d").fetchone()["d"]
        if hosted:                                                 # unauthenticated: a hosted API says only that it is up (the database fingerprint was verified when the connection pool was created)
            return {"status": "ok"}
        return {"status": "ok", "database": db, "schema": env.get("schema"), "env": env.get("env"), "migrations": n}
    return app


app = create_app()
