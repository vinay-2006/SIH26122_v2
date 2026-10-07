"""Legacy issues & delays, root causes, root-cause analysis and institutional-memory endpoints on v2 data. Writes go through the v2 issue domain; the memory ranking is the
ORIGINAL ranking engine (backend/memory/ranking.py: explainable scoring, semantic when the encoder is available, lexical otherwise) fed from v2's institutional_memory."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from .. import permissions as P
from ..domain import issues as di
from ..domain.common import PM, SE, SUP, actor_tx
from ..errors import ApiError
from ..services import documents as docsvc
from . import shapes
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: issues"])


def _id(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"{what} '{raw}' not found.")


def _version(ctx: Ctx) -> uuid.UUID:
    if ctx.version_id is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    return ctx.version_id


# ---------------------------------------------------------------------------------------------------------------------------------- reads
_ISSUE_SQL = """
select i.*, cat.name as category_name, ba.external_activity_id as ext, ba.activity_name, ba.discipline_code, stg.wbs_uid as stage_uid, stg.wbs_name as stage_name,
       rp.full_name as reporter, rs.full_name as resolver, rc.title as root_cause_title,
       (select m.memory_id from institutional_memory m where m.issue_id = i.issue_id limit 1) as memory_id,
       (select coalesce(json_agg(json_build_object('document_id', d.document_id, 'file_name', d.file_name) order by d.uploaded_at), '[]'::json)
          from issue_evidence ie join source_documents d on d.document_id = ie.document_id where ie.issue_id = i.issue_id) as evidence
  from issues i join issue_categories cat on cat.code = i.category_code
  left join baseline_activities ba on ba.version_id = %(ver)s and ba.activity_uid = i.activity_uid
  left join schedule_wbs sw on sw.wbs_id = ba.wbs_id
  left join lateral (select st.wbs_uid, st.wbs_name from schedule_wbs st where st.version_id = %(ver)s and st.node_type = 'STAGE'
                      and (st.wbs_uid = i.stage_wbs_uid or (i.stage_wbs_uid is null and sw.wbs_path like st.wbs_path || '%%')) order by length(st.wbs_path) desc limit 1) stg on true
  left join profiles rp on rp.id = i.reported_by left join profiles rs on rs.id = i.resolved_by left join root_causes rc on rc.root_cause_id = i.root_cause_id
 where i.project_id = %(project)s and {where} order by i.reported_date desc, i.created_at desc limit 500
"""


def _issue_dict(r: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    return {"issue_id": str(r["issue_id"]), "project_id": str(ctx.project_id), "schedule_id": str(ctx.version_id), "activity_id": r["ext"], "activity_name": r["activity_name"],
            "discipline": r["discipline_code"], "stage_id": str(r["stage_uid"]) if r["stage_uid"] else None, "stage_name": r["stage_name"], "category_code": r["category_code"],
            "category_name": r["category_name"], "title": r["title"], "description": r["description"] or "", "severity": r["severity"], "reported_date": shapes.iso(r["reported_date"]),
            "expected_duration_days": shapes.num(r["expected_duration_days"]), "blocks_work": r["blocks_work"], "status": r["status"],
            "source_event_id": str(r["source_event_id"]) if r["source_event_id"] else None, "reported_by": str(r["reported_by"]), "reported_by_name": r["reporter"],
            "created_at": shapes.iso(r["created_at"]), "resolved_by": str(r["resolved_by"]) if r["resolved_by"] else None, "resolved_by_name": r["resolver"],
            "resolved_at": shapes.iso(r["resolved_at"]), "resolution_notes": r["resolution_notes"], "root_cause_id": str(r["root_cause_id"]) if r["root_cause_id"] else None,
            "root_cause_title": r["root_cause_title"], "evidence": [{"document_id": str(e["document_id"]), "file_name": e["file_name"]} for e in (r["evidence"] or [])],
            "memory_incident_id": str(r["memory_id"]) if r["memory_id"] else None}


def load_issues(c, ctx: Ctx, where: str = "true", params: Optional[dict] = None) -> List[Dict[str, Any]]:
    mine = "i.reported_by = %(user)s" if ctx.access.role == SE else "true"          # an engineer sees the issues they raised
    rows = c.execute(_ISSUE_SQL.format(where=f"({mine}) and ({where})"), {"ver": _version(ctx), "project": ctx.project_id, "user": ctx.user.id, **(params or {})}).fetchall()
    return [_issue_dict(r, ctx) for r in rows]


@router.get("/issue-categories")
def categories(ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT))):
    with actor_tx(ctx.actor, readonly=True) as c:
        return [{"code": r["code"], "name": r["name"]} for r in c.execute("select code, name from issue_categories order by sort_order, name").fetchall()]


class IssueIn(BaseModel):
    activity_id: Optional[str] = None
    stage_id: Optional[str] = None
    category_code: str
    title: str
    description: str = ""
    severity: str = "MEDIUM"
    reported_date: Optional[date] = None
    expected_duration_days: Optional[float] = None
    blocks_work: bool = True


@router.post("/schedules/{schedule_id}/issues", status_code=201)
def report_issue(body: IssueIn, ctx: Ctx = Depends(path_ctx(P.REPORT_ISSUE, writable=True))):
    ver = _version(ctx)
    activity_uid = stage_uid = None
    with actor_tx(ctx.actor, readonly=True) as c:
        if body.activity_id:
            r = c.execute("select activity_uid from baseline_activities where version_id = %s and external_activity_id = %s", (ver, body.activity_id)).fetchone()
            if r is None:
                raise ApiError(422, "ACTIVITY_NOT_FOUND", f"Activity '{body.activity_id}' is not part of this schedule version")
            activity_uid = r["activity_uid"]
        if body.stage_id:
            stage_uid = _id(body.stage_id, "Stage")
    res = di.report_issue(ctx.actor, title=body.title, category_code=body.category_code, activity_uid=activity_uid, stage_wbs_uid=stage_uid, description=body.description or None,
                          severity=body.severity, blocks_work=body.blocks_work, reported_date=body.reported_date, expected_duration_days=body.expected_duration_days)
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_issues(c, ctx, "i.issue_id = %(id)s", {"id": res["issue_id"]})[0]


@router.get("/schedules/{schedule_id}/issues")
def list_issues(status: str = "ALL", mine: bool = False, ctx: Ctx = Depends(path_ctx(P.VIEW_ISSUES))):
    where, params = "true", {}
    if status in ("ACTIVE", "RESOLVED"):
        where, params = "i.status = %(st)s", {"st": status}
    if mine:
        where += " and i.reported_by = %(user)s"
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_issues(c, ctx, where, params)


@router.get("/schedules/{schedule_id}/issues/{issue_id}")
def get_issue(issue_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_ISSUES))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = load_issues(c, ctx, "i.issue_id = %(id)s", {"id": _id(issue_id, "Issue")})
    if not rows:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Issue '{issue_id}' not found.")
    return rows[0]


class ResolveIn(BaseModel):
    resolution_notes: str
    cause: Optional[str] = None
    outcome: Optional[str] = None
    lessons_learned: Optional[str] = None
    add_to_memory: bool = False
    share_with_organisation: bool = False


@router.post("/schedules/{schedule_id}/issues/{issue_id}/resolve")
def resolve_issue(issue_id: str, body: ResolveIn, ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    iid = _id(issue_id, "Issue")
    di.resolve_issue(ctx.actor, iid, body.resolution_notes)
    if body.add_to_memory:
        lessons = (body.lessons_learned or body.cause or body.resolution_notes).strip()
        di.promote_to_memory(ctx.actor, iid, lessons_learned=lessons, corrective_action=body.resolution_notes.strip(), outcome=body.outcome,
                             visibility="ORGANISATION" if body.share_with_organisation else "PROJECT")
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_issues(c, ctx, "i.issue_id = %(id)s", {"id": iid})[0]


@router.post("/schedules/{schedule_id}/issues/{issue_id}/evidence")
def add_evidence(issue_id: str, file: UploadFile = File(...), notes: Optional[str] = Form(None), ctx: Ctx = Depends(path_ctx(P.UPLOAD_EVIDENCE, writable=True))):
    iid = _id(issue_id, "Issue")
    data = file.file.read()
    try:
        doc = docsvc.upload_document(ctx.user, ctx.project_id, ctx.access.role, "EVIDENCE", file.filename or "evidence", data)["document_id"]
    except ApiError as e:
        if e.code == "DUPLICATE_UPLOAD" and e.details:
            doc = uuid.UUID(e.details["document_id"])
        else:
            raise
    di.attach_issue_evidence(ctx.actor, iid, doc)
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_issues(c, ctx, "i.issue_id = %(id)s", {"id": iid})[0]


# ---------------------------------------------------------------------------------------------------------------------------------- root causes
_RC_SQL = """
select rc.root_cause_id, rc.category_code, cat.name as category_name, rc.title, rc.summary, rc.status, rc.identified_at,
       count(i.issue_id)::int as issue_count, count(i.issue_id) filter (where i.status = 'ACTIVE')::int as open_issue_count,
       coalesce(array_agg(distinct ba.external_activity_id) filter (where ba.external_activity_id is not null), '{{}}') as activity_ids,
       coalesce(array_agg(distinct stg.wbs_name) filter (where stg.wbs_name is not null), '{{}}') as stage_names,
       coalesce(sum(i.expected_duration_days), 0)::float as expected_delay_days
  from root_causes rc join issue_categories cat on cat.code = rc.category_code
  left join issues i on i.root_cause_id = rc.root_cause_id
  left join baseline_activities ba on ba.version_id = %(ver)s and ba.activity_uid = i.activity_uid
  left join schedule_wbs sw on sw.wbs_id = ba.wbs_id
  left join lateral (select st.wbs_name from schedule_wbs st where st.version_id = %(ver)s and st.node_type = 'STAGE'
                      and (st.wbs_uid = i.stage_wbs_uid or (i.stage_wbs_uid is null and sw.wbs_path like st.wbs_path || '%%')) order by length(st.wbs_path) desc limit 1) stg on true
 where rc.project_id = %(project)s {extra}
 group by rc.root_cause_id, cat.name order by rc.identified_at desc, rc.root_cause_id
"""


def _rc_rows(c, ctx: Ctx, rc_id=None) -> List[Dict[str, Any]]:
    rows = c.execute(_RC_SQL.format(extra="and rc.root_cause_id = %(rc)s" if rc_id else ""), {"ver": _version(ctx), "project": ctx.project_id, "rc": rc_id}).fetchall()
    return [{"root_cause_id": str(r["root_cause_id"]), "project_id": str(ctx.project_id), "category_code": r["category_code"], "category_name": r["category_name"], "title": r["title"],
             "summary": r["summary"], "status": r["status"], "identified_at": shapes.iso(r["identified_at"]), "issue_count": r["issue_count"], "open_issue_count": r["open_issue_count"],
             "activity_ids": list(r["activity_ids"] or []), "stage_names": list(r["stage_names"] or []), "expected_delay_days": r["expected_delay_days"]} for r in rows]


class RootCauseIn(BaseModel):
    category_code: str
    title: str
    summary: Optional[str] = None
    issue_ids: List[str] = []


class LinkIn(BaseModel):
    issue_ids: List[str]


@router.get("/schedules/{schedule_id}/root-causes")
def list_root_causes(ctx: Ctx = Depends(path_ctx(P.VIEW_ROOT_CAUSES))):
    with actor_tx(ctx.actor, readonly=True) as c:
        return _rc_rows(c, ctx)


@router.post("/schedules/{schedule_id}/root-causes", status_code=201)
def create_root_cause(body: RootCauseIn, ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    rid = di.create_root_cause(ctx.actor, title=body.title, category_code=body.category_code, summary=body.summary)["root_cause_id"]
    for i in body.issue_ids:
        di.assign_root_cause(ctx.actor, _id(i, "Issue"), rid)
    with actor_tx(ctx.actor, readonly=True) as c:
        return _rc_rows(c, ctx, rid)[0]


@router.post("/schedules/{schedule_id}/root-causes/{root_cause_id}/issues")
def link_issues(root_cause_id: str, body: LinkIn, ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    rid = _id(root_cause_id, "Root cause")
    for i in body.issue_ids:
        di.assign_root_cause(ctx.actor, _id(i, "Issue"), rid)
    with actor_tx(ctx.actor, readonly=True) as c:
        return _rc_rows(c, ctx, rid)[0]


@router.get("/schedules/{schedule_id}/root-cause-analysis")
def analysis(ctx: Ctx = Depends(path_ctx(P.VIEW_ROOT_CAUSES))):
    """deterministic: issues by category with the repeated-pattern flag (3+ issues of one category across 2+ activities), the identified root causes, and the stages carrying the most open delay"""
    ver = _version(ctx)
    base = ("from issues i join issue_categories cat on cat.code = i.category_code left join baseline_activities ba on ba.version_id = %(ver)s and ba.activity_uid = i.activity_uid "
            "left join schedule_wbs sw on sw.wbs_id = ba.wbs_id left join lateral (select st.wbs_uid, st.wbs_name, st.sequence from schedule_wbs st where st.version_id = %(ver)s and st.node_type = 'STAGE' "
            "and (st.wbs_uid = i.stage_wbs_uid or (i.stage_wbs_uid is null and sw.wbs_path like st.wbs_path || '%%')) order by length(st.wbs_path) desc limit 1) stg on true where i.project_id = %(project)s")
    p = {"ver": ver, "project": ctx.project_id}
    with actor_tx(ctx.actor, readonly=True) as c:
        cats = c.execute("select i.category_code, cat.name as category_name, count(*)::int as issue_count, count(*) filter (where i.status = 'ACTIVE')::int as open_count, "
                         "count(distinct i.activity_uid)::int as activity_count, count(distinct stg.wbs_uid)::int as stage_count, coalesce(sum(i.expected_duration_days), 0)::float as expected_delay_days, "
                         "count(*) filter (where i.root_cause_id is not null)::int as linked_to_root_cause, "
                         "coalesce(array_agg(distinct ba.external_activity_id) filter (where ba.external_activity_id is not null), '{}') as activity_ids, "
                         "coalesce(array_agg(distinct stg.wbs_name) filter (where stg.wbs_name is not null), '{}') as stage_names " + base +
                         " group by i.category_code, cat.name order by count(*) desc, cat.name", p).fetchall()
        delayed = c.execute("select stg.wbs_uid as stage_id, stg.wbs_name as stage_name, count(*)::int as issue_count, count(*) filter (where i.status = 'ACTIVE')::int as open_count, "
                            "coalesce(sum(i.expected_duration_days) filter (where i.status = 'ACTIVE'), 0)::float as open_expected_delay_days " + base +
                            " and stg.wbs_uid is not null group by stg.wbs_uid, stg.wbs_name, stg.sequence order by open_expected_delay_days desc, stg.sequence", p).fetchall()
        rcs = _rc_rows(c, ctx)
    categories = [{**{k: v for k, v in r.items() if k != "activity_ids" and k != "stage_names"}, "activity_ids": list(r["activity_ids"] or []), "stage_names": list(r["stage_names"] or []),
                   "is_repeated_pattern": r["issue_count"] >= 3 and r["activity_count"] >= 2} for r in cats]
    return {"project_id": str(ctx.project_id), "schedule_id": str(ver), "total_issues": sum(x["issue_count"] for x in categories), "open_issues": sum(x["open_count"] for x in categories),
            "categories": categories, "root_causes": rcs, "delayed_stages": [{**d, "stage_id": str(d["stage_id"])} for d in delayed]}


# ---------------------------------------------------------------------------------------------------------------------------------- institutional memory
def _memory_records(c, ctx: Ctx, category: Optional[str] = None):
    """v2 institutional_memory -> the original MemoryRecord (own project + lessons other projects chose to share)"""
    from backend.memory.schemas import MemoryRecord
    rows = c.execute(
        "select m.*, p.project_name, ba.external_activity_id as ext, d.code as disc from institutional_memory m join projects p on p.project_id = m.project_id "
        "left join baseline_activities ba on ba.activity_uid = m.activity_uid and ba.version_id = %s left join disciplines d on d.code = m.discipline_code "
        "where (m.project_id = %s or m.visibility = 'ORGANISATION') and (%s::text is null or upper(m.category_code) = upper(%s)) order by m.recorded_at desc limit 200",
        (_version(ctx), ctx.project_id, category, category)).fetchall()
    out = []
    for r in rows:
        parts = [x for x in (r["narrative"], f"Root Cause: {r['root_cause']}" if r["root_cause"] else None, f"Outcome: {r['outcome']}" if r["outcome"] else None,
                             f"Corrective Action: {r['corrective_action']}" if r["corrective_action"] else None, f"Lessons Learned: {r['lessons_learned']}") if x]
        out.append(MemoryRecord(
            memory_id=str(r["memory_id"]), project_id=r["project_id"], activity_id=r["ext"], title=r["title"] or "Incident", summary=r["lessons_learned"] or r["root_cause"],
            content="\n\n".join(parts) or r["title"], incident_type=r["category_code"], status=None,
            metadata={"discipline": r["discipline_code"], "delay_days": shapes.num(r["delay_days"]), "category_code": r["category_code"], "root_cause": r["root_cause"],
                      "resolution": r["corrective_action"], "outcome": r["outcome"], "lessons_learned": r["lessons_learned"], "source": r["source"],
                      "issue_id": str(r["issue_id"]) if r["issue_id"] else None, "project_name": r["project_name"], "scope": "PROJECT" if r["project_id"] == ctx.project_id else "ORGANISATION",
                      "shared_from_other_project": r["project_id"] != ctx.project_id},
            source_type="INSTITUTIONAL_INCIDENT", source_reference=str(r["memory_id"]), created_at=r["recorded_at"]))
    return out


def _rank(records, query, filters, top_k):
    from backend.memory.adapters import SentenceTransformersAdapter
    from backend.memory.ranking import rank_candidates
    ranked, mode = rank_candidates(candidates=records, query_text=query, filters=filters, encoder=SentenceTransformersAdapter(), top_k=max(1, min(top_k, 100)))
    return ranked, mode


def _response(query, ranked, total, mode, applied):
    return {"query": query, "results": [r.model_dump(mode="json") for r in ranked], "total_candidates": total, "retrieval_mode": mode, "filters_applied": applied}


class ForIssueIn(BaseModel):
    category_code: str
    query: str = ""
    activity_id: Optional[str] = None
    stage_id: Optional[str] = None
    top_k: int = 5


@router.post("/memory/for-issue")
def memory_for_issue(body: ForIssueIn, ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT))):
    """historical incidents relevant to an issue: same category (own project + shared lessons), ranked by text similarity with bonuses for the same activity / stage"""
    from backend.memory.schemas import MemoryFilter
    with actor_tx(ctx.actor, readonly=True) as c:
        recs = _memory_records(c, ctx, body.category_code)
    f = MemoryFilter(incident_type=body.category_code, activity_id=body.activity_id)
    ranked, mode = _rank(recs, body.query, f, body.top_k)
    return _response(body.query, ranked, len(recs), mode, {"category_code": body.category_code})


@router.get("/memory")
def browse_memory(q: str = "", category: Optional[str] = None, limit: int = 50, ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT))):
    from backend.memory.schemas import MemoryFilter
    with actor_tx(ctx.actor, readonly=True) as c:
        recs = _memory_records(c, ctx, category)
    ranked, mode = _rank(recs, q, MemoryFilter(incident_type=category.upper()) if category else None, limit)
    return _response(q, ranked, len(recs), mode, {"incident_type": category} if category else {})


@router.post("/memory/search")
def search_memory(body: Dict[str, Any], ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT))):
    from backend.memory.schemas import MemoryFilter
    f = body.get("filters") or {}
    if f.get("project_id") and str(f["project_id"]) != str(ctx.project_id):
        raise ApiError(403, "PROJECT_ACCESS_DENIED", "Cross-project filter violation: you are authorised for the selected project only")
    with actor_tx(ctx.actor, readonly=True) as c:
        recs = _memory_records(c, ctx, f.get("incident_type"))
    flt = MemoryFilter(**{k: v for k, v in f.items() if k in MemoryFilter.model_fields and k != "project_id"}) if f else None
    ranked, mode = _rank(recs, body.get("query", ""), flt, int(body.get("top_k", 10)))
    return _response(body.get("query", ""), ranked, len(recs), mode, f)


class MemoryIn(BaseModel):
    category_code: str
    title: str
    narrative: str
    root_cause: Optional[str] = None
    resolution: Optional[str] = None
    outcome: Optional[str] = None
    lessons_learned: Optional[str] = None
    share_with_organisation: bool = False


@router.post("/memory", status_code=201)
def record_memory(body: MemoryIn, ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    """record a lesson manually (Supervisor)"""
    if ctx.access.role != SUP:
        raise ApiError(403, "PERMISSION_DENIED", "Only a Supervisor records institutional memory")
    with actor_tx(ctx.actor, write=True) as c:
        from .. import audit
        r = c.execute("insert into institutional_memory (project_id, category_code, title, narrative, root_cause, corrective_action, lessons_learned, outcome, visibility, source, recorded_by) "
                      "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL',%s) returning memory_id",
                      (ctx.project_id, body.category_code, body.title.strip(), body.narrative, body.root_cause, body.resolution, (body.lessons_learned or body.narrative).strip(), body.outcome,
                       "ORGANISATION" if body.share_with_organisation else "PROJECT", ctx.user.id)).fetchone()
        audit.log(c, project_id=ctx.project_id, actor_id=ctx.user.id, role=SUP, action="MEMORY_RECORDED", entity_type="MEMORY", entity_id=r["memory_id"],
                  after={"visibility": "ORGANISATION" if body.share_with_organisation else "PROJECT", "source": "MANUAL"})
    return {"memory_id": str(r["memory_id"])}


class PromoteIn(BaseModel):
    cause: Optional[str] = None
    outcome: Optional[str] = None
    lessons_learned: Optional[str] = None
    share_with_organisation: bool = False


@router.post("/schedules/{schedule_id}/issues/{issue_id}/memory", status_code=201)
def promote_to_memory(issue_id: str, body: PromoteIn = PromoteIn(), ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    """store a RESOLVED issue as institutional knowledge"""
    iid = _id(issue_id, "Issue")
    with actor_tx(ctx.actor, readonly=True) as c:
        row = load_issues(c, ctx, "i.issue_id = %(id)s", {"id": iid})
    if not row:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Issue '{issue_id}' not found.")
    lessons = (body.lessons_learned or body.cause or row[0]["resolution_notes"] or "").strip()
    out = di.promote_to_memory(ctx.actor, iid, lessons_learned=lessons, corrective_action=row[0]["resolution_notes"], outcome=body.outcome,
                               visibility="ORGANISATION" if body.share_with_organisation else "PROJECT")
    return {"memory_id": str(out["memory_id"])}
