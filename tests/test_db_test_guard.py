"""DB-free tests for the database-test guard, the test classification and header/path context resolution."""
import glob
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.context.project import resolve_raw_project_id
from backend.context.schedule import resolve_raw_schedule_id
from backend.testing import classification as C
from backend.testing.guard import check_db_marker, check_env

GOOD = {
    "SETUAI_ALLOW_DB_TESTS": "1",
    "SETUAI_TEST_ENV": "integration",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:54329/setuai_integ",
}
ROOT = Path(__file__).resolve().parent.parent


def test_guard_accepts_isolated_local_integration_db():
    assert check_env(GOOD) == []


@pytest.mark.parametrize("drop", ["SETUAI_ALLOW_DB_TESTS", "SETUAI_TEST_ENV", "DATABASE_URL"])
def test_guard_requires_each_env_var(drop):
    env = {k: v for k, v in GOOD.items() if k != drop}
    assert check_env(env), drop


def test_guard_rejects_wrong_flag_values():
    assert check_env({**GOOD, "SETUAI_ALLOW_DB_TESTS": "true"})
    assert check_env({**GOOD, "SETUAI_TEST_ENV": "dev"})


@pytest.mark.parametrize("url", [
    "postgresql://postgres.abc:pw@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres",
    "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
    "postgresql://u:p@10.1.2.3:5432/setuai_integ",          # not loopback, not allow-listed
    "postgresql://postgres@127.0.0.1:54329/postgres",       # local but name lacks integ/test
    "postgresql://postgres@127.0.0.1:54329/setuai_prod",
])
def test_guard_refuses_shared_or_unmarked_databases(url):
    assert check_env({**GOOD, "DATABASE_URL": url})


def test_guard_allowlist_can_admit_a_remote_integration_host_but_never_supabase():
    remote = {**GOOD, "DATABASE_URL": "postgresql://u:p@ci-db.internal:5432/setuai_integ", "SETUAI_INTEG_HOST_ALLOWLIST": "ci-db.internal"}
    assert check_env(remote) == []
    sup = {**GOOD, "DATABASE_URL": "postgresql://u:p@db.x.supabase.co:5432/setuai_integ", "SETUAI_INTEG_HOST_ALLOWLIST": "db.x.supabase.co"}
    assert check_env(sup), "allow-list must not be able to admit a Supabase host"


def test_guard_refuses_the_host_in_the_repo_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_URL=postgresql://u:p@shared-dev.example.com:5432/setuai_integ\n")
    env = {**GOOD, "DATABASE_URL": "postgresql://u:p@shared-dev.example.com:5432/setuai_integ", "SETUAI_INTEG_HOST_ALLOWLIST": "shared-dev.example.com"}
    assert any(".env" in p for p in check_env(env, str(dotenv)))


class _Cur:
    def __init__(self, row=None, boom=False):
        self.row, self.boom = row, boom
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a):
        if self.boom:
            raise RuntimeError("no such table")
    def fetchone(self): return self.row


class _Conn:
    def __init__(self, cur): self._cur = cur
    def cursor(self): return self._cur


def test_db_marker_check():
    assert check_db_marker(_Conn(_Cur({"value": "integration"}))) is None
    assert check_db_marker(_Conn(_Cur({"value": "shared"})))
    assert check_db_marker(_Conn(_Cur(None)))
    assert check_db_marker(_Conn(_Cur(boom=True))), "missing marker table must be refused"


def test_every_test_file_is_classified_exactly_once():
    files = sorted(
        p.relative_to(ROOT).as_posix()
        for pattern in ("tests/test_*.py", "tests/db_v2/test_*.py", "backend/**/test_*.py", "backend/smoke_test.py")
        for p in ROOT.glob(pattern)
    )
    sets = {"DB_FREE": C.DB_FREE, "DB_READ": C.DB_READ, "DB_WRITE": C.DB_WRITE, "DB_DESTRUCTIVE": C.DB_DESTRUCTIVE}
    unclassified = [f for f in files if C.classify(f) == "unclassified"]
    assert not unclassified, f"add to backend/testing/classification.py: {unclassified}"
    for f in files:
        assert sum(f in s for s in sets.values()) == 1, f"{f} appears in more than one class"
    stale = [f for s in sets.values() for f in s if f not in files]
    assert not stale, f"classification lists files that do not exist: {stale}"


def test_unknown_files_fail_closed():
    assert C.classify("tests/test_brand_new.py") == "unclassified"


def _req(path_params):
    return SimpleNamespace(path_params=path_params)


def test_path_project_id_is_authoritative_and_conflicts_are_rejected():
    assert resolve_raw_project_id(_req({"project_id": "P1"}), None, None) == "P1"
    assert resolve_raw_project_id(_req({"project_id": "P1"}), "P1", "P1") == "P1"
    with pytest.raises(HTTPException) as e:
        resolve_raw_project_id(_req({"project_id": "P2"}), "P1", None)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        resolve_raw_project_id(_req({"project_id": "P2"}), None, "P1")
    with pytest.raises(HTTPException):
        resolve_raw_project_id(_req({}), "P1", "P2")
    assert resolve_raw_project_id(_req({}), "P1", None) == "P1"
    assert resolve_raw_project_id(_req({}), None, "P3") == "P3"
    assert resolve_raw_project_id(_req({}), None, None) is None


def test_path_schedule_id_is_authoritative_and_conflicts_are_rejected():
    assert resolve_raw_schedule_id(_req({"schedule_id": "S1"}), None, None) == "S1"
    with pytest.raises(HTTPException) as e:
        resolve_raw_schedule_id(_req({"schedule_id": "S2"}), "S1", None)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        resolve_raw_schedule_id(_req({"schedule_id": "S2"}), None, "S1")
    with pytest.raises(HTTPException):
        resolve_raw_schedule_id(_req({}), "S1", "S2")  # header vs query, no path parameter
    assert resolve_raw_schedule_id(_req({}), "S1", "S1") == "S1"
    assert resolve_raw_schedule_id(_req({}), "S1", None) == "S1"
    assert resolve_raw_schedule_id(_req({}), None, None) is None
