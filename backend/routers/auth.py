import os
import time
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.shared.auth import UserProfile, get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=UserProfile)
def get_me(current_user: UserProfile = Depends(get_current_user)) -> UserProfile:
    """
    Return the profile of the currently authenticated user.
    Identity and role are derived exclusively from the verified token
    and profiles table lookup.
    """
    return current_user


class LocalLogin(BaseModel):
    email: str
    password: str


def _local_demo_auth_enabled() -> bool:
    """Local demo login exists ONLY against the isolated integration database, with an explicit opt-in.
    Same guard as the DB test-suite: opt-in flags, loopback host, never the shared Supabase DB."""
    from backend.testing import guard

    if os.getenv("SETUAI_LOCAL_DEMO_AUTH") != "1":
        return False
    root = Path(__file__).resolve().parents[2]
    return not guard.check_env(dict(os.environ), str(root / ".env")) and bool(os.getenv("SUPABASE_JWT_SECRET"))


@router.post("/local-login")
def local_login(payload: LocalLogin):
    """
    Issues a REAL HS256 JWT (verified by the same code path as a Supabase token) for an identity that exists in the
    isolated integration database. Disabled (404) in every other environment, so it cannot exist in a shared/prod DB.
    """
    if not _local_demo_auth_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    from backend.shared.db import get_connection

    email = payload.email.strip().lower()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM auth.users WHERE lower(email) = %s", (email,)).fetchone()
    expected = os.getenv("LOCAL_DEMO_PASSWORD", "Demo123456!")
    if row is None or payload.password != expected:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(row["id"]), "email": email, "aud": "authenticated", "role": "authenticated", "iat": now, "exp": now + 8 * 3600},
        os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 8 * 3600}
