"""
Project-Scoped Contractor Repository for SETUAI V7.
Enforces project isolation at repository boundary.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
import psycopg

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ContractorAlreadyExistsError(Exception):
    """Raised when a contractor with contractor_code already exists in the project."""


class ContractorNotFoundError(Exception):
    """Raised when a contractor is not found in the project context."""


class ProjectContractorRepository(BaseRepository):
    """
    Project-scoped repository for contractor entities.
    Every operation requires ProjectContext and enforces project_id scoping.
    """

    @classmethod
    def create(
        cls,
        context: ProjectContext,
        contractor_code: str,
        company_name: str,
        type_or_category: Optional[str] = None,
        contract_reference: Optional[str] = None,
        contact_email: Optional[str] = None,
        status: str = "ACTIVE",
        active: bool = True,
    ) -> Dict[str, Any]:
        contractor_id = uuid.uuid4()
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO contractors (
                            contractor_id, project_id, contractor_code, company_name,
                            type_or_category, contract_reference, contact_email, status, active
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING contractor_id, project_id, contractor_code, company_name,
                                  type_or_category, contract_reference, contact_email,
                                  status, active, created_at, updated_at;
                        """,
                        (
                            contractor_id,
                            context.project_id,
                            contractor_code,
                            company_name,
                            type_or_category,
                            contract_reference,
                            contact_email,
                            status,
                            active,
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row)
                except psycopg.errors.UniqueViolation as exc:
                    conn.rollback()
                    raise ContractorAlreadyExistsError(
                        f"Contractor code '{contractor_code}' already exists in project '{context.project_id}'"
                    ) from exc

    @classmethod
    def get(cls, context: ProjectContext, contractor_id: uuid.UUID | str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT contractor_id, project_id, contractor_code, company_name,
                           type_or_category, contract_reference, contact_email,
                           status, active, created_at, updated_at
                    FROM contractors
                    WHERE contractor_id = %s AND project_id = %s;
                    """,
                    (str(contractor_id), context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def get_by_code(cls, context: ProjectContext, contractor_code: str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT contractor_id, project_id, contractor_code, company_name,
                           type_or_category, contract_reference, contact_email,
                           status, active, created_at, updated_at
                    FROM contractors
                    WHERE contractor_code = %s AND project_id = %s;
                    """,
                    (contractor_code, context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(cls, context: ProjectContext) -> List[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT contractor_id, project_id, contractor_code, company_name,
                           type_or_category, contract_reference, contact_email,
                           status, active, created_at, updated_at
                    FROM contractors
                    WHERE project_id = %s
                    ORDER BY contractor_code ASC;
                    """,
                    (context.project_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def update(
        cls,
        context: ProjectContext,
        contractor_id: uuid.UUID | str,
        company_name: Optional[str] = None,
        type_or_category: Optional[str] = None,
        contract_reference: Optional[str] = None,
        contact_email: Optional[str] = None,
        status: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        existing = cls.get(context, contractor_id)
        if not existing:
            raise ContractorNotFoundError(
                f"Contractor '{contractor_id}' not found in project '{context.project_id}'"
            )

        fields = []
        values = []
        if company_name is not None:
            fields.append("company_name = %s")
            values.append(company_name)
        if type_or_category is not None:
            fields.append("type_or_category = %s")
            values.append(type_or_category)
        if contract_reference is not None:
            fields.append("contract_reference = %s")
            values.append(contract_reference)
        if contact_email is not None:
            fields.append("contact_email = %s")
            values.append(contact_email)
        if status is not None:
            fields.append("status = %s")
            values.append(status)
        if active is not None:
            fields.append("active = %s")
            values.append(active)

        if not fields:
            return existing

        fields.append("updated_at = now()")
        values.extend([str(contractor_id), context.project_id])

        query = f"""
            UPDATE contractors
            SET {', '.join(fields)}
            WHERE contractor_id = %s AND project_id = %s
            RETURNING contractor_id, project_id, contractor_code, company_name,
                      type_or_category, contract_reference, contact_email,
                      status, active, created_at, updated_at;
        """

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
                row = cur.fetchone()
                conn.commit()
                return dict(row)
