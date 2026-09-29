"""
Quality, ITP, and Hold Points REST Router for SETUAI V7.
Exposes project-scoped quality management endpoints.
"""

from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, status

from backend.context.project import ProjectContext, require_project_context
from backend.schemas.quality import (
    ITPCreate,
    ITPResponse,
    QualityEvidenceCreate,
    QualityEvidenceResponse,
    QualityGateCreate,
    QualityGateFailPayload,
    QualityGatePassPayload,
    QualityGateResponse,
    QualityGateWaivePayload,
    QualityStatusResponse,
)
from backend.services.quality_service import QualityService

router = APIRouter(prefix="/api/v1/projects", tags=["quality"])


# ---------------------------------------------------------
# ITP Endpoints
# ---------------------------------------------------------

@router.post(
    "/{project_id}/itps",
    response_model=ITPResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_itp(
    project_id: uuid.UUID,
    payload: ITPCreate,
    context: ProjectContext = Depends(require_project_context),
) -> ITPResponse:
    """Creates an Inspection & Test Plan under the authorized project context."""
    created = QualityService.create_itp(context, payload)
    return ITPResponse(**created)


@router.get(
    "/{project_id}/itps",
    response_model=List[ITPResponse],
)
def list_itps(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> List[ITPResponse]:
    """Lists all Inspection & Test Plans for the authorized project context."""
    itps = QualityService.list_itps(context)
    return [ITPResponse(**itp) for itp in itps]


@router.get(
    "/{project_id}/itps/{itp_id}",
    response_model=ITPResponse,
)
def get_itp(
    project_id: uuid.UUID,
    itp_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ITPResponse:
    """Retrieves an ITP by ID within the authorized project context."""
    itp = QualityService.get_itp(context, itp_id)
    return ITPResponse(**itp)


# ---------------------------------------------------------
# Quality Checkpoints / Gates Endpoints
# ---------------------------------------------------------

@router.post(
    "/{project_id}/quality-gates",
    response_model=QualityGateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_quality_gate(
    project_id: uuid.UUID,
    payload: QualityGateCreate,
    context: ProjectContext = Depends(require_project_context),
) -> QualityGateResponse:
    """Creates a quality gate or hold point under the authorized project context."""
    created = QualityService.create_quality_gate(context, payload)
    return QualityGateResponse(**created)


@router.get(
    "/{project_id}/activities/{activity_id}/quality-gates",
    response_model=List[QualityGateResponse],
)
def list_activity_quality_gates(
    project_id: uuid.UUID,
    activity_id: str,
    context: ProjectContext = Depends(require_project_context),
) -> List[QualityGateResponse]:
    """Lists all quality gates / checkpoints for a specific activity."""
    gates = QualityService.list_activity_quality_gates(context, activity_id)
    return [QualityGateResponse(**gate) for gate in gates]


# ---------------------------------------------------------
# Evidence & Status Transition Endpoints
# ---------------------------------------------------------

@router.post(
    "/{project_id}/quality-gates/{quality_gate_id}/evidence",
    response_model=QualityEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_quality_evidence(
    project_id: uuid.UUID,
    quality_gate_id: uuid.UUID,
    payload: QualityEvidenceCreate,
    context: ProjectContext = Depends(require_project_context),
) -> QualityEvidenceResponse:
    """Submits evidence for a quality gate."""
    evidence = QualityService.submit_quality_evidence(context, quality_gate_id, payload)
    return QualityEvidenceResponse(**evidence)


@router.post(
    "/{project_id}/quality-gates/{quality_gate_id}/pass",
    response_model=QualityGateResponse,
)
def pass_quality_gate(
    project_id: uuid.UUID,
    quality_gate_id: uuid.UUID,
    payload: Optional[QualityGatePassPayload] = None,
    context: ProjectContext = Depends(require_project_context),
) -> QualityGateResponse:
    """Approves / passes a quality gate."""
    remarks = payload.remarks if payload else None
    passed = QualityService.pass_quality_gate(context, quality_gate_id, remarks=remarks)
    return QualityGateResponse(**passed)


@router.post(
    "/{project_id}/quality-gates/{quality_gate_id}/fail",
    response_model=QualityGateResponse,
)
def fail_quality_gate(
    project_id: uuid.UUID,
    quality_gate_id: uuid.UUID,
    payload: Optional[QualityGateFailPayload] = None,
    context: ProjectContext = Depends(require_project_context),
) -> QualityGateResponse:
    """Fails a quality gate."""
    remarks = payload.remarks if payload else None
    failed = QualityService.fail_quality_gate(context, quality_gate_id, remarks=remarks)
    return QualityGateResponse(**failed)


@router.post(
    "/{project_id}/quality-gates/{quality_gate_id}/waive",
    response_model=QualityGateResponse,
)
def waive_quality_gate(
    project_id: uuid.UUID,
    quality_gate_id: uuid.UUID,
    payload: QualityGateWaivePayload,
    context: ProjectContext = Depends(require_project_context),
) -> QualityGateResponse:
    """Waives a quality gate (requires waiver_reason)."""
    waived = QualityService.waive_quality_gate(
        context,
        quality_gate_id,
        waiver_reason=payload.waiver_reason,
        remarks=payload.remarks,
    )
    return QualityGateResponse(**waived)


# ---------------------------------------------------------
# Activity Quality Status & Eligibility Endpoint
# ---------------------------------------------------------

@router.get(
    "/{project_id}/activities/{activity_id}/quality-status",
    response_model=QualityStatusResponse,
)
def get_activity_quality_status(
    project_id: uuid.UUID,
    activity_id: str,
    context: ProjectContext = Depends(require_project_context),
) -> QualityStatusResponse:
    """
    Evaluates and returns canonical quality status and execution eligibility for an activity.
    Exposes the Member 1 Integration Contract.
    """
    status_dict = QualityService.get_quality_status(context, activity_id)
    return QualityStatusResponse(**status_dict)
