"""Workflow 8: the four-project seed through its command-line entry point, against the isolated test database: seed, verify, report, idempotent re-run,
refusal on a foreign database and on a non-local target. (Content-level checks of the seeded data live in tests/v2_seed.)"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from v2api import SECRET, _truncate, connect

ROOT = Path(__file__).resolve().parents[2]


def run(*args, url=None, env_extra=None):
    env = {**os.environ, "DB_V2_URL": url or os.environ["DATABASE_URL"], "SUPABASE_JWT_SECRET": SECRET, **(env_extra or {})}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_v2.py"), *args], capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)


@pytest.fixture
def evidence(tmp_path):
    return {"V2_EVIDENCE_DIR": str(tmp_path / "ev")}


@pytest.fixture
def empty_db():
    _truncate()
    yield
    from backend.v2 import db as v2db
    v2db.close_pool()
    _truncate()


def test_workflow_8_seed_verify_report_and_refusals(empty_db, evidence):
    # read-only commands on an empty database fail cleanly (nothing to verify)
    v0 = run("--verify", env_extra=evidence)
    assert v0.returncode == 1 and "seed:" in v0.stderr and "Traceback" not in v0.stderr
    # seed
    r = run(env_extra=evidence)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "status: OK - all invariants hold" in r.stdout and "NNB-CRUDE" in r.stdout and "SMP-PIPE" in r.stdout
    j = json.loads(run("--verify", "--json", env_extra=evidence).stdout)
    P = j["projects"]
    assert j["ok"] and j["problems"] == [] and len(j["digest"]) == 64
    assert P["NNB-CRUDE"]["physical_pct"] == "100.000" and P["NNB-CRUDE"]["activity_states"]["COMPLETED"] == P["NNB-CRUDE"]["activities"] and P["NNB-CRUDE"]["claims_pending"] == 0
    for k in ("AEC-OFFSHORE", "NRL-EXPANSION"):
        assert 0 < float(P[k]["physical_pct"]) < 100 and P[k]["claims_pending"] > 0 and P[k]["issues_blocking_active"] >= 2
    d = P["SMP-PIPE"]
    assert d["physical_pct"] == "0.000" and d["claims"] == {} and d["ledger"] == {"resource": 0, "activity": 0} and d["decisions_total"] == 0 and d["notifications"] == {} and d["issues"] == {}
    assert all(P[k]["ledger_inconsistent"] == 0 for k in P) and j["audit_chain"]["valid"]
    # report is read-only and prints the table
    rep = run("--report", env_extra=evidence)
    assert rep.returncode == 0 and "actual progress %" in rep.stdout and "weight basis" in rep.stdout
    # re-running changes nothing
    with connect() as c:
        snap = lambda: c.execute("select (select count(*) from audit_logs) a, (select count(*) from execution_events) e, (select count(*) from approved_resource_progress) r").fetchone()
        before = snap()
        again = run(env_extra=evidence)
        assert again.returncode == 0 and "already seeded" in again.stdout and snap() == before
    # a foreign user makes it a non-seeded database: refused, untouched
    with connect(system=True) as c:
        c.execute("insert into auth.users (id, email) values (gen_random_uuid(), 'foreign@example.test')")
    refused = run(env_extra=evidence)
    assert refused.returncode == 1 and "refusing to seed" in refused.stderr
    # --reset-local empties and rebuilds the identical content
    rebuilt = run("--reset-local", env_extra=evidence)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert json.loads(run("--verify", "--json", env_extra=evidence).stdout)["digest"] == j["digest"]


@pytest.mark.parametrize("url", ["postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres", "postgresql://postgres@127.0.0.1:54329/setuai_integ_demo",
                                 "postgresql://postgres@127.0.0.1:54329/postgres"])
def test_workflow_8_the_seed_command_refuses_hosted_and_legacy_databases(url, evidence):
    for args in ([], ["--reset-local"], ["--verify"]):
        r = run(*args, url=url, env_extra=evidence)
        assert r.returncode == 1 and r.stderr.startswith("seed: ") and "Traceback" not in r.stderr and "pw@" not in r.stderr + r.stdout
