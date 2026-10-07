"""Role matrix over every domain service. The role comes from an ACTIVE membership of THIS project, re-verified inside each transaction;
the project manager never sees claim content, a site engineer only their own, and only a supervisor decides."""
import uuid
from datetime import timedelta

import pytest

from backend.v2.domain import claims, decisions, issues, rollups, timeline as tl
from backend.v2.domain.common import ProjectActor, resolve_actor
from domainkit import TODAY, make_doc
from test_issues_service import raises
from v2api import connect


@pytest.fixture
def claim(kit):
    return kit.submit("A2010", 100, text="authorization fixture")["claim_id"]


def roles(kit):
    return {"SE": kit.se, "SE2": kit.se2, "SUP": kit.sup, "PM": kit.pm}


def denied(fn, *a, **kw):
    with raises("PERMISSION_DENIED", 403):
        fn(*a, **kw)


def test_only_the_site_engineer_files_and_manages_their_own_claims(kit, claim):
    denied(kit.submit, "A2010", 100, by=kit.sup, text="supervisors cannot file")
    denied(kit.submit, "A2010", 100, by=kit.pm, text="project managers cannot file")
    for who in (kit.sup, kit.pm):
        denied(claims.withdraw_claim, who, claim, "not mine to withdraw")
        denied(claims.answer_clarification, who, claim, "an answer")
        denied(claims.attach_evidence, who, claim, make_doc(kit))
        denied(claims.list_my_claims, who)
    with raises("CLAIM_NOT_FOUND", 404):                                   # another engineer: not forbidden, simply not visible
        claims.withdraw_claim(kit.se2, claim, "not mine either")
    with raises("CLAIM_NOT_FOUND", 404):
        claims.attach_evidence(kit.se2, claim, make_doc(kit, by="se2"))
    assert kit.count("execution_events", "status = 'WITHDRAWN'") == 0 and kit.count("execution_events") == 1


def test_who_can_read_a_claim(kit, claim):
    assert claims.get_claim(kit.se, claim)["event_id"] == claim and claims.get_claim(kit.sup, claim)["event_id"] == claim
    with raises("CLAIM_NOT_FOUND", 404):
        claims.get_claim(kit.se2, claim)
    with raises("CLAIM_CONTENT_FORBIDDEN", 403):
        claims.get_claim(kit.pm, claim)
    assert [c["event_id"] for c in claims.list_my_claims(kit.se)] == [claim] and claims.list_my_claims(kit.se2) == []


def test_the_review_queue_and_decisions_belong_to_the_supervisor(kit, claim):
    for who in (kit.se, kit.se2, kit.pm):
        denied(claims.review_queue, who)
        denied(decisions.decide, who, claim, action="APPROVE")
        denied(decisions.preview_decision, who, claim)
        denied(claims.rematch_claim, who, claim, kit.uid("A2010"))
        denied(claims.bind_quantities, who, claim, {})
    assert [c["event_id"] for c in claims.review_queue(kit.sup)] == [claim]
    assert kit.count("planner_decisions") == 0 and kit.ledger() == ([], [])


def test_the_pm_sees_counts_and_never_content(kit, claim):
    c = claims.claim_counts(kit.pm)
    assert c["pending_total"] == 1 and set(c) >= {"APPROVED", "REJECTED", "WITHDRAWN", "pending_total"}
    assert claims.claim_counts(kit.sup)["pending_total"] == 1
    denied(claims.claim_counts, kit.se)
    kit.approve(claim)
    assert "claim_id" not in repr(rollups.project_summary(kit.pm)) and tl.activity_timeline(kit.pm, kit.uid("A2010"))["claims"] == []


def test_a_site_engineer_cannot_read_the_audit_trail_or_other_peoples_issues(kit):
    kit.approve(kit.submit("A2010", 50)["claim_id"])
    denied(tl.audit_trail, kit.se)
    denied(tl.verify_audit_chain, kit.se)
    theirs = issues.report_issue(kit.se2, title="Their issue", category_code="WEATHER", activity_uid=kit.uid("A1010"))["issue_id"]
    assert issues.list_issues(kit.se) == []
    with raises("ISSUE_NOT_FOUND", 404):
        issues.get_issue(kit.se, theirs)


def test_issue_management_is_supervisor_only(kit):
    i = issues.report_issue(kit.se, title="Mine", category_code="WEATHER", activity_uid=kit.uid("A1010"))["issue_id"]
    rc = issues.create_root_cause(kit.sup, title="Monsoon", category_code="WEATHER")["root_cause_id"]
    for who in (kit.se, kit.pm):
        denied(issues.resolve_issue, who, i, "resolved")
        denied(issues.create_root_cause, who, title="Another", category_code="WEATHER")
        denied(issues.assign_root_cause, who, i, rc)
    denied(issues.report_issue, kit.pm, title="PM cannot report", category_code="WEATHER", activity_uid=kit.uid("A1010"))


def test_nobody_outside_the_project_can_act_in_it(kit, claim):
    for outsider in (kit.world.outsider, kit.world.pm2):
        with raises("NOT_A_MEMBER", 403):
            resolve_actor(outsider.id, kit.project)
    forged = ProjectActor(kit.world.outsider.id, kit.project, "SUPERVISOR")        # a hand-built actor claiming a role it does not hold
    with raises("MEMBERSHIP_CHANGED", 403):
        decisions.decide(forged, claim, action="APPROVE")
    with raises("MEMBERSHIP_CHANGED", 403):
        claims.review_queue(forged)
    with raises("MEMBERSHIP_CHANGED", 403):
        rollups.project_summary(forged)
    assert kit.count("planner_decisions") == 0


def test_an_actor_cannot_borrow_a_role_they_hold_elsewhere(kit, claim):
    """pm2 is a project manager of ANOTHER project; presenting that identity with a supervisor role for this project is refused"""
    borrowed = ProjectActor(kit.world.pm2.id, kit.project, "SUPERVISOR")
    with raises("MEMBERSHIP_CHANGED", 403):
        decisions.decide(borrowed, claim, action="APPROVE")


def test_a_snapshot_taken_before_a_suspension_or_role_change_is_refused(kit, claim):
    stale_sup = kit.sup
    with connect(system=True) as c:
        c.execute("update project_memberships set status = 'SUSPENDED' where project_id = %s and user_id = %s", (kit.project, kit.world.sup.id))
    with raises("MEMBERSHIP_CHANGED", 403):
        decisions.decide(stale_sup, claim, action="APPROVE")
    with raises("MEMBERSHIP_CHANGED", 403):
        claims.review_queue(stale_sup)
    with raises("NOT_A_MEMBER", 403):
        resolve_actor(kit.world.sup.id, kit.project)
    with connect(system=True) as c:
        c.execute("update project_memberships set status = 'ACTIVE', role = 'SITE_ENGINEER' where project_id = %s and user_id = %s", (kit.project, kit.world.sup.id))
    with raises("MEMBERSHIP_CHANGED", 403):                                         # same user, different role now: the old snapshot is dead
        decisions.decide(stale_sup, claim, action="APPROVE")
    assert kit.count("planner_decisions") == 0 and kit.ledger() == ([], [])


def test_a_removed_engineer_cannot_withdraw_or_file(kit, claim):
    stale = kit.se
    with connect(system=True) as c:
        c.execute("update project_memberships set status = 'REMOVED' where project_id = %s and user_id = %s", (kit.project, kit.world.se.id))
    with raises("MEMBERSHIP_CHANGED", 403):
        claims.withdraw_claim(stale, claim, "after removal")
    with raises("MEMBERSHIP_CHANGED", 403):
        kit.submit("A2010", 10, by=stale, text="after removal")
    assert kit.count("execution_events") == 1 and kit.count("execution_events", "status = 'WITHDRAWN'") == 0


def test_a_claim_id_from_another_project_is_invisible(kit, api, claim):
    """a supervisor of project 2 gets 404 for project 1's claim, never its content"""
    w = kit.world
    from v2api import make_user
    sup2 = make_user("sup2")
    assert api.post(f"/api/v2/projects/{w.project2}/members", w.pm2, json={"email": sup2.email, "role": "SUPERVISOR"}).status_code == 201
    a = resolve_actor(sup2.id, w.project2)
    with raises("CLAIM_NOT_FOUND", 404):
        claims.get_claim(a, claim)
    with raises("CLAIM_NOT_FOUND", 404):
        decisions.decide(a, claim, action="REJECT", justification="not your project")
    assert kit.count("planner_decisions") == 0


def test_database_guards_stay_live_for_an_engineer_after_a_decision(kit, claim):
    """the services never run as system, so the DB guards are the backstop: an engineer's own session cannot rewrite a decided claim or forge ledger rows"""
    import psycopg
    kit.approve(claim)
    before = (kit.ledger(), kit.count("planner_decisions"))
    for sql in ("update execution_events set status = 'REJECTED' where event_id = %(e)s",
                "update execution_events set raw_claim_text = 'tampered' where event_id = %(e)s",
                "delete from execution_events where event_id = %(e)s",
                "insert into approved_activity_progress (project_id, activity_uid, decision_id, as_of_date, reported_pct) "
                "select project_id, matched_activity_uid, (select decision_id from planner_decisions limit 1), current_date, 99 from execution_events where event_id = %(e)s",
                "update planner_decisions set action = 'REJECT'"):
        with connect() as c:
            c.execute("select set_config('app.actor_id', %s, false)", (str(kit.world.se.id),))
            with pytest.raises(psycopg.Error):
                c.execute(sql, {"e": claim})
    assert (kit.ledger(), kit.count("planner_decisions")) == before
