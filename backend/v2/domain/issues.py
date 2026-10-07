"""Issues and delays (Site Engineer or Supervisor report; Supervisor resolves, groups under root causes, promotes lessons to memory).
The Project Manager reads them but never changes them. "Blocked" is DERIVED from active blocking issues, never stored. A resolved issue is
history: it is never reopened (raise a new one)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from .. import audit
from ..db import tx
from ..errors import ApiError
from .common import actor_tx, PM, SE, SUP, ProjectActor, active_version, notify, require_role, require_writable, supervisors_of

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _issue(c, project_id, issue_id, lock=False) -> dict:
    r = c.execute("select * from issues where project_id = %s and issue_id = %s" + (" for update" if lock else ""), (project_id, issue_id)).fetchone()
    if r is None:
        raise ApiError(404, "ISSUE_NOT_FOUND", "No such issue")
    return r


def _visible(actor: ProjectActor, issue: dict) -> None:
    if actor.role == SE and issue["reported_by"] != actor.user_id:
        raise ApiError(404, "ISSUE_NOT_FOUND", "No such issue")                    # an engineer sees the issues they raised


def report_issue(actor: ProjectActor, *, title: str, category_code: str, activity_uid=None, stage_wbs_uid=None, description: Optional[str] = None, severity: str = "MEDIUM",
                 blocks_work: bool = True, reported_date: Optional[date] = None, expected_duration_days=None, delay_started_on: Optional[date] = None,
                 impact_days_estimated=None, evidence_document_ids: Optional[List[uuid.UUID]] = None, source_event_id=None) -> Dict[str, Any]:
    require_role(actor, SE, SUP, what="reporting an issue")
    require_writable(actor)
    if severity not in SEVERITIES:
        raise ApiError(422, "BAD_SEVERITY", f"severity must be one of {', '.join(SEVERITIES)}")
    if activity_uid is None and stage_wbs_uid is None:
        raise ApiError(422, "TARGET_REQUIRED", "An issue must name an activity or a stage")
    if len((title or "").strip()) < 3:
        raise ApiError(422, "TITLE_REQUIRED", "An issue needs a title")
    with actor_tx(actor, write=True) as c:
        ver = active_version(c, actor.project_id)
        if activity_uid is not None and c.execute("select 1 from baseline_activities where version_id = %s and activity_uid = %s", (ver["version_id"], activity_uid)).fetchone() is None:
            raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "That activity is not part of the project's active schedule version")
        if stage_wbs_uid is not None and c.execute("select 1 from schedule_wbs where version_id = %s and wbs_uid = %s and node_type = 'STAGE'", (ver["version_id"], stage_wbs_uid)).fetchone() is None:
            raise ApiError(409, "STAGE_NOT_IN_ACTIVE_SCHEDULE", "That is not a stage of the active schedule version")
        if c.execute("select 1 from issue_categories where code = %s", (category_code,)).fetchone() is None:
            raise ApiError(422, "BAD_CATEGORY", "Unknown issue category")
        if source_event_id is not None:
            ev = c.execute("select filed_by from execution_events where project_id = %s and event_id = %s", (actor.project_id, source_event_id)).fetchone()
            if ev is None or (actor.role == SE and ev["filed_by"] != actor.user_id):
                raise ApiError(404, "CLAIM_NOT_FOUND", "No such claim")
        row = c.execute("insert into issues (project_id, activity_uid, stage_wbs_uid, category_code, title, description, severity, reported_date, expected_duration_days, blocks_work, "
                        "source_event_id, reported_by, delay_started_on, impact_days_estimated) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning issue_id",
                        (actor.project_id, activity_uid, stage_wbs_uid, category_code, title.strip(), description, severity, reported_date or date.today(), expected_duration_days,
                         blocks_work, source_event_id, actor.user_id, delay_started_on, impact_days_estimated)).fetchone()
        iid = row["issue_id"]
        for d_id in dict.fromkeys(evidence_document_ids or []):
            if c.execute("select 1 from source_documents where project_id = %s and document_id = %s", (actor.project_id, d_id)).fetchone() is None:
                raise ApiError(422, "DOCUMENT_NOT_FOUND", "An evidence document does not belong to this project")
            c.execute("insert into issue_evidence (issue_id, project_id, document_id) values (%s,%s,%s)", (iid, actor.project_id, d_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="ISSUE_REPORTED", entity_type="ISSUE", entity_id=iid, version_id=ver["version_id"],
                  after={"title": title.strip(), "category": category_code, "severity": severity, "blocks_work": blocks_work})
    return {"issue_id": iid, "status": "ACTIVE"}


def resolve_issue(actor: ProjectActor, issue_id, notes: str, delay_ended_on: Optional[date] = None, impact_days_actual=None) -> Dict[str, Any]:
    require_role(actor, SUP, what="resolving an issue")
    require_writable(actor)
    if len((notes or "").strip()) < 3:
        raise ApiError(422, "NOTES_REQUIRED", "Resolution notes are required")
    with actor_tx(actor, write=True) as c:
        issue = _issue(c, actor.project_id, issue_id, lock=True)
        if issue["status"] == "RESOLVED":
            raise ApiError(409, "ALREADY_RESOLVED", "The issue is already resolved; a resolved issue is never reopened")
        if delay_ended_on and issue["delay_started_on"] and delay_ended_on < issue["delay_started_on"]:
            raise ApiError(422, "BAD_DATES", "The delay ended before it started")
        c.execute("update issues set status = 'RESOLVED', resolved_by = %s, resolved_at = now(), resolution_notes = %s, delay_ended_on = coalesce(%s, delay_ended_on), "
                  "impact_days_actual = coalesce(%s, impact_days_actual) where project_id = %s and issue_id = %s",
                  (actor.user_id, notes.strip(), delay_ended_on, impact_days_actual, actor.project_id, issue_id))
        notify(c, project_id=actor.project_id, recipient_id=issue["reported_by"], ntype="ISSUE_UPDATE", issue_id=issue_id, created_by=actor.user_id, title="Issue resolved", body=notes.strip()[:300])
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="ISSUE_RESOLVED", entity_type="ISSUE", entity_id=issue_id,
                  before={"status": "ACTIVE"}, after={"status": "RESOLVED", "notes": notes.strip(), "impact_days_actual": str(impact_days_actual) if impact_days_actual is not None else None})
    return {"issue_id": issue_id, "status": "RESOLVED"}


def attach_issue_evidence(actor: ProjectActor, issue_id, document_id) -> Dict[str, Any]:
    require_role(actor, SE, SUP, what="attaching issue evidence")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        issue = _issue(c, actor.project_id, issue_id)
        _visible(actor, issue)
        if issue["status"] == "RESOLVED":
            raise ApiError(409, "ALREADY_RESOLVED", "Evidence cannot be added to a resolved issue")
        if c.execute("select 1 from source_documents where project_id = %s and document_id = %s", (actor.project_id, document_id)).fetchone() is None:
            raise ApiError(422, "DOCUMENT_NOT_FOUND", "That document does not belong to this project")
        c.execute("insert into issue_evidence (issue_id, project_id, document_id) values (%s,%s,%s) on conflict do nothing", (issue_id, actor.project_id, document_id))
    return {"issue_id": issue_id, "document_id": document_id}


def create_root_cause(actor: ProjectActor, *, title: str, category_code: str, summary: Optional[str] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what="managing root causes")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        r = c.execute("insert into root_causes (project_id, category_code, title, summary, identified_by) values (%s,%s,%s,%s,%s) returning root_cause_id", (actor.project_id, category_code, title.strip(), summary, actor.user_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="ROOT_CAUSE_CREATED", entity_type="ROOT_CAUSE", entity_id=r["root_cause_id"], after={"title": title.strip(), "category": category_code})
    return {"root_cause_id": r["root_cause_id"]}


def assign_root_cause(actor: ProjectActor, issue_id, root_cause_id) -> Dict[str, Any]:
    require_role(actor, SUP, what="grouping issues under a root cause")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        issue = _issue(c, actor.project_id, issue_id, lock=True)
        if c.execute("select 1 from root_causes where project_id = %s and root_cause_id = %s", (actor.project_id, root_cause_id)).fetchone() is None:
            raise ApiError(404, "ROOT_CAUSE_NOT_FOUND", "No such root cause in this project")
        c.execute("update issues set root_cause_id = %s where project_id = %s and issue_id = %s", (root_cause_id, actor.project_id, issue_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="ISSUE_GROUPED", entity_type="ISSUE", entity_id=issue_id,
                  before={"root_cause_id": str(issue["root_cause_id"]) if issue["root_cause_id"] else None}, after={"root_cause_id": str(root_cause_id)})
    return {"issue_id": issue_id, "root_cause_id": root_cause_id}


def promote_to_memory(actor: ProjectActor, issue_id, *, lessons_learned: str, corrective_action: Optional[str] = None, outcome: Optional[str] = None,
                      visibility: str = "PROJECT") -> Dict[str, Any]:
    require_role(actor, SUP, what="recording institutional memory")
    require_writable(actor)
    if visibility not in ("PROJECT", "ORGANISATION"):
        raise ApiError(422, "BAD_VISIBILITY", "visibility must be PROJECT or ORGANISATION")
    with actor_tx(actor, write=True) as c:
        issue = _issue(c, actor.project_id, issue_id, lock=True)
        if issue["status"] != "RESOLVED":
            raise ApiError(409, "ISSUE_NOT_RESOLVED", "Only a resolved issue can be promoted to institutional memory")
        r = c.execute("insert into institutional_memory (project_id, issue_id, activity_uid, category_code, title, narrative, root_cause, corrective_action, lessons_learned, outcome, "
                      "delay_days, visibility, source, recorded_by) values (%s,%s,%s,%s,%s,%s,(select title from root_causes where root_cause_id = %s),%s,%s,%s,%s,%s,'ISSUE_RESOLUTION',%s) returning memory_id",
                      (actor.project_id, issue_id, issue["activity_uid"], issue["category_code"], issue["title"], issue["description"], issue["root_cause_id"], corrective_action,
                       lessons_learned.strip(), outcome, issue["impact_days_actual"], visibility, actor.user_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="MEMORY_RECORDED", entity_type="MEMORY", entity_id=r["memory_id"], after={"issue_id": str(issue_id), "visibility": visibility})
    return {"memory_id": r["memory_id"]}


def list_issues(actor: ProjectActor, status: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[dict]:
    """Site Engineer: the issues they raised. Supervisor and Project Manager: all (read-only for the PM)."""
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select i.issue_id, i.title, i.category_code, i.severity, i.status, i.blocks_work, i.reported_date, i.activity_uid, i.stage_wbs_uid, i.root_cause_id, i.reported_by, "
                         "i.delay_started_on, i.delay_ended_on, i.impact_days_estimated, i.impact_days_actual, i.resolved_at from issues i "
                         "where i.project_id = %s and (%s::text is null or i.status = %s) and (%s::text <> 'SITE_ENGINEER' or i.reported_by = %s) order by i.reported_date desc, i.created_at desc limit %s offset %s",
                         (actor.project_id, status, status, actor.role, actor.user_id, min(limit, 500), offset)).fetchall()


def get_issue(actor: ProjectActor, issue_id) -> Dict[str, Any]:
    with actor_tx(actor, readonly=True) as c:
        issue = _issue(c, actor.project_id, issue_id)
        _visible(actor, issue)
        issue["evidence"] = c.execute("select d.document_id, d.kind, d.file_name from issue_evidence e join source_documents d on d.document_id = e.document_id where e.issue_id = %s", (issue_id,)).fetchall()
        return issue


def active_blockers(actor: ProjectActor) -> Dict[str, Any]:
    """the DERIVED blocked set: activities (and stages) with an ACTIVE blocking issue"""
    with actor_tx(actor, readonly=True) as c:
        rows = c.execute("select issue_id, activity_uid, stage_wbs_uid, title, severity from issues where project_id = %s and status = 'ACTIVE' and blocks_work", (actor.project_id,)).fetchall()
    return {"blocked_activities": sorted({str(r["activity_uid"]) for r in rows if r["activity_uid"]}), "blocked_stages": sorted({str(r["stage_wbs_uid"]) for r in rows if r["stage_wbs_uid"]}),
            "issues": rows}


def list_memory(actor: ProjectActor) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select memory_id, project_id, title, lessons_learned, corrective_action, outcome, category_code, delay_days, visibility, recorded_at from institutional_memory "
                         "where project_id = %s or (visibility = 'ORGANISATION') order by recorded_at desc", (actor.project_id,)).fetchall()


def list_root_causes(actor: ProjectActor) -> List[dict]:
    require_role(actor, SUP, PM, what="reading root causes")
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select r.root_cause_id, r.title, r.category_code, r.summary, r.status, r.identified_by, r.identified_at, "
                         "(select count(*) from issues i where i.project_id = r.project_id and i.root_cause_id = r.root_cause_id) as issues "
                         "from root_causes r where r.project_id = %s order by r.identified_at, r.root_cause_id", (actor.project_id,)).fetchall()
