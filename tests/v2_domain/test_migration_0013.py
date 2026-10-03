"""Migration 0013 as a MIGRATION: additive against the 0012 schema, refuses to rewrite append-only history, idempotent, and the commit-time completeness
guarantee of decisions - all on scratch / committed databases (these tests commit)."""
import importlib.util
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import psycopg.rows
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("migrate_v2m", ROOT / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)

from v2api import connect  # noqa: E402


def base():
    u = urlparse(os.environ["DATABASE_URL"])
    return f"{u.scheme}://{u.netloc}/"


@pytest.fixture
def scratch_dbs():
    admin = psycopg.connect(base() + "postgres", autocommit=True)
    names = ["setuai_v2_integ_mig_a", "setuai_v2_integ_mig_b"]
    for n in names:
        admin.execute(f"drop database if exists {n} with (force)")
        admin.execute(f"create database {n}")
    yield [base() + n for n in names]
    for n in names:
        admin.execute(f"drop database if exists {n} with (force)")
    admin.close()


def apply_subset(url, upto, tmp_path, monkeypatch):
    d = tmp_path / f"m_{upto}"
    d.mkdir(exist_ok=True)
    for f in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        if int(f.stem[:4]) <= upto:
            shutil.copy(f, d / f.name)
    monkeypatch.setattr(migrate, "MIGRATIONS", d)
    return migrate.apply(url, with_shim=True)


def catalog(url):
    with psycopg.connect(url, row_factory=psycopg.rows.dict_row) as c:
        q = lambda sql: c.execute(sql).fetchall()
        return {
            "tables": {r["t"] for r in q("select table_name t from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")},
            "columns": {(r["t"], r["c"]): (r["ty"], r["n"]) for r in q("select table_name t, column_name c, data_type ty, is_nullable n from information_schema.columns where table_schema='public'")},
            "constraints": {(r["t"], r["n"]) for r in q("select conrelid::regclass::text t, conname n from pg_constraint where connamespace='public'::regnamespace")},
            "indexes": {r["i"] for r in q("select indexname i from pg_indexes where schemaname='public'")},
            "functions": {(r["p"], r["a"]) for r in q("select proname p, pg_get_function_identity_arguments(oid) a from pg_proc where pronamespace='public'::regnamespace")},
            "views": {r["v"]: [x["c"] for x in c.execute("select column_name c from information_schema.columns where table_schema='public' and table_name=%s order by ordinal_position", (r["v"],)).fetchall()]
                      for r in q("select viewname v from pg_views where schemaname='public'")},
            "triggers": {(r["t"], r["n"]) for r in q("select tgrelid::regclass::text t, tgname n from pg_trigger where not tgisinternal")},
        }


def test_0013_is_additive_over_the_0012_schema(scratch_dbs, tmp_path, monkeypatch):
    a, b = scratch_dbs
    assert apply_subset(a, 12, tmp_path, monkeypatch) == 0
    monkeypatch.setattr(migrate, "MIGRATIONS", ROOT / "db" / "migrations")
    assert migrate.apply(b, with_shim=True) == 0
    before, after = catalog(a), catalog(b)
    assert before["tables"] <= after["tables"], "no table may disappear"
    for key, (ty, nullable) in before["columns"].items():
        assert key in after["columns"], f"column {key} disappeared"
        assert after["columns"][key][0] == ty, f"column {key} changed type"
        assert not (nullable == "YES" and after["columns"][key][1] == "NO"), f"column {key} became NOT NULL"
    assert before["indexes"] <= after["indexes"], f"indexes dropped: {before['indexes'] - after['indexes']}"
    assert before["functions"] <= after["functions"], f"functions dropped: {before['functions'] - after['functions']}"
    assert before["triggers"] <= after["triggers"], f"triggers dropped: {before['triggers'] - after['triggers']}"
    dropped = before["constraints"] - after["constraints"]
    assert {n for _, n in dropped} <= {"execution_events_status_check", "notifications_notification_type_check", "notifications_subject_chk"}, dropped    # widened, then re-added
    for v, cols in before["views"].items():
        assert after["views"][v][:len(cols)] == cols, f"view {v} columns changed"
    new_cols = {k for k in after["columns"] if k not in before["columns"]}
    assert ("planner_decisions", "method") in new_cols and ("approved_resource_progress", "prev_entry_id") in new_cols and ("execution_events", "withdrawn_at") in new_cols


def test_0013_refuses_to_rewrite_append_only_history_it_cannot_back_fill(scratch_dbs, tmp_path, monkeypatch, capsys):
    a, _ = scratch_dbs
    assert apply_subset(a, 12, tmp_path, monkeypatch) == 0
    with psycopg.connect(a, autocommit=True) as c:
        c.execute("set session_replication_role = replica")                          # bypass FKs / triggers just to plant a row 0013 must not touch
        c.execute("insert into planner_decisions (decision_id, project_id, event_id, action, justification, decided_by) values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'REJECT', 'old row', gen_random_uuid())")
    monkeypatch.setattr(migrate, "MIGRATIONS", ROOT / "db" / "migrations")
    assert migrate.apply(a, with_shim=False) == 1
    assert "needs empty decision / ledger tables" in capsys.readouterr().err
    with psycopg.connect(a) as c:
        assert c.execute("select count(*) from schema_migrations where version like '0013%'").fetchone()[0] == 0
        assert c.execute("select count(*) from planner_decisions").fetchone()[0] == 1                      # untouched
        assert c.execute("select count(*) from information_schema.columns where table_name='planner_decisions' and column_name='method'").fetchone()[0] == 0


def test_the_full_migration_set_is_idempotent(scratch_dbs, monkeypatch):
    _, b = scratch_dbs
    monkeypatch.setattr(migrate, "MIGRATIONS", ROOT / "db" / "migrations")
    assert migrate.apply(b, with_shim=True) == 0
    n = len(list((ROOT / "db" / "migrations").glob("*.sql")))
    assert migrate.apply(b, with_shim=False) == 0
    with psycopg.connect(b) as c:
        assert c.execute("select count(*) from schema_migrations").fetchone()[0] == n


# ------------------------------------------------------------------------------------------------ commit-time completeness
@pytest.fixture
def raw(kit):
    conns = []

    def new():
        c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
        c.execute("select set_config('app.actor_id', %s, true)", (str(kit.world.sup.id),))
        conns.append(c)
        return c
    yield new
    for c in conns:
        c.close()


def decision(c, kit, claim_id, action, method):
    return c.execute("insert into planner_decisions (project_id, event_id, selected_activity_uid, action, method, justification, decided_by) values (%s,%s,%s,%s,%s,'raw test',%s) returning decision_id",
                     (kit.project, claim_id, kit.uid("A2010"), action, method, kit.world.sup.id)).fetchone()["decision_id"]


def notification(c, kit, claim_id, d):
    c.execute("insert into notifications (project_id, recipient_id, notification_type, decision_id, event_id, title) values (%s,%s,'CLAIM_DECISION',%s,%s,'t')", (kit.project, kit.world.se.id, d, claim_id))


def audit_row(c, kit, d):
    from backend.v2 import audit
    audit.log(c, project_id=kit.project, actor_id=kit.world.sup.id, role="SUPERVISOR", action="RAW", entity_type="PLANNER_DECISION", entity_id=d)


def test_a_decision_cannot_commit_without_its_notification_and_audit_record(kit, raw):
    cid = kit.submit("A2010", 500, "joints")["claim_id"]
    c = raw(); d = decision(c, kit, cid, "REJECT", "NONE")
    with pytest.raises(psycopg.errors.CheckViolation, match="without notifying"):
        c.commit()
    c = raw(); d = decision(c, kit, cid, "REJECT", "NONE"); notification(c, kit, cid, d)
    with pytest.raises(psycopg.errors.CheckViolation, match="without its audit record"):
        c.commit()
    assert kit.count("planner_decisions") == 0 and kit.count("notifications", "notification_type = 'CLAIM_DECISION'") == 0         # nothing from the failed commits persisted
    c = raw(); d = decision(c, kit, cid, "REJECT", "NONE"); notification(c, kit, cid, d); audit_row(c, kit, d)
    c.commit()
    assert kit.count("planner_decisions") == 1


def test_an_approval_cannot_commit_without_its_ledger_rows(kit, raw):
    cid = kit.submit("A2010", 500, "joints")["claim_id"]
    c = raw(); d = decision(c, kit, cid, "APPROVE", "QUANTITIES_AS_CLAIMED"); notification(c, kit, cid, d); audit_row(c, kit, d)
    with pytest.raises(psycopg.errors.CheckViolation, match="without its progress entries"):
        c.commit()
    assert kit.count("planner_decisions") == 0 and kit.ledger() == ([], []) and kit.count("execution_events", "status = 'MATCHED'") == 1
    c = raw(); d = decision(c, kit, cid, "APPROVE", "QUANTITIES_AS_CLAIMED"); notification(c, kit, cid, d); audit_row(c, kit, d)
    c.execute("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty) values (%s,%s,%s,%s,current_date,500)",
              (kit.project, kit.uid("A2010"), kit.asg("A2010", "WELD_JOINTS"), d))
    c.commit()
    assert len(kit.ledger()[0]) == 1


def test_two_writers_cannot_both_finalise_one_claim(kit, raw):
    cid = kit.submit("A2010", 500, "joints")["claim_id"]
    a = raw(); da = decision(a, kit, cid, "REJECT", "NONE"); notification(a, kit, cid, da); audit_row(a, kit, da)         # A has not committed yet
    done, out = threading.Event(), []

    def writer_b():
        b = raw()
        try:
            db = decision(b, kit, cid, "APPROVE", "QUANTITIES_AS_CLAIMED")                                               # passes the status check: A is uncommitted
            b.commit()
            out.append("committed")
        except Exception as e:                                                                                          # noqa: BLE001
            out.append(e)
        finally:
            done.set()
    t = threading.Thread(target=writer_b); t.start()
    assert not done.wait(0.8), "B must wait for A on the unique index"
    a.commit()
    assert done.wait(10); t.join()
    assert isinstance(out[0], psycopg.errors.UniqueViolation) and "uq_decision_final_per_claim" in str(out[0])
    assert kit.count("planner_decisions", "action in ('APPROVE','EDIT','REJECT')") == 1 and kit.ledger() == ([], [])
