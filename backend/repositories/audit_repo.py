"""
Project-Scoped Audit Repository for SETUAI V7.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository
from backend.shared.audit import append_audit_record


class ProjectAuditRepository(BaseRepository):
    """
    Project-scoped repository for audit logs.
    Captures actor identity, project identity, role, action, and state transitions.
    """

    @classmethod
    def log(
        cls,
        context: ProjectContext,
        action: str,
        entity_type: str,
        entity_id: str,
        schedule_id: Optional[str] = None,
        old_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
    ) -> int:
        with cls.rls_connection(context.user_id) as conn:
            log_id = append_audit_record(
                conn,
                project_id=context.project_id,
                schedule_id=schedule_id,
                actor_id=str(context.user_id),
                role=context.role,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before_state=old_state,
                after_state=new_state,
                entity_context={"old_state": old_state or {}, "new_state": new_state or {}},
            )
            conn.commit()
            return log_id

    @classmethod
    def list(
        cls,
        context: ProjectContext,
        entity_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT log_id, project_id, schedule_id, actor_id, role,
                   action, entity_type, entity_id, before_state, after_state, entity_context, timestamp
            FROM audit_logs
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id, "limit": limit, "offset": offset}

        if entity_id:
            query += " AND entity_id = %(entity_id)s"
            params["entity_id"] = entity_id

        query += " ORDER BY timestamp DESC LIMIT %(limit)s OFFSET %(offset)s;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]
