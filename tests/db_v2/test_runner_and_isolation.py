"""Migration runner safety, rerunnability, checksum immutability, and isolation from the old shared database."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("migrate_v2", ROOT / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)

BASE = "postgresql://postgres@127.0.0.1:54329/"


@pytest.mark.parametrize("url,why", [
    ("postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/setuai_v2_integ", "(?i)supabase|not loopback"),
    ("postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/setuai_v2_integ", "(?i)supabase"),
    ("postgresql://postgres@10.1.2.3:5432/setuai_v2_integ", "not loopback"),
    ("postgresql://postgres@127.0.0.1:54329/setuai_integ_demo", "must start with"),
    ("postgresql://postgres@127.0.0.1:54329/postgres", "must start with"),
    ("postgresql://postgres@127.0.0.1:54329/", "host and database"),
])
def test_runner_refuses_unsafe_targets(url, why):
    with pytest.raises(migrate.GuardError, match=why):
        migrate.check_target(url)


def test_runner_accepts_the_local_v2_database():
    assert migrate.check_target(BASE + "setuai_v2_integ") == ("127.0.0.1", "setuai_v2_integ")


def test_runner_refuses_the_host_configured_in_the_old_env_file(monkeypatch):
    monkeypatch.setattr(migrate, "_old_shared_host", lambda: "127.0.0.1")   # pretend the old shared DB lived on this host
    with pytest.raises(migrate.GuardError, match="old shared database host"):
        migrate.check_target(BASE + "setuai_v2_integ")


def test_runner_never_reads_database_url():
    env = {k: v for k, v in os.environ.items() if k != "DB_V2_URL"}
    env["DATABASE_URL"] = "postgresql://postgres:pw@db.example.supabase.co:5432/postgres"      # the old stack's variable
    r = subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py"), "status"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "DB_V2_URL" in r.stderr
    assert "supabase" not in (r.stdout + r.stderr)                              # nothing about the old URL leaks into output


def test_migrations_are_rerunnable_and_checksum_protected(tmp_path, monkeypatch):
    admin = psycopg.connect(BASE + "postgres", autocommit=True)
    name = "setuai_v2_integ_scratch"
    admin.execute(f"drop database if exists {name}")
    admin.execute(f"create database {name}")
    try:
        mdir = tmp_path / "m"; mdir.mkdir()
        (mdir / "0001_a.sql").write_text("create table t1 (id int primary key);")
        (mdir / "0002_b.sql").write_text("create table t2 (id int primary key);")
        monkeypatch.setattr(migrate, "MIGRATIONS", mdir)
        url = BASE + name
        assert migrate.apply(url, with_shim=False) == 0
        assert migrate.apply(url, with_shim=False) == 0                          # rerun: nothing re-applied
        with psycopg.connect(url) as c:
            assert c.execute("select count(*) from public.schema_migrations").fetchone()[0] == 2
            assert c.execute("select value from public._setuai_env where key='env'").fetchone()[0] == "integration"
        (mdir / "0001_a.sql").write_text("create table t1 (id int primary key, sneaky int);")   # edit an applied migration
        with pytest.raises(migrate.GuardError, match="immutable"):
            migrate.apply(url, with_shim=False)
        (mdir / "0001_a.sql").write_text("create table t1 (id int primary key);")
        (mdir / "0003_bad.sql").write_text("create table t3 (id int); select 1/0;")
        assert migrate.apply(url, with_shim=False) == 1                          # a failing migration reports failure...
        with psycopg.connect(url) as c:                                          # ...and leaves nothing half-applied
            assert c.execute("select to_regclass('public.t3') is null").fetchone()[0] is True
            assert c.execute("select count(*) from public.schema_migrations").fetchone()[0] == 2
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()


def test_the_repo_guard_accepts_this_database_and_it_is_not_the_old_one():
    from backend.testing import guard
    assert guard.check_env(dict(os.environ), str(ROOT / ".env")) == []
    host_old = migrate._old_shared_host()
    assert host_old is None or host_old != urlparse_host(os.environ["DATABASE_URL"])


def urlparse_host(u):
    from urllib.parse import urlparse
    return (urlparse(u).hostname or "").lower()


def test_database_carries_the_v2_fingerprint_and_not_old_schema_objects():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        assert c.execute("select value from public._setuai_env where key='schema'").fetchone()[0] == "sih-v2-baseline"
        # none of the old model's tables exist here: this is not the old schema with columns added
        old = c.execute("select count(*) from information_schema.tables where table_schema='public' and table_name in "
                        "('schedules','schedule_activities','approved_actuals','stages','execution_blockers','institutional_incidents')").fetchone()[0]
        assert old == 0


def test_no_secrets_in_the_new_database_files():
    bad = ("eyJ", "sbp_", "service_role_key", "SUPABASE_SERVICE_ROLE_KEY=", "password=")
    for f in list((ROOT / "db").rglob("*")) + [ROOT / "scripts" / "db_v2.sh"]:
        if f.is_file() and f.suffix in {".sql", ".py", ".sh", ".md"}:
            text = f.read_text()
            assert not any(b in text for b in bad), f
