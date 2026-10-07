"""Legacy quality gates / ITP / hold-point endpoints on v2 data (decision D8). Gates hang on the stable activity identity; the legacy `activity_id` is the external id in the viewed
schedule version. Rules (who may do what, what blocks approval) live in backend/v2/domain/quality.py and the database."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import permissions as P
from ..domain import quality as dq
from ..domain.common import actor_tx
from ..errors import ApiError
from . import shapes
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: quality"])


def _uid(c, ctx: Ctx, activity_id: Optional[str]):
    if not activity_id:
        return None
    r = c.execute("select activity_uid from baseline_activities where version_id = %s and external_activity_id = %s", (ctx.version_id or ctx.active_version_id, activity_id)).fetchone()
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Activity '{activity_id}' not found.")
    return r["activity_uid"]


def _gate_dict(g: Dict[str, Any], ext: Optional[str], version) -> Dict[str, Any]:
    return {"quality_gate_id": str(g["quality_gate_id"]), "project_id": str(g["project_id"]), "schedule_id": str(version) if version else None,
            "stage_id": str(g["stage_wbs_uid"]) if g["stage_wbs_uid"] else None, "activity_id": ext, "itp_id": str(g["itp_id"]) if g["itp_id"] else None,
            "gate_name": g["gate_name"], "gate_type": g["gate_type"], "checkpoint_category": g["checkpoint_category"], "required": g["required"], "status": g["status"],
            "due_date": shapes.iso(g["due_date"]), "remarks": g["remarks"], "passed_at": shapes.iso(g["passed_at"]), "passed_by": str(g["passed_by"]) if g["passed_by"] else None,
            "waived_at": shapes.iso(g["waived_at"]), "waived_by": str(g["waived_by"]) if g["waived_by"] else None, "waiver_reason": g["waiver_reason"],
            "contractor_id": None, "work_package_id": None, "created_at": shapes.iso(g["created_at"]), "updated_at": shapes.iso(g["updated_at"])}


def _gates(c, ctx: Ctx, where: str = "true", params: tuple = ()) -> List[Dict[str, Any]]:
    ver = ctx.version_id or ctx.active_version_id
    rows = c.execute("select g.*, ba.external_activity_id as ext from quality_gates g left join baseline_activities ba on ba.activity_uid = g.activity_uid and ba.version_id = %s "
                     f"where g.project_id = %s and {where} order by g.created_at, g.gate_name", (ver, ctx.project_id, *params)).fetchall()
    return [_gate_dict(r, r["ext"], ver) for r in rows]


def _one(ctx: Ctx, gate_id) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = _gates(c, ctx, "g.quality_gate_id = %s", (gate_id,))
    if not rows:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"QualityGate '{gate_id}' not found.")
    return rows[0]


def _gid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"QualityGate '{raw}' not found.")


class ITPIn(BaseModel):
    title: str
    description: Optional[str] = None
    schedule_id: Optional[str] = None
    stage_id: Optional[str] = None
    discipline: Optional[str] = None
    responsible_party: Optional[str] = None
    contractor_id: Optional[str] = None
    work_package_id: Optional[str] = None
    status: str = Field(default="DRAFT", pattern="^(DRAFT|ACTIVE|ARCHIVED)$")


def _itp(r) -> Dict[str, Any]:
    return {"itp_id": str(r["itp_id"]), "project_id": str(r["project_id"]), "title": r["title"], "description": r["description"], "schedule_id": None, "stage_id": None,
            "discipline": r["discipline_code"], "responsible_party": r["responsible_party"], "contractor_id": None, "work_package_id": None, "status": r["status"],
            "created_at": shapes.iso(r["created_at"]), "updated_at": shapes.iso(r["updated_at"])}


@router.post("/itps", status_code=201)
def create_itp(body: ITPIn, ctx: Ctx = Depends(path_ctx(P.MANAGE_QUALITY, writable=True))):
    if body.contractor_id or body.work_package_id:
        raise ApiError(422, "NOT_AVAILABLE", "Contractors and work packages are not part of this release (scope pending approval)")
    return _itp(dq.create_itp(ctx.actor, title=body.title, description=body.description, discipline_code=body.discipline, responsible_party=body.responsible_party, status=body.status))


@router.get("/itps")
def list_itps(ctx: Ctx = Depends(path_ctx(P.VIEW_QUALITY))):
    return [_itp(r) for r in dq.list_itps(ctx.actor)]


@router.get("/itps/{itp_id}")
def get_itp(itp_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_QUALITY))):
    return _itp(dq.get_itp(ctx.actor, _gid(itp_id)))


class GateIn(BaseModel):
    gate_name: str
    gate_type: str = Field(pattern="^(INSPECTION|TEST|POUR_CARD|WELD_INSPECTION|NDT|MATERIAL_CERTIFICATE|NCR_CLEARANCE|CLIENT_APPROVAL|PRE_COMMENCEMENT|INTERMEDIATE_HOLD|CLEARANCE|FINAL_TAKEOVER|SAFETY_AUDIT)$")
    checkpoint_category: str = Field(default="QUALITY_CHECK", pattern="^(HOLD|WITNESS|REVIEW|QUALITY_CHECK)$")
    stage_id: Optional[str] = None
    schedule_id: Optional[str] = None
    activity_id: Optional[str] = None
    itp_id: Optional[str] = None
    contractor_id: Optional[str] = None
    work_package_id: Optional[str] = None
    required: bool = True
    due_date: Optional[date] = None
    remarks: Optional[str] = None


@router.post("/quality-gates", status_code=201)
def create_gate(body: GateIn, ctx: Ctx = Depends(path_ctx(P.MANAGE_QUALITY, writable=True))):
    if body.contractor_id or body.work_package_id:
        raise ApiError(422, "NOT_AVAILABLE", "Contractors and work packages are not part of this release (scope pending approval)")
    with actor_tx(ctx.actor, readonly=True) as c:
        uid = _uid(c, ctx, body.activity_id)
    stage = uuid.UUID(body.stage_id) if body.stage_id else None
    g = dq.create_gate(ctx.actor, gate_name=body.gate_name, gate_type=body.gate_type, checkpoint_category=body.checkpoint_category, activity_uid=uid, stage_wbs_uid=stage,
                       itp_id=uuid.UUID(body.itp_id) if body.itp_id else None, required=body.required, due_date=body.due_date, remarks=body.remarks)
    return _one(ctx, g["quality_gate_id"])


@router.get("/activities/{activity_id}/quality-gates")
def activity_gates(activity_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_QUALITY))):
    with actor_tx(ctx.actor, readonly=True) as c:
        return _gates(c, ctx, "g.activity_uid = %s", (_uid(c, ctx, activity_id),))


@router.get("/schedules/{schedule_id}/quality-gates")
def schedule_gates(status: Optional[str] = None, ctx: Ctx = Depends(path_ctx(P.VIEW_QUALITY))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = _gates(c, ctx)
    return [g for g in rows if not status or g["status"] == status]


class EvidenceIn(BaseModel):
    source_document_id: Optional[str] = None
    evidence_type: str = Field(pattern="^(TEST_REPORT|PHOTO|THIRD_PARTY_CERT|NCR_CLEARANCE|INSPECTION_NOTE|POUR_CARD|WELD_INSPECTION|NDT_RESULT|MATERIAL_CERTIFICATE|CLIENT_APPROVAL|OTHER)$")
    result: str = Field(default="PASS", pattern="^(PASS|FAIL|PENDING_REVIEW)$")
    inspector_name: Optional[str] = None
    inspection_date: Optional[date] = None
    evidence_hash: Optional[str] = None
    metadata: Dict[str, Any] = {}


@router.post("/quality-gates/{quality_gate_id}/evidence", status_code=201)
def submit_evidence(quality_gate_id: str, body: EvidenceIn, ctx: Ctx = Depends(path_ctx(P.SUBMIT_QUALITY_EVIDENCE, writable=True))):
    ev = dq.submit_evidence(ctx.actor, _gid(quality_gate_id), evidence_type=body.evidence_type, result=body.result,
                            document_id=uuid.UUID(body.source_document_id) if body.source_document_id else None, inspector_name=body.inspector_name,
                            inspection_date=body.inspection_date, evidence_hash=body.evidence_hash, metadata=body.metadata)
    return {"quality_evidence_id": str(ev["quality_evidence_id"]), "quality_gate_id": quality_gate_id, "evidence_type": ev["evidence_type"], "result": ev["result"],
            "inspector_name": ev["inspector_name"], "inspection_date": shapes.iso(ev["inspection_date"]), "metadata": ev["metadata"], "created_at": shapes.iso(ev["created_at"])}


class RemarksIn(BaseModel):
    remarks: Optional[str] = None


class WaiveIn(BaseModel):
    waiver_reason: str = Field(min_length=3)
    remarks: Optional[str] = None


@router.post("/quality-gates/{quality_gate_id}/pass")
def pass_gate(quality_gate_id: str, body: RemarksIn = RemarksIn(), ctx: Ctx = Depends(path_ctx(P.MANAGE_QUALITY, writable=True))):
    dq.pass_gate(ctx.actor, _gid(quality_gate_id), body.remarks)
    return _one(ctx, _gid(quality_gate_id))


@router.post("/quality-gates/{quality_gate_id}/fail")
def fail_gate(quality_gate_id: str, body: RemarksIn = RemarksIn(), ctx: Ctx = Depends(path_ctx(P.MANAGE_QUALITY, writable=True))):
    dq.fail_gate(ctx.actor, _gid(quality_gate_id), body.remarks)
    return _one(ctx, _gid(quality_gate_id))


@router.post("/quality-gates/{quality_gate_id}/waive")
def waive_gate(quality_gate_id: str, body: WaiveIn, ctx: Ctx = Depends(path_ctx(P.MANAGE_QUALITY, writable=True))):
    dq.waive_gate(ctx.actor, _gid(quality_gate_id), body.waiver_reason, body.remarks)
    return _one(ctx, _gid(quality_gate_id))


@router.get("/activities/{activity_id}/quality-status")
def quality_status(activity_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_QUALITY))):
    with actor_tx(ctx.actor, readonly=True) as c:
        uid = _uid(c, ctx, activity_id)
        st = dq.activity_status(c, ctx.project_id, uid)
        gates = _gates(c, ctx, "g.activity_uid = %s", (uid,))
    return {"activity_id": activity_id, **{k: v for k, v in st.items() if k != "gates"}, "gates": gates}
