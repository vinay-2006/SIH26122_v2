"""LOCAL development sign-in for the v2 API, so a browser can obtain a token without any secret in the frontend.

It is OFF unless the operator sets V2_LOCAL_LOGIN_PASSWORD (12+ characters) when starting the API, and it only ever works when ALL of these hold:
  * the database target is LOCAL (db/target_guard kind 'local': loopback host, setuai_v2_* database) - a hosted target disables it for good;
  * a shared HS256 secret is configured (the token is signed with the same secret the API verifies with);
  * the email belongs to the local demo domain (V2_LOCAL_LOGIN_DOMAIN, default seed.setuai.local) and to an active profile.
The password is compared in constant time; failures are throttled per email and per client address. The token is short-lived (8 hours) and is verified by
the ordinary token verifier like any other: this endpoint adds no privilege, it only mints the same token a Supabase sign-in would."""
from __future__ import annotations

import datetime as dt
import hmac
import os
import threading
import time
from typing import Dict, List

import jwt

from db import target_guard as tg

from . import jwt_verify
from .db import database_url, tx
from .errors import ApiError

MIN_PASSWORD = 12
TOKEN_HOURS = 8
MAX_FAILURES, WINDOW_S = 6, 300
_fail: Dict[str, List[float]] = {}
_lock = threading.Lock()


def _enabled_password() -> str:
    pw = os.environ.get("V2_LOCAL_LOGIN_PASSWORD", "")
    if len(pw) < MIN_PASSWORD:
        raise ApiError(404, "LOCAL_LOGIN_DISABLED", "Local sign-in is not enabled on this server")
    try:
        if tg.check_target(database_url()).kind != "local":
            raise ApiError(404, "LOCAL_LOGIN_DISABLED", "Local sign-in is not enabled on this server")
    except (RuntimeError, tg.GuardError):
        raise ApiError(404, "LOCAL_LOGIN_DISABLED", "Local sign-in is not enabled on this server")
    return pw


def _throttle(keys: List[str]) -> None:
    now = time.monotonic()
    with _lock:
        for k in keys:
            recent = [t for t in _fail.get(k, []) if now - t < WINDOW_S]
            _fail[k] = recent
            if len(recent) >= MAX_FAILURES:
                raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many failed sign-in attempts; wait a few minutes and try again")


def _record_failure(keys: List[str]) -> None:
    now = time.monotonic()
    with _lock:
        for k in keys:
            _fail.setdefault(k, []).append(now)


def reset_throttle() -> None:
    with _lock:
        _fail.clear()


def available() -> bool:
    try:
        _enabled_password()
        return jwt_verify.get_verifier().cfg.hs256_secret is not None
    except ApiError:
        return False


def login(email: str, password: str, client: str) -> Dict[str, object]:
    expected = _enabled_password()
    cfg = jwt_verify.get_verifier().cfg
    if not cfg.hs256_secret:
        raise ApiError(404, "LOCAL_LOGIN_DISABLED", "Local sign-in is not enabled on this server")
    em = (email or "").strip().lower()
    keys = [f"e:{em}", f"c:{client}"]
    _throttle(keys)
    domain = os.environ.get("V2_LOCAL_LOGIN_DOMAIN", "seed.setuai.local").lower()
    ok_password = hmac.compare_digest((password or "").encode(), expected.encode())
    row = None
    if em.endswith("@" + domain):
        with tx(readonly=True) as c:
            row = c.execute("select id, email from profiles where lower(email) = %s and is_active", (em,)).fetchone()
    if not (ok_password and row):                              # one answer for "unknown person" and "wrong password"
        _record_failure(keys)
        raise ApiError(401, "INVALID_CREDENTIALS", "Incorrect email or password")
    now = dt.datetime.now(dt.timezone.utc)
    claims = {"sub": str(row["id"]), "email": row["email"], "aud": cfg.audience or "authenticated", "role": "authenticated", "iat": now, "exp": now + dt.timedelta(hours=TOKEN_HOURS)}
    if cfg.issuer:
        claims["iss"] = cfg.issuer
    return {"access_token": jwt.encode(claims, cfg.hs256_secret, algorithm="HS256"), "token_type": "bearer", "expires_in": TOKEN_HOURS * 3600}
