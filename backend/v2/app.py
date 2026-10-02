"""SIH v2 API (separate FastAPI app; the legacy app in backend/main.py is untouched).
Run:  DB_V2_URL=postgresql://postgres@127.0.0.1:54329/setuai_v2_integ  uvicorn backend.v2.app:app --port 8020"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import errors
from . import jwt_verify
from .db import close_pool, database_url, get_pool, tx
from .routers import projects, schedules


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        jwt_verify.get_verifier()                                   # fail fast at startup: no verification key / hosted without an issuer
        get_pool()                                                  # ... and: unsafe target / wrong fingerprint / unreachable
        yield
        close_pool()

    app = FastAPI(title="SIH v2 API", version="0.3.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(projects.router)
    app.include_router(schedules.router)

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
