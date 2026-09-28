"""
Authentication dependencies for FastAPI in SETUAI V7.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.jwt import decode_supabase_jwt
from backend.auth.models import CurrentUser
from backend.shared.db import get_connection

security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> CurrentUser:
    """
    FastAPI dependency that extracts and verifies the Supabase JWT
    from the Authorization header, looking up the authenticated user's
    profile from the profiles table.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_supabase_jwt(token)

    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject claim ('sub')",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, full_name, role
                    FROM profiles
                    WHERE id = %s;
                    """,
                    (user_id,),
                )
                row = cur.fetchone()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database lookup error during authentication: {str(e)}",
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authenticated user '{user_id}' has no profile record in database",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        id=str(row["id"] if isinstance(row, dict) else row[0]),
        email=payload.get("email"),
        full_name=row["full_name"] if isinstance(row, dict) else row[1],
        role=row["role"] if isinstance(row, dict) else row[2],
    )


def require_authenticated_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Explicit dependency alias ensuring caller is authenticated."""
    return current_user
