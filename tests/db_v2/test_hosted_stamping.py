"""The runner's marker rules for a HOSTED target, exercised on scratch local databases with a synthetic hosted Target (no network)."""
import psycopg
import pytest

import importlib.util
from pathlib import Path

from db import target_guard as tg

spec = importlib.util.spec_from_file_location("migrate_v2h", Path(__file__).resolve().parents[2] / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)

from v2world import server_base

BASE = server_base()
REF = "abcdefghijklmnopqrst"
HOSTED = tg.Target("supabase", f"db.{REF}.supabase.co", 5432, "postgres", "postgres", REF, "require")
OTHER = tg.Target("supabase", f"db.{REF[::-1]}.supabase.co", 5432, "postgres", "postgres", REF[::-1], "require")


@pytest.fixture
def scratch():
    admin = psycopg.connect(BASE + "postgres", autocommit=True)
    name = "setuai_v2_integ_hosted_scratch"
    admin.execute(f"drop database if exists {name} with (force)")
    admin.execute(f"create database {name}")
    c = psycopg.connect(BASE + name)
    yield c
    c.close()
    admin.execute(f"drop database if exists {name} with (force)")
    admin.close()


def marker(c):
    return dict(c.execute("select key, value from public._setuai_env").fetchall())


def test_a_brand_new_empty_database_is_stamped_once_with_its_project_ref(scratch):
    migrate.ensure_bookkeeping(scratch, HOSTED)
    scratch.commit()
    assert marker(scratch) == {"env": "hosted", "project_ref": REF, "schema": tg.SCHEMA_ID}
    migrate.ensure_bookkeeping(scratch, HOSTED)                       # second run: verified, not re-stamped
    scratch.commit()
    assert marker(scratch)["project_ref"] == REF


def test_the_same_database_is_refused_by_a_different_project_configuration(scratch):
    migrate.ensure_bookkeeping(scratch, HOSTED)
    scratch.commit()
    with pytest.raises(tg.GuardError, match="does not match"):
        migrate.ensure_bookkeeping(scratch, OTHER)


def test_a_database_with_tables_but_no_marker_is_never_stamped(scratch):
    scratch.execute("create table public.somebody_elses_table (id int)")
    scratch.commit()
    with pytest.raises(tg.GuardError, match="no fingerprint marker"):
        migrate.ensure_bookkeeping(scratch, HOSTED)
    scratch.rollback()
    assert scratch.execute("select to_regclass('public._setuai_env') is null").fetchone()[0]


def test_a_local_stamp_cannot_pass_as_hosted(scratch):
    local = tg.Target("local", "127.0.0.1", 54329, "setuai_v2_integ_hosted_scratch", "postgres", None, None)
    migrate.ensure_bookkeeping(scratch, local)
    scratch.commit()
    assert marker(scratch)["env"] == "integration"
    with pytest.raises(tg.GuardError, match="does not match"):
        migrate.ensure_bookkeeping(scratch, HOSTED)


def test_the_local_shim_is_never_applied_to_a_hosted_target(monkeypatch):
    monkeypatch.setenv("V2_ALLOW_HOSTED", "1")
    monkeypatch.setenv("V2_ALLOWED_PROJECT_REFS", REF)
    monkeypatch.setenv("V2_DENIED_PROJECT_REFS", "none")
    monkeypatch.setattr(migrate, "_old_shared_host", lambda: None)
    monkeypatch.setattr(tg, "old_project_refs", lambda: set())
    with pytest.raises(tg.GuardError, match="shim must never"):
        migrate.apply(f"postgresql://postgres:pw@db.{REF}.supabase.co:5432/postgres?sslmode=require", with_shim=True)


def test_the_runner_refuses_to_connect_to_a_hosted_target_that_is_not_allowed(monkeypatch):
    monkeypatch.delenv("V2_ALLOW_HOSTED", raising=False)
    monkeypatch.setattr(migrate, "_old_shared_host", lambda: None)
    for fn in (lambda u: migrate.apply(u, False), migrate.status):
        with pytest.raises(tg.GuardError, match="V2_ALLOW_HOSTED"):
            fn(f"postgresql://postgres:pw@db.{REF}.supabase.co:5432/postgres?sslmode=require")
