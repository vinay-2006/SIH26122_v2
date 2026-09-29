"""
Project-Scoped Work Package Repository for SETUAI V7.
Enforces project isolation at repository boundary.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional
import psycopg

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository
from backend.repositories.contractor_repo import ProjectContractorRepository, ContractorNotFoundError

logger = logging.getLogger(__name__)


class WorkPackageAlreadyExistsError(Exception):
    """Raised when a work package with package_code already exists in the project."""


class WorkPackageNotFoundError(Exception):
    """Raised when a work package is not found in the project context."""


class ProjectWorkPackageRepository(BaseRepository):
    """
    Project-scoped repository for work package entities.
    Every operation requires ProjectContext and enforces project_id scoping.
    """

    @classmethod
    def create(
        cls,
        context: ProjectContext,
        package_name: str,
        contractor_id: Optional[uuid.UUID | str] = None,
        stage_id: Optional[uuid.UUID | str] = None,
        package_code: Optional[str] = None,
        discipline: Optional[str] = None,
        description: Optional[str] = None,
        planned_start: Optional[date] = None,
        planned_finish: Optional[date] = None,
        status: str = "NOT_STARTED",
    ) -> Dict[str, Any]:
        work_package_id = uuid.uuid4()
        c_id = str(contractor_id) if contractor_id else None
        s_id = str(stage_id) if stage_id else None

        if c_id:
            contractor = ProjectContractorRepository.get(context, c_id)
            if not contractor:
                raise ContractorNotFoundError(
                    f"Contractor '{c_id}' not found in project '{context.project_id}'"
                )

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO work_packages (
                            work_package_id, project_id, contractor_id, stage_id,
                            package_code, package_name, discipline, description,
                            planned_start, planned_finish, status
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING work_package_id, project_id, contractor_id, stage_id,
                                  package_code, package_name, discipline, description,
                                  planned_start, planned_finish, status, created_at, updated_at;
                        """,
                        (
                            work_package_id,
                            context.project_id,
                            c_id,
                            s_id,
                            package_code,
                            package_name,
                            discipline,
                            description,
                            planned_start,
                            planned_finish,
                            status,
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row)
                except psycopg.errors.UniqueViolation as exc:
                    conn.rollback()
                    raise WorkPackageAlreadyExistsError(
                        f"Work package code '{package_code}' already exists in project '{context.project_id}'"
                    ) from exc

    @classmethod
    def get(cls, context: ProjectContext, work_package_id: uuid.UUID | str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT work_package_id, project_id, contractor_id, stage_id,
                           package_code, package_name, discipline, description,
                           planned_start, planned_finish, status, created_at, updated_at
                    FROM work_packages
                    WHERE work_package_id = %s AND project_id = %s;
                    """,
                    (str(work_package_id), context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def get_by_code(cls, context: ProjectContext, package_code: str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT work_package_id, project_id, contractor_id, stage_id,
                           package_code, package_name, discipline, description,
                           planned_start, planned_finish, status, created_at, updated_at
                    FROM work_packages
                    WHERE package_code = %s AND project_id = %s;
                    """,
                    (package_code, context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(
        cls,
        context: ProjectContext,
        contractor_id: Optional[uuid.UUID | str] = None,
    ) -> List[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if contractor_id:
                    cur.execute(
                        """
                        SELECT work_package_id, project_id, contractor_id, stage_id,
                               package_code, package_name, discipline, description,
                               planned_start, planned_finish, status, created_at, updated_at
                        FROM work_packages
                        WHERE project_id = %s AND contractor_id = %s
                        ORDER BY package_code ASC;
                        """,
                        (context.project_id, str(contractor_id)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT work_package_id, project_id, contractor_id, stage_id,
                               package_code, package_name, discipline, description,
                               planned_start, planned_finish, status, created_at, updated_at
                        FROM work_packages
                        WHERE project_id = %s
                        ORDER BY package_code ASC;
                        """,
                        (context.project_id,),
                    )
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def update(
        cls,
        context: ProjectContext,
        work_package_id: uuid.UUID | str,
        package_name: Optional[str] = None,
        contractor_id: Optional[uuid.UUID | str] = None,
        stage_id: Optional[uuid.UUID | str] = None,
        discipline: Optional[str] = None,
        description: Optional[str] = None,
        planned_start: Optional[date] = None,
        planned_finish: Optional[date] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = cls.get(context, work_package_id)
        if not existing:
            raise WorkPackageNotFoundError(
                f"Work package '{work_package_id}' not found in project '{context.project_id}'"
            )

        if contractor_id:
            contractor = ProjectContractorRepository.get(context, contractor_id)
            if not contractor:
                raise ContractorNotFoundError(
                    f"Contractor '{contractor_id}' not found in project '{context.project_id}'"
                )

        fields = []
        values = []
        if package_name is not None:
            fields.append("package_name = %s")
            values.append(package_name)
        if contractor_id is not None:
            fields.append("contractor_id = %s")
            values.append(str(contractor_id) if contractor_id else None)
        if stage_id is not None:
            fields.append("stage_id = %s")
            values.append(str(stage_id) if stage_id else None)
        if discipline is not None:
            fields.append("discipline = %s")
            values.append(discipline)
        if description is not None:
            fields.append("description = %s")
            values.append(description)
        if planned_start is not None:
            fields.append("planned_start = %s")
            values.append(planned_start)
        if planned_finish is not None:
            fields.append("planned_finish = %s")
            values.append(planned_finish)
        if status is not None:
            fields.append("status = %s")
            values.append(status)

        if not fields:
            return existing

        fields.append("updated_at = now()")
        values.extend([str(work_package_id), context.project_id])

        query = f"""
            UPDATE work_packages
            SET {', '.join(fields)}
            WHERE work_package_id = %s AND project_id = %s
            RETURNING work_package_id, project_id, contractor_id, stage_id,
                      package_code, package_name, discipline, description,
                      planned_start, planned_finish, status, created_at, updated_at;
        """

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
                row = cur.fetchone()
                conn.commit()
                return dict(row)
