"""
Security and Audit Context for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from backend.context.project import ProjectContext
from backend.repositories.audit_repo import ProjectAuditRepository


class AuditContext(BaseModel):
    """Normalized audit record payload."""
    user_id: uuid.UUID
    project_id: uuid.UUID
    schedule_id: Optional[str] = None
    role: str
    action: str
    entity_type: str
    entity_id: str
    result: str = "SUCCESS"
    old_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_context(
        cls,
        context: ProjectContext,
        action: str,
        entity_type: str,
        entity_id: str,
        schedule_id: Optional[str] = None,
        result: str = "SUCCESS",
        old_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
    ) -> AuditContext:
        return cls(
            user_id=context.user_id,
            project_id=context.project_id,
            schedule_id=schedule_id,
            role=context.role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            result=result,
            old_state=old_state,
            new_state=new_state,
        )

    def persist(self, context: ProjectContext) -> uuid.UUID:
        """Persist this audit event via the project-scoped audit repository."""
        return ProjectAuditRepository.log(
            context=context,
            action=self.action,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            schedule_id=self.schedule_id,
            old_state=self.old_state,
            new_state=self.new_state,
        )
