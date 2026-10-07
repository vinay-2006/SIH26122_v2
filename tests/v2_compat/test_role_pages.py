"""Page-level access: WBS Explorer is not a Project Manager page, Audit Trail and Project Intelligence are not Supervisor pages -- enforced by the API, not just hidden."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def P(kit, tail):
    return f"/api/v1/projects/{kit.project}{tail}"


def test_the_project_manager_has_no_wbs_explorer_but_keeps_every_other_schedule_read(kit, lg, ver):
    assert lg.get(f"/api/v1/schedules/{ver}/wbs-tree", kit.world.pm).status_code == 403
    for tail in (f"/api/v1/schedules/{ver}/activities", f"/api/v1/schedules/{ver}/dependencies", "/api/v1/schedules/active"):
        assert lg.get(tail, kit.world.pm).status_code == 200, tail                      # Overview / Impact Preview / Schedule still read the schedule
    for who in (kit.world.sup, kit.world.se):
        assert lg.get(f"/api/v1/schedules/{ver}/wbs-tree", who).status_code == 200
    assert lg.get(f"/api/v1/schedules/{ver}/wbs-tree", kit.world.outsider).status_code == 403


def test_the_supervisor_has_no_audit_trail_or_project_intelligence_but_keeps_the_audit_feed_for_activity_history(kit, lg, ver):
    for tail in ("/dossier", "/dossier/audit-verification", "/agent/briefing", "/agent/findings"):
        assert lg.get(P(kit, tail), kit.world.sup).status_code == 403, tail
    assert lg.post(P(kit, "/agent/query"), kit.world.sup, json={"query": "status"}).status_code == 403
    assert lg.get("/api/v1/audit", kit.world.sup).status_code == 200                    # Activity History reads the audit feed
    assert lg.get("/api/v1/review-queue", kit.world.sup).status_code == 200            # the review workflow is untouched


def test_the_other_roles_keep_the_pages_they_had(kit, lg, ver):
    assert lg.get(P(kit, "/dossier/audit-verification"), kit.world.pm).status_code == 200
    assert lg.get(P(kit, "/agent/briefing"), kit.world.pm).status_code == 200
    assert lg.get(P(kit, "/agent/briefing"), kit.world.se).status_code == 200
    assert lg.get(P(kit, "/dossier/audit-verification"), kit.world.se).status_code == 403            # engineers never had the audit trail
