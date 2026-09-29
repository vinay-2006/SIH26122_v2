"""
Repository for ITP (Inspection & Test Plan) database operations.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from backend.repositories.base import BaseRepository


class ITPRepository(BaseRepository):
    """Project-scoped repository for ITP operations."""

    @classmethod
    def create(
        cls,
        user_id: uuid.UUID | str,
        project_id: uuid.UUID,
        title: str,
        description: Optional[str] = None,
        schedule_id: Optional[str] = None,
        stage_id: Optional[uuid.UUID] = None,
        discipline: Optional[str] = None,
        responsible_party: Optional[str] = None,
        contractor_id: Optional[uuid.UUID] = None,
        work_package_id: Optional[uuid.UUID] = None,
        status: str = "DRAFT",
    ) -> Dict[str, Any]:
        """Creates a new ITP record."""
        query = """
            INSERT INTO itps (
                project_id, schedule_id, stage_id, title, description,
                discipline, responsible_party, contractor_id, work_package_id, status
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            ) RETURNING *;
        """
        params = (
            str(project_id),
            schedule_id,
            str(stage_id) if stage_id else None,
            title,
            description,
            discipline,
            responsible_party,
            str(contractor_id) if contractor_id else None,
            str(work_package_id) if work_package_id else None,
            status,
        )

        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    @classmethod
    def get_by_id(cls, user_id: uuid.UUID | str, itp_id: uuid.UUID, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Retrieves an ITP by ID, enforcing project boundary."""
        query = """
            SELECT * FROM itps 
            WHERE itp_id = %s AND project_id = %s;
        """
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (str(itp_id), str(project_id)))
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list_by_project(cls, user_id: uuid.UUID | str, project_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Lists all ITPs for a given project."""
        query = """
            SELECT * FROM itps 
            WHERE project_id = %s 
            ORDER BY created_at DESC;
        """
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (str(project_id),))
                rows = cur.fetchall()
                return [dict(row) for row in rows]
