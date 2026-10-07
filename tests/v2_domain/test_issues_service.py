"""Issues and delays: report (engineer or supervisor), triage and resolve (supervisor), root causes, memory; the PM reads only."""
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from backend.v2.domain import issues, rollups
from backend.v2.errors import ApiError
from domainkit import TODAY, make_doc
from v2api import connect


class Raises:
    def __init__(self, code, status=None): self.code, self.status = code, status
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        assert et is ApiError, f"expected ApiError {self.code}, got {et}: {ev}"
        assert ev.code == self.code, f"expected {self.code}, got {ev.code}: {ev.message}"
        if self.status:
            assert ev.status == self.status
        return True


raises = Raises


def stage_uid(kit):
    with connect() as c:
        return c.execute("select wbs_uid from schedule_wbs w join schedule_versions v on v.version_id=w.version_id where v.project_id=%s and v.status='ACTIVE' and w.node_type='STAGE' order by w.wbs_path limit 1", (kit.project,)).fetchone()["wbs_uid"]


def report(kit, by=None, **kw):
    kw.setdefault("title", "Excavator broke down"); kw.setdefault("category_code", "EQUIPMENT_SHORTAGE"); kw.setdefault("activity_uid", kit.uid("A1010"))
    return issues.report_issue(by or kit.se, **kw)


def test_an_engineer_reports_a_delay_with_dates_estimate_and_evidence(kit):
    ev = make_doc(kit, "ISSUE_REPORT")
    r = report(kit, description="Hydraulic pump failed; spare from Guwahati", severity="HIGH", blocks_work=True, delay_started_on=TODAY - timedelta(days=2),
               impact_days_estimated=4, expected_duration_days=4, evidence_document_ids=[ev])
    got = issues.get_issue(kit.se, r["issue_id"])
    assert got["status"] == "ACTIVE" and got["severity"] == "HIGH" and got["impact_days_estimated"] == D("4.00") and got["delay_started_on"] == TODAY - timedelta(days=2)
    assert [e["document_id"] for e in got["evidence"]] == [ev] and got["reported_by"] == kit.world.se.id
    with connect() as c:
        assert c.execute("select action from audit_logs where entity_type = 'ISSUE'").fetchone()["action"] == "ISSUE_REPORTED"


def test_blocked_is_derived_from_active_blocking_issues_and_is_never_stored(kit):
    blocking = report(kit, title="Access road flooded", category_code="WEATHER", activity_uid=kit.uid("A1010"))
    report(kit, title="Paperwork delay, work continues", category_code="PERMIT_APPROVAL", activity_uid=kit.uid("A1000"), blocks_work=False)
    stage = report(kit, title="Whole stage on hold", category_code="SITE_ACCESS", activity_uid=None, stage_wbs_uid=stage_uid(kit))
    b = issues.active_blockers(kit.sup)
    assert b["blocked_activities"] == [str(kit.uid("A1010"))] and b["blocked_stages"] == [str(stage_uid(kit))]
    issues.resolve_issue(kit.sup, blocking["issue_id"], "Road reopened after drainage")
    assert issues.active_blockers(kit.sup)["blocked_activities"] == []
    with connect() as c:
        assert not c.execute("select 1 from information_schema.columns where column_name in ('blocked','is_blocked')").fetchall()


def test_reports_are_validated(kit):
    for code, kw in [("TARGET_REQUIRED", dict(activity_uid=None)), ("BAD_SEVERITY", dict(severity="SEVERE")), ("TITLE_REQUIRED", dict(title="x")), ("BAD_CATEGORY", dict(category_code="GREMLINS")),
                     ("ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", dict(activity_uid=uuid.uuid4())), ("STAGE_NOT_IN_ACTIVE_SCHEDULE", dict(activity_uid=None, stage_wbs_uid=uuid.uuid4())),
                     ("DOCUMENT_NOT_FOUND", dict(evidence_document_ids=[uuid.uuid4()])), ("CLAIM_NOT_FOUND", dict(source_event_id=uuid.uuid4()))]:
        with raises(code):
            report(kit, **kw)
    assert kit.count("issues") == 0


def test_who_can_report_and_who_sees_what(kit):
    mine = report(kit, title="Mine")["issue_id"]
    theirs = report(kit, title="Theirs", by=kit.se2)["issue_id"]
    sup_own = report(kit, title="Supervisor spotted this", by=kit.sup)["issue_id"]
    with raises("PERMISSION_DENIED", 403):
        report(kit, by=kit.pm)
    assert {i["issue_id"] for i in issues.list_issues(kit.se)} == {mine}
    assert {i["issue_id"] for i in issues.list_issues(kit.sup)} == {mine, theirs, sup_own}
    assert {i["issue_id"] for i in issues.list_issues(kit.pm)} == {mine, theirs, sup_own}              # read-only for the PM
    with raises("ISSUE_NOT_FOUND", 404):
        issues.get_issue(kit.se, theirs)
    assert issues.get_issue(kit.pm, theirs)["title"] == "Theirs"


def test_only_a_supervisor_resolves_and_a_resolved_issue_is_history(kit):
    r = report(kit, delay_started_on=TODAY - timedelta(days=3), impact_days_estimated=5)
    for who in (kit.se, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            issues.resolve_issue(who, r["issue_id"], "I fixed it")
    with raises("NOTES_REQUIRED", 422):
        issues.resolve_issue(kit.sup, r["issue_id"], " ")
    with raises("BAD_DATES", 422):
        issues.resolve_issue(kit.sup, r["issue_id"], "done", delay_ended_on=TODAY - timedelta(days=10))
    issues.resolve_issue(kit.sup, r["issue_id"], "Spare pump fitted", delay_ended_on=TODAY, impact_days_actual=3)
    got = issues.get_issue(kit.sup, r["issue_id"])
    assert got["status"] == "RESOLVED" and got["resolved_by"] == kit.world.sup.id and got["delay_ended_on"] == TODAY and got["impact_days_actual"] == 3 and got["impact_days_estimated"] == 5
    with raises("ALREADY_RESOLVED", 409):
        issues.resolve_issue(kit.sup, r["issue_id"], "again")
    with raises("ALREADY_RESOLVED", 409):
        issues.attach_issue_evidence(kit.se, r["issue_id"], make_doc(kit, "EVIDENCE"))
    with connect() as c:
        assert c.execute("select notification_type, recipient_id from notifications where issue_id = %s", (r["issue_id"],)).fetchone() == {"notification_type": "ISSUE_UPDATE", "recipient_id": kit.world.se.id}
        import psycopg
        c.execute("select set_config('app.system','on',false)")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("update issues set status='ACTIVE', resolved_by=null, resolved_at=null where issue_id=%s", (r["issue_id"],))       # the database also refuses to reopen


def test_evidence_can_be_added_by_the_reporter_while_active(kit):
    r = report(kit)
    d = make_doc(kit, "PHOTO")
    issues.attach_issue_evidence(kit.se, r["issue_id"], d)
    with raises("ISSUE_NOT_FOUND", 404):
        issues.attach_issue_evidence(kit.se2, r["issue_id"], make_doc(kit, "PHOTO"))
    with raises("DOCUMENT_NOT_FOUND", 422):
        issues.attach_issue_evidence(kit.se, r["issue_id"], uuid.uuid4())
    assert [e["document_id"] for e in issues.get_issue(kit.sup, r["issue_id"])["evidence"]] == [d]


def test_root_causes_group_issues_and_resolved_issues_become_institutional_memory(kit):
    a, b = report(kit, title="Pump failure 1")["issue_id"], report(kit, title="Pump failure 2", activity_uid=kit.uid("A1000"))["issue_id"]
    for who in (kit.se, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            issues.create_root_cause(who, title="Ageing equipment", category_code="EQUIPMENT_SHORTAGE")
    rc = issues.create_root_cause(kit.sup, title="Ageing hired excavators", category_code="EQUIPMENT_SHORTAGE", summary="Two breakdowns in one month")["root_cause_id"]
    issues.assign_root_cause(kit.sup, a, rc); issues.assign_root_cause(kit.sup, b, rc)
    with raises("ROOT_CAUSE_NOT_FOUND", 404):
        issues.assign_root_cause(kit.sup, a, uuid.uuid4())
    with raises("ISSUE_NOT_RESOLVED", 409):
        issues.promote_to_memory(kit.sup, a, lessons_learned="Hire newer machines")
    issues.resolve_issue(kit.sup, a, "Replaced", impact_days_actual=2)
    with raises("PERMISSION_DENIED", 403):
        issues.promote_to_memory(kit.pm, a, lessons_learned="x lesson")
    with raises("BAD_VISIBILITY", 422):
        issues.promote_to_memory(kit.sup, a, lessons_learned="Hire newer machines", visibility="WORLD")
    m = issues.promote_to_memory(kit.sup, a, lessons_learned="Inspect hired excavators before mobilisation", corrective_action="Pre-hire inspection checklist",
                                 outcome="No repeat in 90 days", visibility="ORGANISATION")
    mem = issues.list_memory(kit.pm)
    assert [x["memory_id"] for x in mem] == [m["memory_id"]] and mem[0]["delay_days"] == 2 and mem[0]["visibility"] == "ORGANISATION" and mem[0]["lessons_learned"].startswith("Inspect")
    assert {i["root_cause_id"] for i in issues.list_issues(kit.sup)} == {rc}


def test_issues_do_not_change_progress_and_archived_projects_are_read_only(kit, api):
    report(kit)
    assert kit.project_pct() == 0 and kit.ledger() == ([], [])
    s = rollups.project_summary(kit.pm)
    assert s["issues"] == {"active": 1, "blocking": 1, "resolved": 0}
    api.post(f"/api/v2/projects/{kit.project}/archive", kit.world.pm)
    with raises("PROJECT_ARCHIVED", 409):
        report(kit, title="After archive")
    assert len(issues.list_issues(kit.sup)) == 1                                                       # still readable
