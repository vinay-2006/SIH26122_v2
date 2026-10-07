"""
Read-only integrity + coverage checks for the SETUAI-V7-DEMO seed (DB_READ + INTEGRATION).
Requires:  python -m backend.testing.seed_integration   (guarded; isolated DB only)
Writes nothing.
"""
import os

import psycopg
import psycopg.rows
import pytest

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.services.stage_service import StageService
from backend.shared.audit import verify_audit_chain
from backend.testing.seed_integration import ACTS, ACTUALS, DEPS, P_B, P_DEMO, S_B, S_V1, S_V2, UID

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def db():
    c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row, autocommit=True)
    c.read_only = True
    n = c.execute("SELECT count(*) n FROM projects WHERE project_id = %s", (P_DEMO,)).fetchone()["n"]
    if n != 1:
        pytest.fail("seed missing: run `python -m backend.testing.seed_integration` against the isolated DB first")
    yield c
    c.close()


def q(db, sql, a=()):
    return db.execute(sql, a).fetchall()


def test_two_schedule_versions_one_active_and_historical_v1_untouched(db):
    rows = {r["schedule_id"]: r for r in q(db, "SELECT * FROM schedules WHERE project_id = %s", (P_DEMO,))}
    assert rows[S_V2]["active"] is True and rows[S_V1]["active"] is False
    assert rows[S_V2]["supersedes_schedule_id"] == S_V1
    assert len([r for r in rows.values() if r["active"]]) == 1
    assert q(db, "SELECT count(*) n FROM schedule_activities WHERE schedule_id = %s", (S_V1,))[0]["n"] == 8
    assert q(db, "SELECT count(*) n FROM approved_actuals WHERE schedule_id = %s", (S_V1,))[0]["n"] == 0, "V1 data must not be mixed with V2"


def test_stage_structure_and_weights(db):
    st = q(db, "SELECT stage_code, weight_pct, status FROM stages WHERE schedule_id = %s ORDER BY sequence_order", (S_V2,))
    assert len(st) == 4 and abs(sum(s["weight_pct"] for s in st) - 100) < 1e-6
    assert {s["status"] for s in st} >= {"IN_PROGRESS", "NOT_STARTED", "BLOCKED"}
    assert q(db, "SELECT count(*) n FROM stages WHERE gating_predecessor_stage_id IS NOT NULL AND schedule_id = %s", (S_V2,))[0]["n"] == 3


def test_activity_dependency_and_float_coverage(db):
    assert len(ACTS) == 20 and len(DEPS) == 14
    types = {r["relationship_type"] for r in q(db, "SELECT relationship_type FROM schedule_dependencies WHERE schedule_id = %s", (S_V2,))}
    assert types == {"FS", "SS", "FF", "SF"}
    lags = [r["lag_days"] for r in q(db, "SELECT lag_days FROM schedule_dependencies WHERE schedule_id = %s", (S_V2,))]
    assert any(l > 0 for l in lags) and any(l < 0 for l in lags) and any(l == 0 for l in lags)
    a = q(db, "SELECT is_critical, total_float FROM schedule_activities WHERE schedule_id = %s", (S_V2,))
    assert any(r["is_critical"] for r in a) and any(not r["is_critical"] for r in a) and any(r["total_float"] is None for r in a)
    multi = q(db, "SELECT successor_activity_id FROM schedule_dependencies WHERE schedule_id = %s GROUP BY 1 HAVING count(*) > 1", (S_V2,))
    assert multi, "at least one activity with multiple predecessors"


def test_dependency_graph_is_acyclic(db):
    edges = {}
    for r in q(db, "SELECT predecessor_activity_id p, successor_activity_id s FROM schedule_dependencies WHERE schedule_id = %s", (S_V2,)):
        edges.setdefault(r["p"], []).append(r["s"])
    seen, stack = set(), set()

    def visit(n):
        if n in stack:
            raise AssertionError(f"cycle through {n}")
        if n in seen:
            return
        stack.add(n)
        [visit(m) for m in edges.get(n, [])]
        stack.discard(n)
        seen.add(n)

    [visit(n) for n in list(edges)]


def test_attribution_is_consistent_across_the_chain(db):
    bad = q(db, """SELECT a.activity_id FROM schedule_activities a
                   JOIN work_packages w ON w.work_package_id = a.work_package_id
                   WHERE a.schedule_id = %s AND (a.contractor_id IS DISTINCT FROM w.contractor_id OR a.project_id <> w.project_id)""", (S_V2,))
    assert bad == []
    bad = q(db, """SELECT e.event_id FROM execution_events e JOIN schedule_activities a
                   ON a.schedule_id = e.schedule_id AND a.activity_id = e.matched_activity_id
                   WHERE e.project_id = %s AND (e.contractor_id IS DISTINCT FROM a.contractor_id OR e.work_package_id IS DISTINCT FROM a.work_package_id)""", (P_DEMO,))
    assert bad == []


def test_cross_project_and_cross_schedule_consistency(db):
    for sql in (  # scoped to the seed's own projects; residue from other suites is a separate finding
        "SELECT 1 FROM schedule_activities a JOIN schedules s USING (schedule_id) WHERE s.project_id IN ('%s','%s') AND a.project_id IS DISTINCT FROM s.project_id" % (P_DEMO, P_B),
        "SELECT 1 FROM execution_events e JOIN schedules s USING (schedule_id) WHERE s.project_id IN ('%s','%s') AND e.project_id IS DISTINCT FROM s.project_id" % (P_DEMO, P_B),
        "SELECT 1 FROM stages t JOIN schedules s USING (schedule_id) WHERE s.project_id IN ('%s','%s') AND t.project_id <> s.project_id" % (P_DEMO, P_B),
        "SELECT 1 FROM schedule_activities a JOIN stages t ON t.stage_id = a.stage_id WHERE a.project_id IN ('%s','%s') AND t.schedule_id <> a.schedule_id" % (P_DEMO, P_B),
        "SELECT 1 FROM approved_actuals x JOIN schedules s USING (schedule_id) WHERE s.project_id IN ('%s','%s') AND x.project_id IS DISTINCT FROM s.project_id" % (P_DEMO, P_B),
    ):
        assert q(db, sql) == [], sql


def test_baseline_execution_states_are_covered(db):
    rows = {r["activity_id"]: r for r in q(db, "SELECT * FROM approved_actuals WHERE schedule_id = %s", (S_V2,))}
    states = {a: StageService.get_execution_state(dict(r)) for a, r in rows.items()}
    assert states["EW-030"] == "COMPLETED" and states["EW-020"] == "IN_PROGRESS"
    covered = set(states.values()) | {"NOT_STARTED"}  # EW-010 has no actual
    assert {"NOT_STARTED", "IN_PROGRESS", "COMPLETED"} <= covered
    assert "EW-010" not in rows
    assert StageService.get_workflow_condition(dict(rows["STR-010"])) == "REWORK_IN_PROGRESS"
    assert set(ACTUALS) == set(rows)


def test_quality_gates_cover_every_status(db):
    got = {r["status"] for r in q(db, "SELECT status FROM quality_gates WHERE project_id = %s", (P_DEMO,))}
    assert got == {"FAILED", "PENDING", "PASSED", "WAIVED", "NOT_REQUIRED", "SUBMITTED"}
    hold = q(db, """SELECT a.activity_id FROM schedule_activities a
                    JOIN approved_actuals x ON x.schedule_id = a.schedule_id AND x.activity_id = a.activity_id
                    JOIN quality_gates g ON g.schedule_id = a.schedule_id AND g.activity_id = a.activity_id
                    WHERE a.schedule_id = %s AND a.quality_gate_required AND x.actual_pct_complete >= 100 AND g.status <> 'PASSED'
                      AND g.status <> 'WAIVED' AND g.status <> 'NOT_REQUIRED'""", (S_V2,))
    assert "FND-010" in {r["activity_id"] for r in hold}, "FND-010 is 100% with a FAILED gate => QUALITY_HOLD candidate"


def test_pending_field_reports_exist_including_one_on_a_completed_activity(db):
    ev = {r["reported_activity_id"] for r in q(db, "SELECT reported_activity_id FROM execution_events WHERE project_id = %s AND status = 'EXTRACTED'", (P_DEMO,))}
    assert ev == {"EW-010", "EW-030"}


def test_seed_audit_chain_verifies(db):
    logs = [dict(r) for r in q(db, "SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id", (P_DEMO,))]
    assert logs and verify_audit_chain(logs, allow_subchain=False) == (True, None)


def _as(user_key):
    c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row, autocommit=True)
    c.execute("SET ROLE authenticated")
    c.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (UID[user_key],))
    return c


@pytest.mark.parametrize("table", ["schedules", "schedule_activities", "execution_events", "approved_actuals", "stages",
                                   "quality_gates", "contractors", "work_packages", "institutional_incidents"])
def test_runtime_rls_isolates_demo_from_sibling_project(db, table):
    demo_total = q(db, f"SELECT count(*) n FROM {table} WHERE project_id = %s", (P_DEMO,))[0]["n"]
    assert demo_total > 0
    pm, b = _as("pm"), _as("userB")
    assert pm.execute(f"SELECT count(*) n FROM {table} WHERE project_id = %s", (P_DEMO,)).fetchone()["n"] == demo_total
    assert pm.execute(f"SELECT count(*) n FROM {table} WHERE project_id = %s", (P_B,)).fetchone()["n"] == 0
    assert b.execute(f"SELECT count(*) n FROM {table} WHERE project_id = %s", (P_DEMO,)).fetchone()["n"] == 0


def test_same_activity_id_in_two_projects_resolves_to_the_right_one(db):
    pm, b = _as("pm"), _as("userB")
    a = pm.execute("SELECT project_id, schedule_id FROM schedule_activities WHERE activity_id = 'EW-010'").fetchall()
    assert {(str(r["project_id"]), r["schedule_id"]) for r in a} == {(P_DEMO, S_V2), (P_DEMO, S_V1)}
    bb = b.execute("SELECT project_id, schedule_id FROM schedule_activities WHERE activity_id = 'EW-010'").fetchall()
    assert {(str(r["project_id"]), r["schedule_id"]) for r in bb} == {(P_B, S_B)}
