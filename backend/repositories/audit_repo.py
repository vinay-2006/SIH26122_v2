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
        context_payload = {
            "old_state": old_state or {},
            "new_state": new_state or {},
        }
        payload_bytes = json.dumps(context_payload, sort_keys=True, default=str).encode("utf-8")
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        prev_hash = "GENESIS"
        current_hash = hashlib.sha256(f"{prev_hash}:{payload_hash}".encode("utf-8")).hexdigest()

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (
                        project_id, schedule_id, actor_id, role,
                        action, entity_type, entity_id, before_state, after_state,
                        payload_hash, previous_hash, current_hash, entity_context
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING log_id;
                    """,
                    (
                        context.project_id,
                        schedule_id,
                        str(context.user_id),
                        context.role,
                        action,
                        entity_type,
                        entity_id,
                        json.dumps(old_state or {}, default=str),
                        json.dumps(new_state or {}, default=str),
                        payload_hash,
                        prev_hash,
                        current_hash,
                        json.dumps(context_payload, default=str),
                    ),
                )
                conn.commit()
                row = cur.fetchone()
                return row["log_id"] if isinstance(row, dict) else row[0]

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
