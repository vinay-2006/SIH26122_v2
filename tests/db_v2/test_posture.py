"""The posture verifier (Data API off, backend-only writes): passes on the real schema and DETECTS each deliberate breakage."""
import importlib.util
import re
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("posture_v2", ROOT / "db" / "posture.py")
posture = importlib.util.module_from_spec(spec)
sys.modules["posture_v2"] = posture                       # dataclasses need the module registered before it executes
spec.loader.exec_module(posture)
spec2 = importlib.util.spec_from_file_location("migrate_v2p", ROOT / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(migrate)

from v2world import server_base

BASE = server_base()


def failing(findings):
    return [f.check for f in findings if not f.ok]


def test_the_real_schema_passes_every_check(conn):
    f = posture.check_posture(conn)
    assert failing(f) == [] and len(f) >= 14


@pytest.fixture(scope="module")
def scratch():
    admin = psycopg.connect(BASE + "postgres", autocommit=True)
    name = "setuai_v2_integ_posture_scratch"
    admin.execute(f"drop database if exists {name} with (force)")
    admin.execute(f"create database {name}")
    assert migrate.apply(BASE + name, with_shim=True) == 0
    yield BASE + name
    admin.execute(f"drop database if exists {name} with (force)")
    admin.close()


BREAKAGES = [
    ("alter table projects disable row level security", "row-level security enabled"),
    ("grant insert on projects to authenticated", "authenticated can only SELECT"),
    ("grant update (project_name) on projects to authenticated", "authenticated can only SELECT"),
    ("grant select on projects to anon", "anon has no privilege"),
    ("grant usage on sequence audit_logs_log_id_seq to authenticated", "authenticated has no sequence privilege"),
    ("create function public.evil() returns int language sql as 'select 1'; grant execute on function public.evil() to anon", "executable by anon or PUBLIC"),
    ("create function public.evil() returns int language sql as 'select 1'; grant execute on function public.evil() to authenticated", "only the RLS helper functions"),
    ("alter view v_project_progress reset (security_invoker)", "security_invoker"),
    ("create function public.sd() returns int language sql security definer as 'select 1'", "pins its search_path"),
    ("create policy leak on projects for select to authenticated using (true)", "USING (true)"),
    ("create policy w on projects for insert to authenticated with check (is_project_member(project_id))", "no write policy"),
    ("drop trigger trg_arp_append_only on approved_resource_progress", "append-only tables"),
    ("delete from public._setuai_env", "fingerprint"),
    ("delete from public.schema_migrations where version like '0005%'", "contiguous"),
]


@pytest.mark.parametrize("ddl,expected", BREAKAGES, ids=[b[1] + str(i) for i, b in enumerate(BREAKAGES)])
def test_each_deliberate_breakage_is_detected_by_name(scratch, ddl, expected):
    with psycopg.connect(scratch, prepare_threshold=None) as c:
        assert failing(posture.check_posture(c)) == []                  # clean before
        c.execute(ddl)                                                  # DDL is transactional: rolled back below
        bad = failing(posture.check_posture(c))
        assert any(expected in b for b in bad), (ddl, bad)
        c.rollback()
        assert failing(posture.check_posture(c)) == []                  # and clean after the rollback


def test_the_cli_is_read_only_and_exits_nonzero_on_failure(scratch, monkeypatch, capsys):
    monkeypatch.setenv("DB_V2_URL", scratch)
    assert posture.main() == 0
    with psycopg.connect(scratch, autocommit=True) as c:
        c.execute("grant insert on projects to authenticated")
    try:
        assert posture.main() == 1
        out = capsys.readouterr().out
        assert "FAIL authenticated can only SELECT" in out and "postgresql://" not in out
    finally:
        with psycopg.connect(scratch, autocommit=True) as c:
            c.execute("revoke insert on projects from authenticated")
    monkeypatch.delenv("DB_V2_URL")
    assert posture.main() == 2


# ------------------------------------------------------------------------------------------------ PostgreSQL version
class FakeInfo:
    def __init__(self, v): self.server_version = v


class FakeConn:
    def __init__(self, v): self.info = FakeInfo(v)


def test_the_runner_refuses_servers_older_than_postgresql_15():
    migrate.require_min_pg(FakeConn(150000)); migrate.require_min_pg(FakeConn(170002)); migrate.require_min_pg(FakeConn(160015))
    with pytest.raises(migrate.GuardError, match="PostgreSQL 15 or newer"):
        migrate.require_min_pg(FakeConn(140010))


def test_static_review_nothing_newer_than_postgresql_15_is_used():
    """a STATIC scan only: it cannot prove PG15 compatibility (that needs a real PG15 server); it flags syntax introduced in 16 / 17"""
    newer = re.compile(r"\b(ANY_VALUE|JSON_OBJECT|JSON_ARRAY|JSON_ARRAYAGG|JSON_OBJECTAGG|JSON_SCALAR|JSON_SERIALIZE|JSON_QUERY|JSON_VALUE|JSON_EXISTS|JSON_TABLE|"
                       r"pg_input_is_valid|pg_input_error_info|random_normal|pg_get_acl|uuidv7|MERGE\s+.*\bRETURNING|ON_ERROR|IS\s+JSON|"
                       r"REINDEX\s+.*\bSYSTEM|FOR\s+PORTION\s+OF|pg_stat_io|SET\s+SCHEMA\s+.*\bCASCADE)\b", re.I)
    files = list((ROOT / "db" / "migrations").glob("*.sql")) + list((ROOT / "db").glob("*.py")) + list((ROOT / "backend" / "v2").rglob("*.py"))
    hits = [(str(f.relative_to(ROOT)), m.group(0)) for f in files for m in [newer.search(f.read_text())] if m and f.name != "posture.py"]
    assert hits == [], hits
