"""
SETUAI V7 Authentication Package.
"""

from backend.auth.dependencies import get_current_user, require_authenticated_user
from backend.auth.jwt import decode_supabase_jwt
from backend.auth.models import CurrentUser, UserProfile

__all__ = [
    "CurrentUser",
    "UserProfile",
    "decode_supabase_jwt",
    "get_current_user",
    "require_authenticated_user",
]
