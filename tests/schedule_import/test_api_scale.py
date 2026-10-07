"""Large schedules through the real API: batched writes (few client round trips), bounded time, correct identities, and the reconciliation
safeguards visible to the PM."""
import csv
import io
import time
from contextlib import contextmanager
from datetime import date, timedelta

from v2api import connect, ledger_fingerprint, seed_progress

P = "/api/v2/projects"
N = 2000


def make_csv(n, prefix="A", renamed=False):
    a, r = io.StringIO(), io.StringIO()
    w, rw = csv.writer(a), csv.writer(r)
    w.writerow(["Activity ID", "Activity Name", "WBS Path", "Discipline", "Start", "Finish", "Duration", "Predecessors"])
    rw.writerow(["Activity ID", "Resource ID", "Baseline Qty", "Unit"])
    d0 = date(2026, 1, 5)
    for i in range(n):
        s = d0 + timedelta(days=i % 300)
        w.writerow([f"{prefix}{i:05d}", f"Activity {i} work package {i % 97}", f"Site > Area {i % 40} > Zone {i % 7}", "Civil Works",
                    s.strftime("%d-%m-%Y"), (s + timedelta(days=9)).strftime("%d-%m-%Y"), 8, f"{prefix}{i - 1:05d}" if i else ""])
        for res, qty, unit in (("CONCRETE_M3", 100 + i, "m3"), ("MANHOURS", 500, "mh"), ("EXCAVATOR_HOURS", 80, "hr"), ("STEEL_T", 5 + i % 9, "tonne")):
            rw.writerow([f"{prefix}{i:05d}", res, qty, unit])
    return a.getvalue().encode(), r.getvalue().encode()


def upload(api, user, project, a, r, **data):
    return api.post(f"{P}/{project}/schedule-imports", user, files={"file": ("big.csv", a, "text/csv"), "resources_file": ("r.csv", r, "text/csv")},
                    data={"data_date": "2026-01-05", **data})


class CountingConn:
    """wraps a psycopg connection and counts client->server calls"""
    def __init__(self, conn, counter): self._c, self._n = conn, counter
    def execute(self, *a, **k): self._n["calls"] += 1; return self._c.execute(*a, **k)
    def cursor(self, *a, **k):
        cur = self._c.cursor(*a, **k); n = self._n
        class Cur:
            def __getattr__(s, name): return getattr(cur, name)
            def executemany(s, *aa, **kk): n["batches"] += 1; return cur.executemany(*aa, **kk)
            def execute(s, *aa, **kk): n["calls"] += 1; return cur.execute(*aa, **kk)
        return Cur()
    def __getattr__(self, name): return getattr(self._c, name)


def test_a_2000_activity_schedule_imports_in_a_handful_of_batched_calls(world, api, monkeypatch):
    from backend.v2.services import schedules as svc
    a, r = make_csv(N)
    t0 = time.monotonic()
    imp = upload(api, world.pm, world.project, a, r)
    assert imp.status_code == 201 and imp.json()["report"]["ready_to_build"] and imp.json()["report"]["stats"]["assignments"] == N * 4
    counter = {"calls": 0, "batches": 0}
    real_tx = svc.tx
    @contextmanager
    def counting_tx(*args, **kw):
        with real_tx(*args, **kw) as c:
            yield CountingConn(c, counter)
    monkeypatch.setattr(svc, "tx", counting_tx)
    b = api.post(f"{P}/{world.project}/schedule-imports/{imp.json()['import_id']}/build", world.pm)
    monkeypatch.setattr(svc, "tx", real_tx)
    assert b.status_code == 201, b.text
    total = counter["calls"] + counter["batches"]
    assert total < 120, f"{total} client calls for {N} activities / {N * 4} assignments: writes must be batched"
    assert counter["batches"] >= 5
    assert (b.json()["activities"], b.json()["assignments"], b.json()["dependencies"]) == (N, N * 4, N - 1)
    assert api.post(f"{P}/{world.project}/schedule-versions/{b.json()['version_id']}/activate", world.pm).status_code == 200
    assert time.monotonic() - t0 < 30
    with connect() as c:
        assert c.execute("select count(*) n from baseline_activities").fetchone()["n"] == N
        assert c.execute("select count(distinct activity_uid) n from baseline_activities").fetchone()["n"] == N
        assert c.execute("select count(*) n from baseline_resources").fetchone()["n"] == N * 4


def test_a_full_renumbering_is_mapped_explicitly_and_keeps_every_identity(world, api):
    a, r = make_csv(N, "A")
    imp = upload(api, world.pm, world.project, a, r)
    v1 = api.post(f"{P}/{world.project}/schedule-imports/{imp.json()['import_id']}/build", world.pm).json()["version_id"]
    assert api.post(f"{P}/{world.project}/schedule-versions/{v1}/activate", world.pm).status_code == 200
    with connect() as c:
        uid_of = {x["external_activity_id"]: str(x["activity_uid"]) for x in c.execute("select external_activity_id, activity_uid from baseline_activities").fetchall()}
        asg_before = {str(x["assignment_uid"]) for x in c.execute("select assignment_uid from baseline_resources").fetchall()}
    seed_progress(world.project, "A00010", {"CONCRETE_M3": 55}, world.sup, world.se)
    before = ledger_fingerprint()

    a2, r2 = make_csv(N, "N")                                       # every external id changed, nothing else
    t0 = time.monotonic()
    rev = upload(api, world.pm, world.project, a2, r2)
    assert rev.status_code == 201 and time.monotonic() - t0 < 20
    recon = rev.json()["reconciliation"]
    assert not recon["fuzzy"]["skipped"] and recon["summary"]["renamed"] > N * 0.9
    assert any(b["code"] == "UNRESOLVED_PROGRESS" or b["code"] == "UNRESOLVED_MATCH" for b in recon["blockers"])
    rid = rev.json()["import_id"]
    r_ = api.post(f"{P}/{world.project}/schedule-imports/{rid}/build", world.pm)
    assert r_.status_code == 409                                     # nothing is applied without the PM

    accept = {f"N{i:05d}": uid_of[f"A{i:05d}"] for i in range(N)}   # the PM supplies the id map for the whole renumbering
    d = api.put(f"{P}/{world.project}/schedule-imports/{rid}/decisions", world.pm, json={"reconcile": {"accept": accept}})
    assert d.status_code == 200 and d.json()["reconciliation"]["blockers"] == []
    b = api.post(f"{P}/{world.project}/schedule-imports/{rid}/build", world.pm)
    assert b.status_code == 201, b.text
    with connect() as c:
        new_uid = {x["external_activity_id"]: str(x["activity_uid"]) for x in c.execute(
            "select external_activity_id, activity_uid from baseline_activities where version_id = %s", (b.json()["version_id"],)).fetchall()}
        asg_after = {str(x["assignment_uid"]) for x in c.execute("select assignment_uid from baseline_resources where version_id = %s", (b.json()["version_id"],)).fetchall()}
        assert all(new_uid[f"N{i:05d}"] == uid_of[f"A{i:05d}"] for i in range(N))      # every activity kept its identity
        assert asg_after == asg_before                                                   # and every resource assignment
        assert c.execute("select count(*) n from activity_lineage where relation = 'RENAMED'").fetchone()["n"] == N
    assert ledger_fingerprint() == before
    assert api.post(f"{P}/{world.project}/schedule-versions/{b.json()['version_id']}/activate", world.pm).status_code == 200
    assert ledger_fingerprint() == before
    with connect() as c:
        assert float(c.execute("select physical_pct p from v_activity_progress where external_activity_id = 'N00010'").fetchone()["p"]) > 0


def test_beyond_the_cap_the_pm_sees_that_fuzzy_matching_was_skipped(world, api, monkeypatch):
    from backend.v2.schedule_import import reconcile as R
    a, r = make_csv(300, "A")
    imp = upload(api, world.pm, world.project, a, r)
    v1 = api.post(f"{P}/{world.project}/schedule-imports/{imp.json()['import_id']}/build", world.pm).json()["version_id"]
    api.post(f"{P}/{world.project}/schedule-versions/{v1}/activate", world.pm)
    seed_progress(world.project, "A00007", {"CONCRETE_M3": 10}, world.sup, world.se)
    monkeypatch.setattr(R, "MAX_FUZZY_SIDE", 50)
    a2, r2 = make_csv(300, "N")
    rev = upload(api, world.pm, world.project, a2, r2).json()
    assert rev["reconciliation"]["fuzzy"] == {"skipped": True, "reason": "TOO_MANY_UNMATCHED", "pairs_scored": 0, "old_unmatched": 300, "new_unmatched": 300}
    assert rev["reconciliation"]["summary"]["renamed"] == 0
    assert {b["code"] for b in rev["reconciliation"]["blockers"]} == {"UNRESOLVED_PROGRESS"}


def test_an_oversized_staged_payload_is_refused_before_anything_is_written(world, api, monkeypatch):
    from backend.v2.services import schedules as svc
    monkeypatch.setattr(svc, "MAX_STAGED_BYTES", 20_000)
    a, r = make_csv(200)
    resp = upload(api, world.pm, world.project, a, r)
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "SCHEDULE_TOO_LARGE"
    with connect() as c:
        assert c.execute("select (select count(*) from source_documents) + (select count(*) from schedule_imports) n").fetchone()["n"] == 0
