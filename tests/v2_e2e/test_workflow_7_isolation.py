"""Workflow 7: role and membership isolation, exercised as an attacker would: wrong role, wrong project, outsider, suspended, removed, deactivated, revoked."""
import uuid

from apikit import TODAY, jpeg, upload
from v2api import make_user
from v2api import connect


def code(r):
    try:
        return r.json()["error"]["code"]
    except Exception:
        return None


def test_workflow_7_each_role_can_do_exactly_its_own_work(story):
    s, api = story, story.api
    c = s.claim("A2010", 400, text="mine")
    other = s.claim("A2010", 100, who=s.se2, text="theirs")
    # Site Engineer: cannot decide, cannot manage schedules or members, cannot see others' claims, cannot see the queue or the audit trail
    assert api.post(s.url(f"/claims/{c['claim_id']}/decision"), s.se, json={"action": "APPROVE"}).status_code == 403
    assert api.post(s.url("/schedule-imports"), s.se, files={"file": ("x.csv", b"a,b\n", "text/csv")}).status_code == 403
    assert api.post(s.url("/members"), s.se, json={"email": "x@y.z", "role": "SITE_ENGINEER"}).status_code == 403
    assert api.get(s.url(f"/claims/{other['claim_id']}"), s.se).status_code == 404
    assert api.get(s.url("/review-queue"), s.se).status_code == 403 and api.get(s.url("/audit"), s.se).status_code == 403
    assert api.patch(s.url("/settings"), s.se, json={"over_baseline_tolerance_pct": 50}).status_code == 403
    # Supervisor: decides and resolves, but cannot file claims, upload schedules or manage members / settings
    assert api.post(s.url("/claims"), s.sup, json={"event_date": str(TODAY), "raw_text": "I saw it", "activity_uid": s.uid("A2010"), "claimed_pct": 10}).status_code == 403
    assert api.post(s.url("/schedule-imports"), s.sup, files={"file": ("x.csv", b"a,b\n", "text/csv")}).status_code == 403
    assert api.post(s.url("/members"), s.sup, json={"email": "x@y.z", "role": "SITE_ENGINEER"}).status_code == 403
    assert api.get(s.url("/settings"), s.sup).status_code == 403
    assert api.post(s.url(f"/claims/{c['claim_id']}/withdraw"), s.sup, json={"reason": "not theirs"}).status_code == 403
    # Project Manager: manages, reads aggregates, but never decides, files, or reads claim content
    assert api.post(s.url(f"/claims/{c['claim_id']}/decision"), s.pm, json={"action": "APPROVE"}).status_code == 403
    assert api.post(s.url(f"/claims/{c['claim_id']}/decision"), s.pm, json={"action": "REJECT", "justification": "no"}).status_code == 403
    assert api.post(s.url("/claims"), s.pm, json={"event_date": str(TODAY), "raw_text": "pm claim", "activity_uid": s.uid("A2010"), "claimed_pct": 10}).status_code == 403
    assert api.get(s.url(f"/claims/{c['claim_id']}"), s.pm).json()["error"]["code"] == "CLAIM_CONTENT_FORBIDDEN"
    assert api.get(s.url("/claim-counts"), s.pm).json()["pending_total"] == 2
    assert api.patch(s.url("/settings"), s.pm, json={"over_baseline_tolerance_pct": 12}).status_code == 200
    assert api.post(s.url(f"/issues/{uuid.uuid4()}/resolve"), s.pm, json={"notes": "x"}).status_code == 403
    assert s.count("planner_decisions") == 0 and s.count("approved_resource_progress") == 0                       # none of the refused attempts changed anything


def test_workflow_7_outsiders_other_projects_suspended_removed_deactivated_and_revoked_users(story):
    s, api = story, story.api
    w = s.world
    c = s.claim("A2010", 400)
    probes = [("GET", s.url("/dashboard/summary")), ("GET", s.url("/review-queue")), ("GET", s.url(f"/claims/{c['claim_id']}")), ("GET", s.url("/documents")), ("GET", s.url("/activities")),
              ("GET", s.url("/issues")), ("POST", s.url(f"/claims/{c['claim_id']}/decision")), ("GET", s.url("/audit"))]
    call = lambda who, m, u: api.call(m, u, who, json={"action": "APPROVE"} if m == "POST" else None)
    # an outsider (member of nothing) and a PM of another project
    for who in (w.outsider, w.pm2):
        assert all(call(who, m, u).status_code == 403 and code(call(who, m, u)) == "NOT_A_MEMBER" for m, u in probes)
    # an unauthenticated caller and a garbage token
    assert all(api.call(m, u, None).status_code == 401 for m, u in probes)
    assert api.get(s.url("/dashboard/summary"), headers={"Authorization": "Bearer not.a.token"}).status_code == 401
    # a claim id from this project used under another project's URL is simply not found
    other_pid = w.project2
    assert api.get(f"/api/v2/projects/{other_pid}/claims/{c['claim_id']}", w.pm2).status_code in (403, 404)       # pm2 may not read content at all
    sup2 = make_user("sup2")
    assert api.post(f"/api/v2/projects/{other_pid}/members", w.pm2, json={"email": sup2.email, "role": "SUPERVISOR"}).status_code == 201
    assert api.get(f"/api/v2/projects/{other_pid}/claims/{c['claim_id']}", sup2).status_code == 404
    assert api.post(f"/api/v2/projects/{other_pid}/claims/{c['claim_id']}/decision", sup2, json={"action": "APPROVE"}).status_code == 404
    assert s.count("planner_decisions") == 0

    # suspended membership: the existing token stops working at once
    with connect(system=True) as cx:
        cx.execute("update project_memberships set status = 'SUSPENDED' where project_id = %s and user_id = %s", (s.project, w.sup.id))
    r = api.get(s.url("/review-queue"), w.sup)
    assert r.status_code == 403 and code(r) == "NOT_A_MEMBER"
    with connect(system=True) as cx:
        cx.execute("update project_memberships set status = 'ACTIVE' where project_id = %s and user_id = %s", (s.project, w.sup.id))
    assert api.get(s.url("/review-queue"), w.sup).status_code == 200
    # a removed engineer cannot file or read
    with connect(system=True) as cx:
        cx.execute("update project_memberships set status = 'REMOVED' where project_id = %s and user_id = %s", (s.project, w.se2.id))
    assert api.get(s.url("/my-claims"), w.se2).status_code == 403
    # a deactivated account is refused everywhere; revoked sessions are refused; both are the administrator's / sign-in system's doing
    with connect(system=True) as cx:
        cx.execute("update profiles set is_active = false where id = %s", (w.se.id,))
    r = api.get(s.url("/my-claims"), w.se)
    assert r.status_code == 403 and code(r) == "ACCOUNT_DISABLED"
    with connect(system=True) as cx:
        cx.execute("update profiles set is_active = true where id = %s", (w.se.id,))
        cx.execute("update profiles set tokens_valid_after = now() + interval '1 minute' where id = %s", (w.sup.id,))
    r = api.get(s.url("/review-queue"), w.sup)
    assert r.status_code == 401 and code(r) == "TOKEN_REVOKED"
    assert s.count("planner_decisions") == 0 and s.count("approved_resource_progress") == 0


def test_workflow_7_documents_and_evidence_follow_the_same_walls(story):
    s, api = story, story.api
    mine = upload(s, api, "mine.txt", b"my notes about A2010", "DAILY_REPORT")
    theirs = upload(s, api, "theirs.txt", b"other engineer's notes", "DAILY_REPORT", who=s.se2)
    c = s.claim("A2010", 400, evidence_document_ids=[mine["document_id"]])
    assert api.post(s.url(f"/claims/{c['claim_id']}/evidence"), s.se, json={"document_id": theirs["document_id"]}).status_code in (201, 422)        # attaching is allowed for project documents; reading is not
    for who, want in ((s.se, 200), (s.sup, 200), (s.pm, 404), (s.se2, 404), (s.outsider, 403)):
        assert api.get(s.url(f"/documents/{mine['document_id']}/content"), who).status_code == want, who
    # the file never leaves through any JSON response
    for who in (s.se, s.sup):
        txt = api.get(s.url(f"/claims/{c['claim_id']}"), who).text + api.get(s.url("/documents"), who).text
        assert "storage" not in txt.lower() and "/evidence" not in txt and ".local" not in txt
    assert jpeg()  # keeps the import used
