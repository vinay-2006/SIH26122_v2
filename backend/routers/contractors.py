"""
Contractor Domain Router for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import List
from fastapi import APIRouter, Depends, status

from backend.context.project import ProjectContext, require_project_context
from backend.schemas.contractor import ContractorCreate, ContractorResponse, ContractorUpdate
from backend.services.contractor_service import ContractorService

router = APIRouter(prefix="/api/v1/projects", tags=["contractors"])


@router.post(
    "/{project_id}/contractors",
    response_model=ContractorResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contractor(
    project_id: uuid.UUID,
    payload: ContractorCreate,
    context: ProjectContext = Depends(require_project_context),
) -> ContractorResponse:
    """
    Creates a new contractor under the authorized project context.
    """
    created = ContractorService.create_contractor(context, payload)
    return ContractorResponse(**created)


@router.get(
    "/{project_id}/contractors",
    response_model=List[ContractorResponse],
)
def list_contractors(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> List[ContractorResponse]:
    """
    Lists all contractors belonging to the authorized project context.
    """
    contractors = ContractorService.list_contractors(context)
    return [ContractorResponse(**c) for c in contractors]


@router.get(
    "/{project_id}/contractors/{contractor_id}",
    response_model=ContractorResponse,
)
def get_contractor(
    project_id: uuid.UUID,
    contractor_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ContractorResponse:
    """
    Retrieves a contractor by ID within the authorized project context.
    """
    contractor = ContractorService.get_contractor(context, contractor_id)
    return ContractorResponse(**contractor)


@router.patch(
    "/{project_id}/contractors/{contractor_id}",
    response_model=ContractorResponse,
)
def update_contractor(
    project_id: uuid.UUID,
    contractor_id: uuid.UUID,
    payload: ContractorUpdate,
    context: ProjectContext = Depends(require_project_context),
) -> ContractorResponse:
    """
    Updates contractor metadata within the authorized project context.
    """
    updated = ContractorService.update_contractor(context, contractor_id, payload)
    return ContractorResponse(**updated)
