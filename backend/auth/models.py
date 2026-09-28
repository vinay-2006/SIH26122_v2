"""
Authentication models for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Optional
from pydantic import BaseModel, Field


class CurrentUser(BaseModel):
    """
    Canonical representation of an authenticated user identity extracted from
    the validated Supabase JWT and verified against the profiles table.
    """
    id: str = Field(description="UUID string of the authenticated user")
    email: Optional[str] = Field(default=None, description="Email address from JWT or profile")
    full_name: Optional[str] = Field(default=None, description="Full name from profiles record")
    role: Optional[str] = Field(default=None, description="System/profile role")

    @property
    def user_id(self) -> uuid.UUID:
        """Returns user identity as a standard Python UUID."""
        return uuid.UUID(self.id)

    def __str__(self) -> str:
        return f"CurrentUser(id={self.id}, name={self.full_name}, role={self.role})"


# Backward-compatible alias for existing V6 code and tests
class UserProfile(CurrentUser):
    """UserProfile is an alias/subclass of CurrentUser for full backward compatibility."""
    pass
