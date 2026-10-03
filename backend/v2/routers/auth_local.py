from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import local_login
from ._common import ERRORS

router = APIRouter(prefix="/api/v2/auth", tags=["auth"], responses=ERRORS)


class LocalLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


@router.get("/config", summary="Which sign-in methods this server offers (no secrets)")
def config():
    return {"local_login": local_login.available()}


@router.post("/local-login", summary="LOCAL development sign-in for the seeded demo people; disabled unless the operator enables it on a local database")
def local_sign_in(body: LocalLogin, request: Request):
    """Returns a short-lived bearer token for the same people the Supabase sign-in would authenticate. Off by default; never available for a hosted target."""
    return local_login.login(body.email, body.password, request.client.host if request.client else "unknown")
