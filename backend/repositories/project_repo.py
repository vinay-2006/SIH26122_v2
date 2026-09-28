"""
Project and Membership Repositories for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
import psycopg

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository
from backend.shared.db import get_connection


class ProjectAlreadyExistsError(Exception):
    """Raised when a project with the given project_code already exists."""


class ProjectRepository(BaseRepository):
    """Repository for project metadata queries and mutations."""

    @classmethod
    def get_by_id(cls, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT project_id, project_code, project_name, description,
                           client_name, project_type, location, latitude, longitude,
                           geofence_radius_m, planned_start, planned_finish, contract_finish,
                           status, created_by, created_at, updated_at
                    FROM projects
                    WHERE project_id = %s;
                    """,
                    (project_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def get_by_code(cls, project_code: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT project_id, project_code, project_name, description,
                           client_name, project_type, location, latitude, longitude,
                           geofence_radius_m, planned_start, planned_finish, contract_finish,
                           status, created_by, created_at, updated_at
                    FROM projects
                    WHERE project_code = %s;
                    """,
                    (project_code,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def create_project(cls, project_data: Dict[str, Any], creator_user_id: uuid.UUID) -> Dict[str, Any]:
        """
        Atomically creates a project and establishes initial PROJECT_MANAGER membership
        for the creator.
        """
        project_id = project_data.get("project_id") or uuid.uuid4()
        membership_id = uuid.uuid4()

        with get_connection() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO projects (
                            project_id, project_code, project_name, description,
                            client_name, project_type, location, latitude, longitude,
                            geofence_radius_m, planned_start, planned_finish, contract_finish,
                            status, created_by
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                        RETURNING project_id, project_code, project_name, description,
                                  client_name, project_type, location, latitude, longitude,
                                  geofence_radius_m, planned_start, planned_finish, contract_finish,
                                  status, created_by, created_at, updated_at;
                        """,
                        (
                            project_id,
                            project_data["project_code"],
                            project_data["project_name"],
                            project_data.get("description"),
                            project_data.get("client_name"),
                            project_data.get("project_type"),
                            project_data.get("location"),
                            project_data.get("latitude"),
                            project_data.get("longitude"),
                            project_data.get("geofence_radius_m"),
                            project_data.get("planned_start"),
                            project_data.get("planned_finish"),
                            project_data.get("contract_finish"),
                            project_data.get("status", "ACTIVE"),
                            creator_user_id,
                        ),
                    )
                    created_project = dict(cur.fetchone())

                    # Create initial membership for creator as PROJECT_MANAGER
                    cur.execute(
                        """
                        INSERT INTO project_memberships (
                            membership_id, user_id, project_id, assigned_role, active, status
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING membership_id;
                        """,
                        (
                            membership_id,
                            creator_user_id,
                            project_id,
                            "PROJECT_MANAGER",
                            True,
                            "ACTIVE",
                        ),
                    )

                conn.commit()
                created_project["membership_id"] = membership_id
                return created_project
            except psycopg.errors.UniqueViolation as exc:
                conn.rollback()
                raise ProjectAlreadyExistsError(
                    f"Project with code '{project_data['project_code']}' already exists"
                ) from exc
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def update_project(cls, project_id: uuid.UUID, update_fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Updates project metadata fields."""
        if not update_fields:
            return cls.get_by_id(project_id)

        set_clauses = []
        params = []
        for k, v in update_fields.items():
            set_clauses.append(f"{k} = %s")
            params.append(v)

        set_clauses.append("updated_at = now()")
        params.append(project_id)

        sql = f"""
            UPDATE projects
            SET {', '.join(set_clauses)}
            WHERE project_id = %s
            RETURNING project_id, project_code, project_name, description,
                      client_name, project_type, location, latitude, longitude,
                      geofence_radius_m, planned_start, planned_finish, contract_finish,
                      status, created_by, created_at, updated_at;
        """

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                conn.commit()
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def update_status(cls, project_id: uuid.UUID, new_status: str) -> Optional[Dict[str, Any]]:
        """Transitions project status (e.g. ACTIVE, ARCHIVED)."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET status = %s, updated_at = now()
                    WHERE project_id = %s
                    RETURNING project_id, project_code, project_name, description,
                              client_name, project_type, location, latitude, longitude,
                              geofence_radius_m, planned_start, planned_finish, contract_finish,
                              status, created_by, created_at, updated_at;
                    """,
                    (new_status, project_id),
                )
                conn.commit()
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list_for_user(cls, user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """List all projects where user has an active membership."""
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.project_id, p.project_code, p.project_name, p.status, pm.assigned_role
                    FROM projects p
                    JOIN project_memberships pm ON pm.project_id = p.project_id
                    WHERE pm.user_id = %s AND pm.active = TRUE
                    ORDER BY p.project_name ASC;
                    """,
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]


class MembershipRepository(BaseRepository):
    """Repository for project membership queries."""

    @classmethod
    def get_membership(cls, user_id: uuid.UUID, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT membership_id, user_id, project_id, assigned_role, active, status, created_at
                    FROM project_memberships
                    WHERE user_id = %s AND project_id = %s;
                    """,
                    (user_id, project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list_members(cls, context: ProjectContext) -> List[Dict[str, Any]]:
        """List members for authorized project context."""
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pm.membership_id, pm.user_id, pm.project_id, pm.assigned_role, pm.active,
                           p.full_name, p.role as profile_role
                    FROM project_memberships pm
                    JOIN profiles p ON p.id = pm.user_id
                    WHERE pm.project_id = %s
                    ORDER BY p.full_name ASC;
                    """,
                    (context.project_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def add_member(
        cls,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        assigned_role: str,
    ) -> Dict[str, Any]:
        """Assigns a user to a project with a specific role."""
        membership_id = uuid.uuid4()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO project_memberships (
                        membership_id, user_id, project_id, assigned_role, active, status
                    ) VALUES (%s, %s, %s, %s, TRUE, 'ACTIVE')
                    ON CONFLICT (user_id, project_id)
                    DO UPDATE SET assigned_role = EXCLUDED.assigned_role, active = TRUE, status = 'ACTIVE'
                    RETURNING membership_id, user_id, project_id, assigned_role, active, status;
                    """,
                    (membership_id, user_id, project_id, assigned_role),
                )
                conn.commit()
                return dict(cur.fetchone())
