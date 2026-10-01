"""
Issue service (SETUAI V7): report, resolve and analyse issues / delays, atomically with their audit record.

Rules
  * project + schedule scoped: the activity / stage / source claim must belong to the caller's project and schedule
  * REPORT_ISSUE (site engineer, supervisor, PM, owner) reports; MANAGE_BLOCKERS (supervisor, planner, PM, owner)
    resolves, groups issues under root causes and promotes them to institutional memory
  * an issue is history: it is resolved, never deleted
  * reporting / resolving never touches approved actuals or progress; BLOCKED is derived from ACTIVE blocking rows
  * resolving can store the issue as institutional knowledge in the same transaction (idempotent per issue)
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.base import BaseRepository
from backend.schemas.issue import IssueCreate, IssueResolve, RootCauseCreate
from backend.shared.audit import append_audit_record

_ISSUE_SQL = """
    SELECT i.issue_id, i.project_id, i.schedule_id, i.activity_id, sa.activity_name, sa.discipline,
           i.stage_id, st.stage_name, i.category_code, c.name AS category_name, i.title, i.description, i.severity,
           i.reported_date, i.expected_duration_days, i.blocks_work, i.status, i.source_event_id,
           i.reported_by, rp.full_name AS reported_by_name, i.created_at,
           i.resolved_by, vp.full_name AS resolved_by_name, i.resolved_at, i.resolution_notes,
           i.root_cause_id, rc.title AS root_cause_title,
           COALESCE((SELECT json_agg(json_build_object('document_id', d.document_id, 'file_name', d.file_name)
                                     ORDER BY ie.created_at)
                       FROM issue_evidence ie JOIN source_documents d ON d.document_id = ie.document_id
                      WHERE ie.issue_id = i.issue_id), '[]'::json) AS evidence,
           (SELECT m.incident_id FROM institutional_incidents m WHERE m.issue_id = i.issue_id) AS memory_incident_id
      FROM issues i
      JOIN issue_categories c ON c.code = i.category_code
      LEFT JOIN schedule_activities sa ON sa.schedule_id = i.schedule_id AND sa.activity_id = i.activity_id
      LEFT JOIN stages st ON st.stage_id = i.stage_id
      LEFT JOIN root_causes rc ON rc.root_cause_id = i.root_cause_id
      LEFT JOIN profiles rp ON rp.id = i.reported_by
      LEFT JOIN profiles vp ON vp.id = i.resolved_by
"""


def _jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool, list, dict)) else v) for k, v in row.items()}


def promote_issue_to_memory(
    cur: Any,
    issue: Dict[str, Any],
    *,
    recorded_by: Any,
    cause: Optional[str],
    outcome: Optional[str],
    lessons_learned: Optional[str],
    share_with_organisation: bool,
    source: str = "ISSUE_RESOLUTION",
) -> Optional[str]:
    """
    Store a RESOLVED issue as an institutional-memory record (one per issue; re-promoting is a no-op).
    `issue` is a row from _ISSUE_SQL. Works on any open cursor, so the API and the seeder share one implementation.
    """
    cur.execute(
        """
        INSERT INTO institutional_incidents (
            incident_id, project_id, stage_id, activity_id, schedule_id, discipline, incident_type, category_code,
            title, narrative, root_cause, delay_days, corrective_action, lessons_learned, outcome,
            recorded_by, recorded_at, status, issue_id, visibility, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                (%s::date - %s::date), %s, %s, %s, %s, COALESCE(%s, now()), 'CLOSED', %s, %s, %s)
        ON CONFLICT (issue_id) WHERE issue_id IS NOT NULL DO NOTHING
        RETURNING incident_id
        """,
        (
            uuid.uuid4(), issue["project_id"], issue["stage_id"], issue["activity_id"], issue["schedule_id"],
            issue.get("discipline"), issue["category_code"], issue["category_code"],
            issue["title"], issue["description"], cause,
            issue["resolved_at"], issue["reported_date"],
            issue["resolution_notes"], lessons_learned, outcome,
            recorded_by, issue["resolved_at"], issue["issue_id"],
            "ORGANISATION" if share_with_organisation else "PROJECT", source,
        ),
    )
    row = cur.fetchone()
    return str(row["incident_id"]) if row else None


class IssueService(BaseRepository):
    # ------------------------------------------------------------------ issues
    @classmethod
    def _get(cls, cur: Any, context: ScheduleContext, issue_id: Any) -> Optional[Dict[str, Any]]:
        cur.execute(_ISSUE_SQL + " WHERE i.issue_id = %s AND i.project_id = %s AND i.schedule_id = %s",
                    (issue_id, context.project_id, context.schedule_id))
        row = cur.fetchone()
        return dict(row) if row else None

    @classmethod
    def report_issue(cls, context: ScheduleContext, payload: IssueCreate) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.REPORT_ISSUE):
            raise_permission_denied(Permission.REPORT_ISSUE.value, context.role)
        issue_id = uuid.uuid4()
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                stage_id = payload.stage_id
                if payload.activity_id:
                    cur.execute(
                        "SELECT stage_id FROM schedule_activities WHERE schedule_id = %s AND project_id = %s AND activity_id = %s",
                        (context.schedule_id, context.project_id, payload.activity_id),
                    )
                    act = cur.fetchone()
                    if act is None:
                        raise_resource_not_found("Activity", payload.activity_id)
                    # the issue always carries the activity's stage, so stage-level root-cause analysis needs no join guesswork
                    if stage_id is not None and act["stage_id"] is not None and str(act["stage_id"]) != str(stage_id):
                        raise HTTPException(status_code=422, detail="stage_id does not match the stage of the affected activity")
                    stage_id = act["stage_id"] or stage_id
                if stage_id:
                    cur.execute(
                        "SELECT 1 FROM stages WHERE stage_id = %s AND project_id = %s AND schedule_id = %s",
                        (stage_id, context.project_id, context.schedule_id),
                    )
                    if cur.fetchone() is None:
                        raise_resource_not_found("Stage", str(stage_id))
                if payload.source_event_id:
                    cur.execute(
                        "SELECT 1 FROM execution_events WHERE event_id = %s AND project_id = %s AND schedule_id = %s",
                        (payload.source_event_id, context.project_id, context.schedule_id),
                    )
                    if cur.fetchone() is None:
                        raise_resource_not_found("Source field report", payload.source_event_id)
                cur.execute(
                    """
                    INSERT INTO issues (issue_id, project_id, schedule_id, activity_id, stage_id, category_code, title,
                                        description, severity, reported_date, expected_duration_days, blocks_work,
                                        source_event_id, reported_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s, %s, %s, %s)
                    """,
                    (issue_id, context.project_id, context.schedule_id, payload.activity_id, stage_id,
                     payload.category_code.value, payload.title.strip(), payload.description.strip(), payload.severity.value,
                     payload.reported_date, payload.expected_duration_days, payload.blocks_work, payload.source_event_id,
                     context.user_id),
                )
                row = cls._get(cur, context, issue_id)
                append_audit_record(
                    cur, entity_type="ISSUE", entity_id=str(issue_id), action="ISSUE_REPORTED",
                    actor_id=str(context.user_id), before_state=None, after_state=_jsonable(row),
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                conn.commit()
        return row

    @classmethod
    def get_issue(cls, context: ScheduleContext, issue_id: uuid.UUID) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
            raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                row = cls._get(cur, context, issue_id)
        if row is None:
            raise_resource_not_found("Issue", str(issue_id))
        return row

    @classmethod
    def list_issues(
        cls,
        context: ScheduleContext,
        status_filter: Optional[str] = "ALL",
        category: Optional[str] = None,
        activity_id: Optional[str] = None,
        stage_id: Optional[uuid.UUID] = None,
        mine: bool = False,
    ) -> List[Dict[str, Any]]:
        if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
            raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
        sql = _ISSUE_SQL + " WHERE i.project_id = %s AND i.schedule_id = %s"
        params: list = [context.project_id, context.schedule_id]
        if status_filter and status_filter.upper() != "ALL":
            sql += " AND i.status = %s"
            params.append(status_filter.upper())
        if category:
            sql += " AND i.category_code = %s"
            params.append(category.upper())
        if activity_id:
            sql += " AND i.activity_id = %s"
            params.append(activity_id)
        if stage_id:
            sql += " AND i.stage_id = %s"
            params.append(stage_id)
        if mine:
            sql += " AND i.reported_by = %s"
            params.append(context.user_id)
        sql += " ORDER BY (i.status = 'ACTIVE') DESC, i.reported_date DESC, i.issue_id"
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def resolve_issue(cls, context: ScheduleContext, issue_id: uuid.UUID, payload: IssueResolve) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.MANAGE_BLOCKERS):
            raise_permission_denied(Permission.MANAGE_BLOCKERS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM issues WHERE issue_id = %s AND project_id = %s AND schedule_id = %s FOR UPDATE",
                    (issue_id, context.project_id, context.schedule_id),
                )
                locked = cur.fetchone()
                if locked is None:
                    raise_resource_not_found("Issue", str(issue_id))
                if locked["status"] != "ACTIVE":
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Issue is already resolved")
                before = cls._get(cur, context, issue_id)
                cur.execute(
                    "UPDATE issues SET status = 'RESOLVED', resolved_by = %s, resolved_at = now(), resolution_notes = %s "
                    "WHERE issue_id = %s",
                    (context.user_id, payload.resolution_notes.strip(), issue_id),
                )
                after = cls._get(cur, context, issue_id)
                memory_id = None
                if payload.add_to_memory:
                    cause = payload.cause.strip() if payload.cause else (after.get("root_cause_title"))
                    memory_id = promote_issue_to_memory(
                        cur, after, recorded_by=context.user_id, cause=cause,
                        outcome=payload.outcome, lessons_learned=payload.lessons_learned,
                        share_with_organisation=payload.share_with_organisation,
                    )
                    after = cls._get(cur, context, issue_id)
                append_audit_record(
                    cur, entity_type="ISSUE", entity_id=str(issue_id), action="ISSUE_RESOLVED",
                    actor_id=str(context.user_id), before_state=_jsonable(before),
                    after_state={**_jsonable(after), "memory_incident_id": memory_id},
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                # tell the reporter (when it was someone else) that their issue was resolved
                if str(after["reported_by"]) != str(context.user_id):
                    cur.execute(
                        """
                        INSERT INTO notifications (project_id, recipient_id, notification_type, issue_id, title, body, created_by)
                        VALUES (%s, %s, 'ISSUE_UPDATE', %s, %s, %s, %s)
                        """,
                        (context.project_id, after["reported_by"], issue_id, f"Issue resolved: {after['title']}",
                         payload.resolution_notes.strip(), context.user_id),
                    )
                conn.commit()
        return after

    # ---------------------------------------------------------------- evidence
    @classmethod
    def attach_evidence(cls, context: ScheduleContext, issue_id: uuid.UUID, *, filename: str, stored_path: str,
                        file_hash: str, notes: Optional[str] = None) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.REPORT_ISSUE):
            raise_permission_denied(Permission.REPORT_ISSUE.value, context.role)
        document_id = f"DOC-{uuid.uuid4().hex[:12].upper()}"
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if cls._get(cur, context, issue_id) is None:
                    raise_resource_not_found("Issue", str(issue_id))
                cur.execute(
                    "INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash, project_id) "
                    "VALUES (%s, %s, 'ISSUE_EVIDENCE', %s, %s, %s)",
                    (document_id, filename, context.user_id, file_hash, context.project_id),
                )
                cur.execute(
                    "INSERT INTO issue_evidence (issue_id, document_id, notes) VALUES (%s, %s, %s)",
                    (issue_id, document_id, notes),
                )
                append_audit_record(
                    cur, entity_type="ISSUE", entity_id=str(issue_id), action="ISSUE_EVIDENCE_ADDED",
                    actor_id=str(context.user_id), before_state=None,
                    after_state={"document_id": document_id, "file_name": filename, "stored_path": stored_path},
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                row = cls._get(cur, context, issue_id)
                conn.commit()
        return row

    # -------------------------------------------------------------- root causes
    @classmethod
    def _root_cause_rows(cls, cur: Any, context: ScheduleContext, root_cause_id: Optional[Any] = None) -> List[Dict[str, Any]]:
        sql = """
            SELECT rc.root_cause_id, rc.project_id, rc.category_code, c.name AS category_name, rc.title, rc.summary,
                   rc.status, rc.identified_at,
                   count(i.issue_id)::int AS issue_count,
                   count(i.issue_id) FILTER (WHERE i.status = 'ACTIVE')::int AS open_issue_count,
                   COALESCE(array_agg(DISTINCT i.activity_id) FILTER (WHERE i.activity_id IS NOT NULL), '{}') AS activity_ids,
                   COALESCE(array_agg(DISTINCT st.stage_name) FILTER (WHERE st.stage_name IS NOT NULL), '{}') AS stage_names,
                   COALESCE(sum(i.expected_duration_days), 0)::float AS expected_delay_days
              FROM root_causes rc
              JOIN issue_categories c ON c.code = rc.category_code
              LEFT JOIN issues i ON i.root_cause_id = rc.root_cause_id AND i.schedule_id = %s
              LEFT JOIN stages st ON st.stage_id = i.stage_id
             WHERE rc.project_id = %s
        """
        params: list = [context.schedule_id, context.project_id]
        if root_cause_id is not None:
            sql += " AND rc.root_cause_id = %s"
            params.append(root_cause_id)
        sql += " GROUP BY rc.root_cause_id, c.name ORDER BY rc.identified_at DESC, rc.root_cause_id"
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    @classmethod
    def _link(cls, cur: Any, context: ScheduleContext, root_cause_id: Any, issue_ids: List[uuid.UUID]) -> None:
        if not issue_ids:
            return
        cur.execute(
            "SELECT issue_id FROM issues WHERE project_id = %s AND schedule_id = %s AND issue_id = ANY(%s)",
            (context.project_id, context.schedule_id, issue_ids),
        )
        found = {r["issue_id"] for r in cur.fetchall()}
        missing = [str(i) for i in issue_ids if i not in found]
        if missing:
            raise_resource_not_found("Issue", ", ".join(missing))
        cur.execute("UPDATE issues SET root_cause_id = %s WHERE issue_id = ANY(%s)", (root_cause_id, issue_ids))

    @classmethod
    def create_root_cause(cls, context: ScheduleContext, payload: RootCauseCreate) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.MANAGE_BLOCKERS):
            raise_permission_denied(Permission.MANAGE_BLOCKERS.value, context.role)
        root_cause_id = uuid.uuid4()
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO root_causes (root_cause_id, project_id, category_code, title, summary, identified_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (root_cause_id, context.project_id, payload.category_code.value, payload.title.strip(),
                     payload.summary, context.user_id),
                )
                cls._link(cur, context, root_cause_id, payload.issue_ids)
                row = cls._root_cause_rows(cur, context, root_cause_id)[0]
                append_audit_record(
                    cur, entity_type="ROOT_CAUSE", entity_id=str(root_cause_id), action="ROOT_CAUSE_IDENTIFIED",
                    actor_id=str(context.user_id), before_state=None,
                    after_state={**_jsonable(row), "issue_ids": [str(i) for i in payload.issue_ids]},
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                conn.commit()
        return row

    @classmethod
    def link_issues(cls, context: ScheduleContext, root_cause_id: uuid.UUID, issue_ids: List[uuid.UUID]) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.MANAGE_BLOCKERS):
            raise_permission_denied(Permission.MANAGE_BLOCKERS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if not cls._root_cause_rows(cur, context, root_cause_id):
                    raise_resource_not_found("Root cause", str(root_cause_id))
                cls._link(cur, context, root_cause_id, issue_ids)
                row = cls._root_cause_rows(cur, context, root_cause_id)[0]
                append_audit_record(
                    cur, entity_type="ROOT_CAUSE", entity_id=str(root_cause_id), action="ROOT_CAUSE_ISSUES_LINKED",
                    actor_id=str(context.user_id), before_state=None, after_state={"issue_ids": [str(i) for i in issue_ids]},
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                conn.commit()
        return row

    @classmethod
    def analysis(cls, context: ScheduleContext) -> Dict[str, Any]:
        """
        Deterministic root-cause view: issues grouped by category, with the repeated-pattern flag
        (3+ issues of one category across 2+ activities), plus the identified root causes and the stages carrying
        the most open delay. No inference: every number is a count or sum over stored issues.
        """
        if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
            raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.category_code, c.name AS category_name,
                           count(*)::int AS issue_count,
                           count(*) FILTER (WHERE i.status = 'ACTIVE')::int AS open_count,
                           count(DISTINCT i.activity_id)::int AS activity_count,
                           count(DISTINCT i.stage_id)::int AS stage_count,
                           COALESCE(sum(i.expected_duration_days), 0)::float AS expected_delay_days,
                           count(*) FILTER (WHERE i.root_cause_id IS NOT NULL)::int AS linked_to_root_cause,
                           COALESCE(array_agg(DISTINCT i.activity_id) FILTER (WHERE i.activity_id IS NOT NULL), '{}') AS activity_ids,
                           COALESCE(array_agg(DISTINCT st.stage_name) FILTER (WHERE st.stage_name IS NOT NULL), '{}') AS stage_names
                      FROM issues i
                      JOIN issue_categories c ON c.code = i.category_code
                      LEFT JOIN stages st ON st.stage_id = i.stage_id
                     WHERE i.project_id = %s AND i.schedule_id = %s
                     GROUP BY i.category_code, c.name
                     ORDER BY count(*) DESC, c.name
                    """,
                    (context.project_id, context.schedule_id),
                )
                categories = [dict(r) for r in cur.fetchall()]
                for c in categories:
                    c["is_repeated_pattern"] = c["issue_count"] >= 3 and c["activity_count"] >= 2
                cur.execute(
                    """
                    SELECT st.stage_id, st.stage_name, count(*)::int AS issue_count,
                           count(*) FILTER (WHERE i.status = 'ACTIVE')::int AS open_count,
                           COALESCE(sum(i.expected_duration_days) FILTER (WHERE i.status = 'ACTIVE'), 0)::float AS open_expected_delay_days
                      FROM issues i JOIN stages st ON st.stage_id = i.stage_id
                     WHERE i.project_id = %s AND i.schedule_id = %s
                     GROUP BY st.stage_id, st.stage_name, st.sequence_order
                     ORDER BY open_expected_delay_days DESC, st.sequence_order
                    """,
                    (context.project_id, context.schedule_id),
                )
                delayed = [dict(r) for r in cur.fetchall()]
                root_causes = cls._root_cause_rows(cur, context)
        return {
            "project_id": context.project_id,
            "schedule_id": context.schedule_id,
            "total_issues": sum(c["issue_count"] for c in categories),
            "open_issues": sum(c["open_count"] for c in categories),
            "categories": categories,
            "root_causes": root_causes,
            "delayed_stages": delayed,
        }

    @classmethod
    def list_root_causes(cls, context: ScheduleContext) -> List[Dict[str, Any]]:
        if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
            raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                return cls._root_cause_rows(cur, context)
