"""
Execution history (DB_WRITE + INTEGRATION, isolated DB): every activity with progress has its own believable chronological
history of claims and supervisor decisions, the latest approved state is unchanged (approved_actuals stays the single current
state), unstarted work has no history, and the Activity History endpoint returns the whole story.

    SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration \
    DATABASE_URL=postgresql://postgres@127.0.0.1:54329/setuai_integ pytest tests/test_integration_execution_history.py
"""
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.prototype_seed import data, seed as seeder
from backend.shared.db import get_connection

pytestmark = [pytest.mark.integration]

ACT = {a.code: (p, a) for p in data.PROJECTS for s in p.stages for a in s.acts}
CODES = {p.code: p for p in data.PROJECTS}
# the seeded history only: batch-intake / API-created claims carry a fingerprint, test claims an EV-NT- id
SEEDED = "ee.claim_fingerprint IS NULL AND ee.event_id NOT LIKE 'EV-NT-%%'"


@pytest.fixture(scope="module")
def db():
    with seeder._connect() as conn:
        seeder.seed(conn)
    su = get_connection()
    su.autocommit = True
    pids = {c: str(su.execute("SELECT project_id FROM projects WHERE project_code = %s", (c,)).fetchone()["project_id"]) for c in CODES}
    rows = su.execute(
        f"""SELECT ee.event_id, ee.project_id, ee.schedule_id, ee.stage_id, ee.matched_activity_id AS activity_id, ee.event_date,
                   ee.raw_claim_text, ee.claimed_pct, ee.status, ee.event_type, ee.input_channel, ee.document_id, ee.created_at,
                   pd.decision_id, pd.action, pd.approved_pct, pd.decided_at, pd.justification
              FROM execution_events ee JOIN planner_decisions pd ON pd.event_id = ee.event_id
             WHERE {SEEDED} AND ee.project_id = ANY(%s::uuid[]) ORDER BY ee.event_date, pd.decided_at, ee.event_id""",
        (list(pids.values()),)).fetchall()
    by_act = defaultdict(list)
    for r in rows:
        by_act[r["activity_id"]].append(dict(r))
    actuals = {r["activity_id"]: dict(r) for r in su.execute(
        "SELECT * FROM approved_actuals WHERE project_id = ANY(%s::uuid[])", (list(pids.values()),)).fetchall()}
    acts = {(str(r["project_id"]), r["activity_id"]): dict(r) for r in su.execute(
        "SELECT project_id, activity_id, stage_id FROM schedule_activities WHERE project_id = ANY(%s::uuid[])", (list(pids.values()),)).fetchall()}
    yield {"su": su, "pids": pids, "by_act": by_act, "actuals": actuals, "acts": acts}
    app.dependency_overrides.pop(get_current_user, None)


def accepted(rows):
    return [r for r in rows if r["action"] in ("APPROVE", "EDIT")]


def test_completed_project_activities_show_a_progression_to_completion(db):
    nnb = CODES["NNB-COP-01"]
    for s in nnb.stages:
        for a in s.acts:
            acc = accepted(db["by_act"][a.code])
            pcts = [float(r["approved_pct"]) for r in acc]
            assert len(acc) >= 2, f"{a.code} must show how it reached completion, not a single claim"
            assert pcts[-1] == 100.0 and all(b > x for x, b in zip(pcts, pcts[1:])), (a.code, pcts)
            assert pcts[0] < 100.0 and acc[-1]["event_type"] == "ACTUAL_FINISH"


def test_ongoing_projects_have_histories_that_end_at_the_existing_current_state(db):
    for code in ("AND-ODC-01", "NRL-EXP-01"):
        for s in CODES[code].stages:
            for a in s.acts:
                if a.pct == 0:
                    continue
                acc = accepted(db["by_act"][a.code])
                assert acc, a.code
                assert float(acc[-1]["approved_pct"]) == a.pct, (a.code, float(acc[-1]["approved_pct"]), a.pct)
                if a.pct < 100:
                    assert all(float(r["approved_pct"]) < 100 for r in acc), f"{a.code} is ongoing and must not be completed in its history"


def test_unstarted_work_has_no_claims_decisions_or_actuals(db):
    started = {a.code for a in ACT.values() if False}
    for code, (p, a) in ACT.items():
        if a.pct == 0:
            assert code not in db["by_act"], f"{code} has not started but has claims"
            assert code not in db["actuals"], f"{code} has not started but has an approved actual"
    smp = CODES["SMP-CCP-01"]
    n = db["su"].execute("SELECT count(*) n FROM execution_events WHERE project_id = %s", (db["pids"]["SMP-CCP-01"],)).fetchone()["n"]
    assert n == 0, "the upcoming project has no execution history at all"


def test_history_is_chronological_monotonic_and_belongs_to_the_right_place(db):
    for code, (p, a) in ACT.items():
        rows = db["by_act"].get(code, [])
        if not rows:
            continue
        acc = accepted(rows)
        dates = [r["event_date"] for r in acc]
        assert dates == sorted(dates) and len(set(dates)) == len(dates), f"{code}: dates must strictly increase"
        pcts = [float(r["approved_pct"]) for r in acc]
        assert all(b > x for x, b in zip(pcts, pcts[1:])), f"{code}: progress must never decrease: {pcts}"
        decided = [r["decided_at"] for r in acc]
        assert decided == sorted(decided), f"{code}: decisions are recorded in claim order"
        for r in rows:
            assert r["decided_at"].date() >= r["event_date"], f"{code}: decided before the claim was made"
            assert r["event_date"] <= p.data_date
            assert str(r["project_id"]) == db["pids"][p.code] and r["schedule_id"] == p.schedule_id
            assert r["stage_id"] == db["acts"][(db["pids"][p.code], code)]["stage_id"], f"{code}: claim attributed to the wrong stage"
        start = db["actuals"][code]["actual_start"]
        assert dates[0] >= start, f"{code}: reported before it started"


def test_latest_decision_is_the_single_current_state_in_approved_actuals(db):
    for code, (p, a) in ACT.items():
        if a.pct == 0:
            continue
        acc = accepted(db["by_act"][code])
        actual = db["actuals"][code]
        assert actual["decision_id"] == acc[-1]["decision_id"] and actual["event_id"] == acc[-1]["event_id"], code
        assert float(actual["actual_pct_complete"]) == a.pct
    n = db["su"].execute("SELECT count(*) n FROM (SELECT 1 FROM approved_actuals WHERE project_id = ANY(%s::uuid[]) GROUP BY schedule_id, activity_id HAVING count(*) > 1) x", (list(db["pids"].values()),)).fetchone()["n"]
    assert n == 0, "approved_actuals keeps exactly one row per activity"


def test_histories_vary_per_activity_and_read_like_site_reports(db):
    counts = {c: len(accepted(rows)) for c, rows in db["by_act"].items() if c in ACT}
    assert len(set(counts.values())) >= 6 and max(counts.values()) - min(counts.values()) >= 6
    texts = [r["raw_claim_text"] for rows in db["by_act"].values() for r in rows]
    assert len(set(texts)) > 0.9 * len(texts)
    assert not [t for t in texts if t.endswith(" completed") and len(t) < 90], "no bare '<activity> completed' claims"
    assert sum(1 for t in texts if "percent complete" in t) == 0
    for code, rows in db["by_act"].items():
        if code in ACT:
            assert len({r["raw_claim_text"] for r in rows}) == len(rows), f"{code}: repeated claim text"
    # long running work reports more often than short administrative work
    avg = lambda codes: sum(counts[c] for c in codes) / len(codes)
    assert avg(["NNB-PLC-020", "NNB-PLC-030", "NNB-PLC-040", "NNB-PLC-050"]) > avg(["NNB-SUR-040", "AND-PLN-040", "AND-PLN-030"]) + 1


def test_claims_come_from_different_reports_without_duplicating_claims(db):
    kinds = {r["input_channel"] for rows in db["by_act"].values() for r in rows}
    assert {"FILE_UPLOAD", "SCANNED_OCR", "TYPED_TEXT"} <= kinds
    files = db["su"].execute("SELECT DISTINCT split_part(file_name, '_', 1) k FROM source_documents WHERE project_id = ANY(%s::uuid[]) AND batch_id IS NULL", (list(db["pids"].values()),)).fetchall()
    assert {"DSR", "WPR", "MBOOK"} <= {r["k"] for r in files} or {"DSR", "WPR"} <= {r["k"] for r in files}
    seen = set()
    for code, rows in db["by_act"].items():
        for r in rows:
            key = (code, r["event_date"], float(r["claimed_pct"]))
            assert key not in seen, f"duplicate claim {key}"
            seen.add(key)
    # a report file may corroborate a claim without creating another claim
    extra = db["su"].execute(
        """SELECT count(*) n FROM source_references sr JOIN execution_events ee ON ee.event_id = sr.event_id
            WHERE ee.project_id = ANY(%s::uuid[]) AND sr.document_id IS DISTINCT FROM ee.document_id AND sr.document_id IS NOT NULL""",
        (list(db["pids"].values()),)).fetchone()["n"]
    assert extra > 10


def test_supervisor_decisions_are_consistent_with_the_claims(db):
    for code, rows in db["by_act"].items():
        for r in rows:
            if r["action"] == "EDIT":
                assert float(r["approved_pct"]) < float(r["claimed_pct"]), f"{code}: an EDIT approves less than claimed"
                assert r["status"] == "EDITED"
            elif r["action"] == "APPROVE":
                assert float(r["approved_pct"]) == float(r["claimed_pct"]) and r["status"] == "APPROVED"
            elif r["action"] == "REJECT":
                assert r["status"] == "REJECTED" and r["approved_pct"] is None and r["justification"]
    rejected = [r for rows in db["by_act"].values() for r in rows if r["action"] == "REJECT"]
    assert len(rejected) >= 10, "some claims were rejected and later resubmitted"


def _as(who):
    role = "SITE_ENGINEER" if who == "engineer" else "SUPERVISOR"
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=seeder.UID[who], full_name=who, role=role)
    return TestClient(app)


def test_activity_history_endpoint_returns_the_whole_story(db):
    client = _as("supervisor")
    for code in ("NNB-PLC-040", "NRL-PEI-020", "AND-DRL-030", "NRL-CIV-030"):
        p, a = ACT[code]
        r = client.get(f"/api/v1/activities/{code}/history", headers={"X-Project-ID": db["pids"][p.code], "X-Schedule-ID": p.schedule_id})
        assert r.status_code == 200, r.text
        body = r.json()
        events = [t for t in body["timeline"] if t["type"] == "execution_event"]
        decisions = [t for t in body["timeline"] if t["type"] == "planner_decision"]
        expect = db["by_act"][code]
        assert len(events) == len(expect) >= 2 and len(decisions) == len(expect), code
        dates = [(t.get("timestamp") or t.get("data", {}).get("event_date")) for t in events]
        assert [str(d)[:10] for d in dates] == sorted(str(d)[:10] for d in dates), f"{code}: timeline must be chronological"
    # an unstarted activity exists but has an empty timeline (not a 404)
    p, a = ACT["NRL-COM-020"]
    r = client.get("/api/v1/activities/NRL-COM-020/history", headers={"X-Project-ID": db["pids"][p.code], "X-Schedule-ID": p.schedule_id})
    assert r.status_code == 200 and r.json()["timeline"] == []


def test_dashboard_progress_is_unchanged_by_the_history(db):
    client = _as("supervisor")
    for code, p in CODES.items():
        r = client.get(f"/api/v1/projects/{db['pids'][code]}/schedules/{p.schedule_id}/dashboard")
        assert r.status_code == 200, r.text
        d = r.json()
        got = {s["stage_code"]: s["actual_pct"] for s in d["stages"]}
        want = {s.code: data.rollup(s.acts) for s in p.stages}
        assert got == pytest.approx(want, abs=0.01), code
        assert d["overall"]["actual_pct"] == pytest.approx(data.rollup(data.all_acts(p)), abs=0.01)
