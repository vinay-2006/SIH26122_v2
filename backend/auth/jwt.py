"""
JWT verification utilities for Supabase Auth in SETUAI V7.
"""

from __future__ import annotations

import os
from typing import Optional

import jwt
from fastapi import HTTPException, status


_jwks_client: Optional[jwt.PyJWKClient] = None
_jwks_url: Optional[str] = None


def get_jwks_client(url: str) -> jwt.PyJWKClient:
    """
    Get or create a cached PyJWKClient instance for the specified JWKS URL.
    Caches the JWK set for 300 seconds to optimize verification latency.
    """
    global _jwks_client, _jwks_url

    if _jwks_client is None or _jwks_url != url:
        _jwks_client = jwt.PyJWKClient(
            url,
            cache_jwk_set=True,
            lifespan=300,
        )
        _jwks_url = url

    return _jwks_client


def decode_supabase_jwt(token: str) -> dict:
    """
    Verify and decode a Supabase-issued JWT token.
    Extracts the payload containing the user ID ('sub').

    Supports:
    1. Offline HMAC-SHA256 verification via SUPABASE_JWT_SECRET / JWT_SECRET.
    2. Offline asymmetric ES256/RS256 verification via Supabase JWKS (PyJWKClient).
    3. Online HTTP verification fallback via Supabase Auth API.
    4. Local development / offline test mode fallback (AUTH_DEV_MODE=true).
    """
    if not token or not isinstance(token, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: token must be a non-empty string",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = token.strip()

    # 1. Symmetric HMAC-SHA256 verification
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET")

    if jwt_secret and not jwt_secret.startswith(("http://", "https://")):
        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
                leeway=10,
            )

            if "sub" in payload:
                return payload

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject claim ('sub')",
                headers={"WWW-Authenticate": "Bearer"},
            )

        except HTTPException:
            raise
        except jwt.PyJWTError:
            # Token might use ES256/RS256 asymmetric signature (JWKS) or secret mismatch; fall through.
            pass

    # 2. Asymmetric verification via JWKS
    jwks_url = os.getenv("SUPABASE_JWKS_URL")
    supabase_url = os.getenv("SUPABASE_URL")

    if not jwks_url and supabase_url:
        jwks_url = (
            f"{supabase_url.rstrip('/')}"
            "/auth/v1/.well-known/jwks.json"
        )

    if jwks_url:
        try:
            client = get_jwks_client(jwks_url)
            signing_key = client.get_signing_key_from_jwt(token)

            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256", "RS256"],
                options={"verify_aud": False},
                leeway=10,
            )

            if "sub" in payload:
                return payload

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject claim ('sub')",
                headers={"WWW-Authenticate": "Bearer"},
            )

        except HTTPException:
            raise
        except jwt.PyJWKClientConnectionError:
            # Network error connecting to JWKS endpoint; fall through.
            pass
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception:
            # Other transient error; fall through.
            pass

    # 3. HTTP verification fallback against Supabase Auth endpoint
    supabase_key = (
        os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SECRET_KEY")
    )

    if supabase_url and supabase_key:
        try:
            import httpx

            resp = httpx.get(
                f"{supabase_url.rstrip('/')}/auth/v1/user",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {token}",
                },
                timeout=5.0,
            )

            if resp.status_code == 200:
                user_data = resp.json()
                return {
                    "sub": str(user_data.get("id")),
                    "email": user_data.get("email"),
                    "role": user_data.get("role"),
                }

            if resp.status_code in (400, 401, 403):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token validation failed: Supabase rejected the token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Token validation failed: Supabase returned {resp.status_code}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token validation failed: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 4. Local development / offline test mode fallback
    auth_dev_mode = os.getenv("AUTH_DEV_MODE", "false").strip().lower() == "true"

    if auth_dev_mode and not (jwt_secret or jwks_url or (supabase_url and supabase_key)):
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
            )
            if "sub" in payload:
                return payload
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unable to validate authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
