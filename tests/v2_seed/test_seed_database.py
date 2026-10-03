"""The seeded database, checked from raw rows and through the real HTTP API. The seed is built once (conftest.seeded); the destructive tests come last."""
import inspect
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from seedkit import person
from domainkit import assert_consistent, recompute_project_pct
from v2api import connect

CODES = {"A": "NNB-CRUDE", "B": "AEC-OFFSHORE", "C": "NRL-EXPANSION", "D": "SMP-PIPE"}


def pid(code):
    from backend.v2.seed.projects_spec import project_uuid
    return project_uuid(code)


def kit_of(code):
    p = pid(code)
    with connect() as c:
        ver = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (p,)).fetchone()["version_id"]
    def pct():
        with connect() as c:
            return c.execute("select physical_pct from project_progress_as_of(%s, current_date)", (ver,)).fetchone()["physical_pct"]
    return SimpleNamespace(project=p, project_pct=pct)


def one(sql, *a):
    with connect() as c:
        return c.execute(sql, a).fetchone()


# ------------------------------------------------------------------------------------------------ the seeded state
def test_the_seeded_state_verifies_and_matches_the_frozen_digest(seeded):
    rep = seeded.runner.verify()
    assert rep["ok"] and rep["problems"] == []
    assert seeded.runner.EXPECTED_DIGEST is not None and rep["digest"] == seeded.runner.EXPECTED_DIGEST, \
        f"content digest changed to {rep['digest']}: if the generator was changed on purpose, update EXPECTED_DIGEST in backend/v2/seed/runner.py"


def test_four_projects_fifteen_people_one_active_baseline_each(seeded):
    assert one("select count(*) n from projects")["n"] == 4 and one("select count(*) n from profiles")["n"] == 15
    for code in CODES.values():
        r = one("select count(*) filter (where status='ACTIVE') a, count(*) t from schedule_versions where project_id=%s", pid(code))
        assert (r["a"], r["t"]) == (1, 1)
    roles = one("select count(*) filter (where role='PROJECT_MANAGER') pm, count(*) filter (where role='SUPERVISOR') s, count(*) filter (where role='SITE_ENGINEER') e from project_memberships")
    assert roles["pm"] == 4 and roles["s"] >= 6 and roles["e"] >= 10
    assert one("select lifecycle_status from projects where project_id=%s", pid("NNB-CRUDE"))["lifecycle_status"] == "COMPLETED"
    assert one("select lifecycle_status from projects where project_id=%s", pid("SMP-PIPE"))["lifecycle_status"] == "UPCOMING"


def test_baselines_came_through_the_import_pipeline_not_around_it(seeded):
    for code in CODES.values():
        r = one("select (select count(*) from schedule_imports where project_id=%s and status='BUILT') imps, (select count(*) from source_documents where project_id=%s and kind='SCHEDULE_FILE') docs, "
                "(select count(*) from audit_logs where project_id=%s and action='SCHEDULE_IMPORT_STAGED') staged, (select count(*) from audit_logs where project_id=%s and action='SCHEDULE_VERSION_ACTIVATED') act",
                pid(code), pid(code), pid(code), pid(code))
        assert (r["imps"], r["docs"], r["staged"], r["act"]) == (1, 1, 1, 1), code
    imp = one("select uploaded_by from schedule_imports where project_id=%s", pid("NNB-CRUDE"))["uploaded_by"]
    pm = one("select user_id from project_memberships where project_id=%s and role='PROJECT_MANAGER'", pid("NNB-CRUDE"))["user_id"]
    assert imp == pm


def test_history_was_created_by_the_right_roles_only(seeded):
    with connect() as c:
        assert c.execute("select count(*) n from execution_events e join project_memberships m on m.project_id=e.project_id and m.user_id=e.filed_by where m.role <> 'SITE_ENGINEER'").fetchone()["n"] == 0
        assert c.execute("select count(*) n from planner_decisions d join project_memberships m on m.project_id=d.project_id and m.user_id=d.decided_by where m.role <> 'SUPERVISOR'").fetchone()["n"] == 0
        assert c.execute("select count(*) n from approved_resource_progress r left join planner_decisions d on d.decision_id=r.decision_id where d.decision_id is null").fetchone()["n"] == 0
        assert c.execute("select count(*) n from audit_logs where action in ('CLAIM_APPROVED','CLAIM_REJECTED','CLAIM_HELD') and role <> 'SUPERVISOR'").fetchone()["n"] == 0
        assert c.execute("select count(*) n from audit_logs where action = 'CLAIM_SUBMITTED' and role <> 'SITE_ENGINEER'").fetchone()["n"] == 0


def test_the_seed_never_wrote_a_ledger_row_directly(seeded):
    from backend.v2.seed import history_plan, projects_spec, runner, schedule_gen
    src = "".join(inspect.getsource(m) for m in (runner, history_plan, projects_spec, schedule_gen))
    assert "insert into approved_" not in src.lower() and "update approved_" not in src.lower()
    assert src.count("system=True") == 1                                       # the one privileged step: creating the local sign-in identities
    assert "def bootstrap_users" in src and src.index("system=True") > src.index("def bootstrap_users") or "_conn(system=True)" in src


# ------------------------------------------------------------------------------------------------ A: completed
def test_project_a_is_one_hundred_percent_in_every_rollup(seeded):
    k = kit_of("NNB-CRUDE")
    assert k.project_pct() == 100 and recompute_project_pct(k) == D("100")
    p = pid("NNB-CRUDE")
    with connect() as c:
        v = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (p,)).fetchone()["version_id"]
        acts = c.execute("select physical_pct, execution_state from activity_progress_as_of(%s, current_date)", (v,)).fetchall()
        assert len(acts) == 59 and all(a["physical_pct"] == 100 and a["execution_state"] == "COMPLETED" for a in acts)
        assert all(r["physical_pct"] == 100 for r in c.execute("select physical_pct from wbs_progress_as_of(%s, current_date)", (v,)).fetchall())
        assert all(r["physical_pct"] == 100 for r in c.execute("select physical_pct from discipline_progress_as_of(%s, current_date)", (v,)).fetchall())
        # every measured assignment reached (or passed) its baseline quantity; none was clamped
        short = c.execute("select count(*) n from baseline_resources br where br.version_id=%s and br.measures_progress and coalesce((select cumulative_qty from approved_resource_progress x where x.assignment_uid=br.assignment_uid order by entry_seq desc limit 1),0) < br.baseline_qty", (v,)).fetchone()["n"]
        assert short == 0
        over = c.execute("select max(cumulative_qty) mx, max(overrun_pct) op from approved_resource_progress where project_id=%s and over_baseline", (p,)).fetchone()
        assert over["mx"] is not None and over["op"] > 0
    assert one("select count(*) n from execution_events where project_id=%s and status not in ('APPROVED','REJECTED')", p)["n"] == 0
    assert one("select count(*) n from issues where project_id=%s and status='ACTIVE'", p)["n"] == 0 and one("select count(*) n from issues where project_id=%s and status='RESOLVED'", p)["n"] == 8


# ------------------------------------------------------------------------------------------------ B and C: ongoing
@pytest.mark.parametrize("key", ["B", "C"])
def test_ongoing_projects_are_mixed_with_pending_work_and_blockers(seeded, key):
    p = pid(CODES[key])
    k = kit_of(CODES[key])
    pct = k.project_pct()
    assert 20 < pct < 80 and recompute_project_pct(k) == pct
    with connect() as c:
        v = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (p,)).fetchone()["version_id"]
        st = {r["execution_state"]: r["n"] for r in c.execute("select execution_state, count(*) n from activity_progress_as_of(%s, current_date) group by 1", (v,)).fetchall()}
        assert set(st) == {"COMPLETED", "IN_PROGRESS", "NOT_STARTED"}
        units = {r["u"] for r in c.execute("select distinct br.unit_of_measure u from approved_resource_progress r join baseline_resources br on br.assignment_uid=r.assignment_uid and br.version_id=%s where r.project_id=%s", (v, p)).fetchall()}
        assert len(units) >= 3                                                  # several unit kinds side by side, never converted into each other
        pending = c.execute("select count(*) n from execution_events where project_id=%s and status in ('REPORTED','EXTRACTED','MATCHED','VALIDATED','DISPUTED')", (p,)).fetchone()["n"]
        assert pending >= 3
        assert c.execute("select count(*) n from issues where project_id=%s and status='ACTIVE' and blocks_work", (p,)).fetchone()["n"] >= 2
        # pending claims contribute nothing: the ledger heads are all from APPROVED claims
        assert c.execute("select count(*) n from approved_resource_progress r join planner_decisions d on d.decision_id=r.decision_id join execution_events e on e.event_id=d.event_id where e.status <> 'APPROVED'").fetchone()["n"] == 0
        # reported and approved values are both kept where a Supervisor edited
        edits = c.execute("select count(*) n from planner_decisions d where d.project_id=%s and d.action='EDIT'", (p,)).fetchone()["n"]
        assert edits >= 1
        diff = c.execute("select count(*) n from planner_decisions d join claim_quantities cq on cq.event_id=d.event_id join approved_resource_progress r on r.decision_id=d.decision_id and r.claim_quantity_id=cq.claim_quantity_id "
                         "where d.project_id=%s and d.action='EDIT' and r.cumulative_qty <> cq.normalized_qty", (p,)).fetchone()["n"]
        assert diff >= 1
        assert c.execute("select count(*) n from execution_events where project_id=%s and status='WITHDRAWN' and withdrawn_reason is not null", (p,)).fetchone()["n"] == 2


def test_project_c_has_clarification_and_root_cause_history(seeded):
    p = pid("NRL-EXPANSION")
    with connect() as c:
        cl = {r["s"]: r["n"] for r in c.execute("select clarification_status s, count(*) n from execution_events where project_id=%s and clarification_status is not null group by 1", (p,)).fetchall()}
        assert cl.get("ASKED", 0) >= 1 and cl.get("ANSWERED", 0) >= 3
        loops = c.execute("select count(*) n from execution_events where project_id=%s and status='APPROVED' and clarification_status='ANSWERED'", (p,)).fetchone()["n"]
        assert loops == 2
        assert c.execute("select count(*) n from (select root_cause_id from issues where project_id=%s and root_cause_id is not null group by 1 having count(*) >= 2) t", (p,)).fetchone()["n"] == 2
        ack = c.execute("select overrun_ack_note, d.decision_id from planner_decisions d where d.project_id=%s and d.overrun_ack", (p,)).fetchone()
        assert ack and len(ack["overrun_ack_note"]) > 20
        pctd = c.execute("select count(*) n from planner_decisions where project_id=%s and method='APPLY_PCT_TO_ASSIGNMENTS'", (p,)).fetchone()["n"]
        assert pctd == 3
        # a percent-only claim that the Supervisor applied explicitly keeps the reported figure and records the quantities it produced
        d = c.execute("select e.claimed_pct, d.applied from planner_decisions d join execution_events e on e.event_id=d.event_id where d.project_id=%s and d.method='APPLY_PCT_TO_ASSIGNMENTS' limit 1", (p,)).fetchone()
        assert d["claimed_pct"] > 0 and d["applied"] and all(x["source"] == "PCT" for x in d["applied"])


# ------------------------------------------------------------------------------------------------ D: upcoming
def test_project_d_has_exactly_zero_execution_history(seeded):
    p = pid("SMP-PIPE")
    tables = {"execution_events": "project_id", "claim_quantities": "project_id", "claim_evidence": "project_id", "claim_validations": "project_id", "planner_decisions": "project_id",
              "approved_resource_progress": "project_id", "approved_activity_progress": "project_id", "notifications": "project_id", "issues": "project_id", "issue_evidence": "project_id",
              "root_causes": "project_id", "institutional_memory": "project_id", "conflict_records": "project_id", "candidate_matches": "project_id", "api_idempotency": "project_id"}
    with connect() as c:
        for t, col in tables.items():
            assert c.execute(f"select count(*) n from {t} where {col} = %s", (p,)).fetchone()["n"] == 0, t
        assert c.execute("select count(*) n from source_documents where project_id=%s and kind <> 'SCHEDULE_FILE'", (p,)).fetchone()["n"] == 0
        v = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (p,)).fetchone()["version_id"]
        assert c.execute("select count(*) n from activity_progress_as_of(%s, date '2100-01-01') where physical_pct <> 0 or execution_state <> 'NOT_STARTED' or actual_start is not null", (v,)).fetchone()["n"] == 0
        pr = c.execute("select physical_pct, spi_approx, weight_basis from project_progress_as_of(%s, date '2100-01-01')", (v,)).fetchone()
        assert pr["physical_pct"] == 0 and pr["weight_basis"] == "DURATION"
        assert all(r["physical_pct"] == 0 for r in c.execute("select physical_pct from wbs_progress_as_of(%s, date '2100-01-01')", (v,)).fetchall())
        assert c.execute("select count(*) n from activity_progress_as_of(%s, date '2100-01-01')", (v,)).fetchone()["n"] == 49


# ------------------------------------------------------------------------------------------------ cross-project integrity
def test_ledgers_are_internally_consistent_for_all_four_projects(seeded):
    assert_consistent(kit_of("NNB-CRUDE"))
    for code in CODES.values():
        k = kit_of(code)
        assert recompute_project_pct(k) == k.project_pct(), code


def test_no_assignment_is_double_counted_across_the_whole_database(seeded):
    with connect() as c:
        bad = c.execute("select count(*) n from (select assignment_uid, sum(incremental_qty) s, max(cumulative_qty) m from approved_resource_progress group by 1) t where t.s <> t.m").fetchone()["n"]
        assert bad == 0
        assert c.execute("select count(*) n from (select event_id from planner_decisions where action in ('APPROVE','EDIT') group by event_id having count(*) > 1) t").fetchone()["n"] == 0


def test_the_audit_chain_verifies_and_every_decision_has_its_notification_and_audit_record(seeded):
    with connect() as c:
        from backend.v2.audit import verify_chain
        assert verify_chain(c)["valid"]
        n = c.execute("select count(*) n from planner_decisions").fetchone()["n"]
        assert c.execute("select count(*) n from notifications where notification_type='CLAIM_DECISION'").fetchone()["n"] == n
        assert c.execute("select count(*) n from audit_logs where entity_type='PLANNER_DECISION'").fetchone()["n"] == n


# ------------------------------------------------------------------------------------------------ through the API as the seeded people
def url(code, tail):
    return f"/api/v2/projects/{pid(code)}{tail}"


def test_seeded_people_can_use_the_api_with_their_own_roles(seeded, http):
    pm, sup, se = person("rohit.menon"), person("lakshmi.iyer"), person("arun.nair")
    s = http.get(url("AEC-OFFSHORE", "/dashboard/summary"), pm).json()
    assert s["project"]["name"].startswith("Andaman") and 20 < s["physical_pct"] < 80 and s["claims"]["scope"] == "aggregate" and s["claims"]["pending_total"] >= 3
    assert s["issues"]["blocking"] >= 2 and "APPROXIMATION" in s["planned_method_note"] and s["weight_basis"] == "MANHOURS"
    assert http.get(url("AEC-OFFSHORE", "/blockers"), sup).json()["blocked_stages"], "the stage-level blocker is derived from the active issue"
    q = http.get(url("AEC-OFFSHORE", "/review-queue"), sup, params={"limit": 50}).json()
    assert q["items"] and all(i["status"] in ("EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED") for i in q["items"])
    mine = http.get(url("AEC-OFFSHORE", "/my-claims"), se, params={"limit": 200}).json()["items"]
    assert mine and len({m["event_id"] for m in mine}) == len(mine)
    assert http.get(url("AEC-OFFSHORE", f"/claims/{q['items'][0]['event_id']}"), pm).status_code == 403
    assert http.get(url("AEC-OFFSHORE", "/review-queue"), pm).status_code == 403


def test_the_completed_and_upcoming_projects_through_the_api(seeded, http):
    pm = person("anita.bora")
    a = http.get(url("NNB-CRUDE", "/dashboard/summary"), pm).json()
    d = http.get(url("SMP-PIPE", "/dashboard/summary"), pm).json()
    assert a["physical_pct"] == 100 and a["activities"]["completed"] == 59 and a["claims"]["pending_total"] == 0
    assert d["physical_pct"] == 0 and d["activities"]["not_started"] == 49 and d["claims"]["pending_total"] == 0 and d["weight_basis"] == "DURATION"
    assert "duration" in d["weight_basis_explanation"].lower()
    t = http.get(url("SMP-PIPE", "/dashboard/timeline"), pm, params={"date_from": "2027-01-01", "date_to": "2028-12-31", "step_days": 30}).json()
    assert t["points"] and all(p["physical_pct"] == 0 for p in t["points"]) and t["points"][-1]["planned_pct"] == 100
    stages = http.get(url("NNB-CRUDE", "/dashboard/stages"), pm).json()["items"]
    assert stages and all(s["physical_pct"] == 100 for s in stages)


def test_people_cannot_cross_into_projects_they_do_not_belong_to(seeded, http):
    assert http.get(url("NNB-CRUDE", "/dashboard/summary"), person("rohit.menon")).status_code == 403          # PM of B is nothing on A
    assert http.get(url("AEC-OFFSHORE", "/dashboard/summary"), person("farah.khan")).status_code == 403        # PM of C is nothing on B
    assert http.get(url("SMP-PIPE", "/review-queue"), person("kabir.sarma")).json()["items"] == []               # supervisor of D: queue is empty, access is fine
    assert http.get(url("SMP-PIPE", "/review-queue"), person("lakshmi.iyer")).status_code == 403
    projects = {p["project_code"] for p in http.get("/api/v2/projects", person("kabir.sarma")).json()}
    assert projects == {"NNB-CRUDE", "SMP-PIPE"}
    assert {p["project_code"] for p in http.get("/api/v2/projects", person("imran.hussain")).json()} == {"AEC-OFFSHORE", "NRL-EXPANSION"}


def test_a_site_engineer_of_the_upcoming_project_can_reach_the_claim_form_but_nothing_is_written(seeded, http):
    """D is a normal project (its engineer may file once work starts) but the seeded state must stay at zero history, so this test only checks access and writes nothing."""
    se = person("pranav.rao")
    acts = http.get(url("SMP-PIPE", "/activities"), se, params={"q": "Route survey"}).json()["items"]
    assert acts and acts[0]["measured_assignments"] and acts[0]["physical_pct"] == 0
    assert all(m["approved_cumulative_qty"] is None for m in acts[0]["measured_assignments"])
    r = http.post(url("SMP-PIPE", "/claims"), se, json={"event_date": "not-a-date", "raw_text": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"                    # reached the handler as an authorised engineer; nothing created
    assert http.post(url("SMP-PIPE", "/claims"), person("anita.bora"), json={}).status_code == 403    # the PM may not file
    assert one("select count(*) n from execution_events where project_id=%s", pid("SMP-PIPE"))["n"] == 0


# ------------------------------------------------------------------------------------------------ safety and idempotency (these change the database: keep them last)
def test_seeding_again_changes_nothing(seeded):
    counts = lambda: one("select (select count(*) from execution_events) e, (select count(*) from planner_decisions) d, (select count(*) from approved_resource_progress) r, "
                         "(select count(*) from audit_logs) a, (select count(*) from issues) i, (select count(*) from projects) p, (select count(*) from notifications) n")
    before = dict(counts())
    with connect() as c:
        c.execute("delete from api_idempotency")
    before = dict(counts())
    rep = seeded.runner.seed()
    assert rep["ok"] and dict(counts()) == before


def test_a_database_that_is_not_the_seeded_state_is_refused_and_untouched(seeded):
    with connect(system=True) as c:
        c.execute("insert into auth.users (id, email) values (gen_random_uuid(), 'stranger@example.test')")
    try:
        before = one("select count(*) n from audit_logs")["n"]
        with pytest.raises(seeded.runner.SeedError, match="not empty and is not the seeded demo state"):
            seeded.runner.seed()
        assert one("select count(*) n from audit_logs")["n"] == before and one("select count(*) n from execution_events")["n"] > 0
    finally:
        with connect(system=True) as c:
            c.execute("delete from auth.users where email = 'stranger@example.test'")
            c.execute("delete from profiles where email = 'stranger@example.test'")


def test_reset_local_rebuilds_the_identical_content(seeded):
    first = seeded.runner.verify()["digest"]
    rep = seeded.runner.seed(reset=True)
    assert rep["ok"] and rep["digest"] == first == seeded.runner.EXPECTED_DIGEST
    assert_consistent(kit_of("NNB-CRUDE"))


# ------------------------------------------------------------------------------------------------ targets (no database needed beyond the test one)
@pytest.mark.parametrize("url_", [
    "postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres",
    "postgresql://postgres.abcdefghijklmnop:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
    "postgresql://postgres@127.0.0.1:54329/setuai_integ_demo",
    "postgresql://postgres@127.0.0.1:54329/postgres",
    "postgresql://postgres@10.0.0.5:5432/setuai_v2_integ",
])
def test_the_seed_refuses_every_non_local_or_non_v2_target(monkeypatch, url_):
    from backend.v2.seed import runner
    monkeypatch.setenv("DB_V2_URL", url_)
    for fn in (runner.local_target, runner.seed, runner.reset_local, runner.verify):
        with pytest.raises(runner.SeedError):
            fn()
