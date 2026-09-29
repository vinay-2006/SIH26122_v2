"""
Contractor Domain Service for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.contractor_repo import (
    ContractorAlreadyExistsError,
    ContractorNotFoundError,
    ProjectContractorRepository,
)
from backend.schemas.contractor import ContractorCreate, ContractorUpdate


class ContractorService:
    """Service layer managing the Contractor Domain."""

    @classmethod
    def create_contractor(
        cls,
        context: ProjectContext,
        payload: ContractorCreate,
    ) -> Dict[str, Any]:
        """
        Creates a new contractor under the authorized ProjectContext.
        """
        try:
            created = ProjectContractorRepository.create(
                context=context,
                contractor_code=payload.contractor_code,
                company_name=payload.company_name,
                type_or_category=payload.type_or_category,
                contract_reference=payload.contract_reference,
                contact_email=payload.contact_email,
                status=payload.status or "ACTIVE",
                active=payload.active if payload.active is not None else True,
            )
        except ContractorAlreadyExistsError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e

        ProjectAuditRepository.log(
            context=context,
            action="CONTRACTOR_CREATED",
            entity_type="CONTRACTOR",
            entity_id=str(created["contractor_id"]),
            new_state={
                "contractor_code": created.get("contractor_code"),
                "company_name": created.get("company_name"),
            },
        )
        return created

    @classmethod
    def get_contractor(
        cls,
        context: ProjectContext,
        contractor_id: uuid.UUID | str,
    ) -> Dict[str, Any]:
        contractor = ProjectContractorRepository.get(context, contractor_id)
        if not contractor:
            raise_resource_not_found("Contractor", str(contractor_id))
        return contractor

    @classmethod
    def list_contractors(
        cls,
        context: ProjectContext,
    ) -> List[Dict[str, Any]]:
        return ProjectContractorRepository.list(context)

    @classmethod
    def update_contractor(
        cls,
        context: ProjectContext,
        contractor_id: uuid.UUID | str,
        payload: ContractorUpdate,
    ) -> Dict[str, Any]:
        try:
            updated = ProjectContractorRepository.update(
                context=context,
                contractor_id=contractor_id,
                company_name=payload.company_name,
                type_or_category=payload.type_or_category,
                contract_reference=payload.contract_reference,
                contact_email=payload.contact_email,
                status=payload.status,
                active=payload.active,
            )
        except ContractorNotFoundError as e:
            raise_resource_not_found("Contractor", str(contractor_id))

        ProjectAuditRepository.log(
            context=context,
            action="CONTRACTOR_UPDATED",
            entity_type="CONTRACTOR",
            entity_id=str(contractor_id),
            new_state={"company_name": updated.get("company_name"), "status": updated.get("status")},
        )
        return updated
