"""Phase 1: the schema builds, is fully constrained, immutable where required, and RLS-protected by default."""
from datetime import timedelta

import pytest

from v2world import TODAY, add_activity, as_actor, build_world, fails, one, sysmode, u


def test_all_migrations_recorded_and_marker_present(conn):
    rows = [r["version"] for r in conn.execute("select version from public.schema_migrations order by version").fetchall()]
    assert rows == sorted(rows) and len(rows) == 8 and rows[0].startswith("0001_") and rows[-1].startswith("0008_")
    assert one(conn, "select value from public._setuai_env where key='env'") == "integration"


def test_every_public_table_has_rls_and_anon_has_no_access(conn):
    tables = conn.execute("select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                          "where n.nspname='public' and c.relkind='r'").fetchall()
    assert len(tables) >= 40
    assert [t["relname"] for t in tables if not t["relrowsecurity"]] == []
    for t in tables:
        assert not one(conn, "select has_table_privilege('anon', %s, 'SELECT')", (f"public.{t['relname']}",)), t["relname"]
        assert not one(conn, "select has_table_privilege('authenticated', %s, 'INSERT')", (f"public.{t['relname']}",)), t["relname"]


def test_project_master_view_exposes_canonical_baseline_fields(w, conn):
    row = conn.execute("select * from project_master where project_id=%s", (w.project,)).fetchone()
    assert row["baseline_name"] == "Baseline Rev 0" and row["baseline_locked_at"] is not None
    assert {"data_date", "planned_start_date", "planned_finish_date"} <= set(row)


def test_external_activity_id_unique_per_version_but_reusable_across_projects(w, conn):
    sysmode(conn)
    fails(conn, "insert into baseline_activities (project_id,version_id,activity_uid,external_activity_id,wbs_id,activity_name,"
                "discipline_code,baseline_duration,baseline_start,baseline_finish) select project_id,version_id,activity_uid,"
                "'A1000',wbs_id,'dup','CIVIL',1,baseline_start,baseline_finish from baseline_activities limit 1",
          match="immutable|locked")  # locked version blocks first; uniqueness is exercised on a draft below
    # a second project reuses the same external id A1000 with no conflict
    v = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by) values (%s,%s,1,'BASELINE','Base',%s,%s,%s,%s)",
                 (v, w.project_b, TODAY, TODAY, TODAY + timedelta(days=9), w.pm2))
    root = u()
    conn.execute("insert into schedule_wbs (wbs_id,project_id,version_id,wbs_code,wbs_name,node_type) values (%s,%s,%s,'R','R','PROJECT')",
                 (root, w.project_b, v))
    class P: project = w.project_b
    a = add_activity(conn, P, v, "A1000", "Same external id, different project", root, "CIVIL", 0, 5, [])
    assert a.uid != w.a1.uid
    fails(conn, "insert into baseline_activities (project_id,version_id,activity_uid,external_activity_id,wbs_id,activity_name,"
                "discipline_code,baseline_duration,baseline_start,baseline_finish) values (%s,%s,%s,'A1000',%s,'x','CIVIL',1,%s,%s)",
          (w.project_b, v, a.uid, root, TODAY, TODAY), match="uq_ba_external|duplicate key")


def test_activity_row_checks(w, conn):
    sysmode(conn)
    v = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by,parent_version_id) values (%s,%s,2,'REVISION','Rev 1',%s,%s,%s,%s,%s)",
                 (v, w.project, TODAY, TODAY, TODAY + timedelta(days=9), w.pm, w.v1))
    root = u()
    conn.execute("insert into schedule_wbs (wbs_id,project_id,version_id,wbs_code,wbs_name,node_type) values (%s,%s,%s,'R','R','PROJECT')",
                 (root, w.project, v))
    uid = u()
    conn.execute("insert into activities (activity_uid,project_id,first_version_id) values (%s,%s,%s)", (uid, w.project, v))
    base = ("insert into baseline_activities (project_id,version_id,activity_uid,external_activity_id,wbs_id,activity_name,"
            "discipline_code,activity_type,baseline_duration,baseline_start,baseline_finish) values (%s,%s,%s,'X1',%s,'n',%s,%s,%s,%s,%s)")
    fails(conn, base, (w.project, v, uid, root, "CIVIL", "TASK", 3, TODAY, TODAY - timedelta(days=1)), match="check")
    fails(conn, base, (w.project, v, uid, root, "CIVIL", "TASK", -1, TODAY, TODAY), match="check")
    fails(conn, base, (w.project, v, uid, root, "CIVIL", "MILESTONE", 2, TODAY, TODAY), match="check")
    fails(conn, base, (w.project, v, uid, root, "NOT_A_DISCIPLINE", "TASK", 2, TODAY, TODAY), match="foreign key")


def test_is_critical_is_derived_from_float(w, conn):
    rows = {r["external_activity_id"]: r["is_critical"] for r in conn.execute(
        "select external_activity_id,is_critical from baseline_activities where version_id=%s", (w.v1,)).fetchall()}
    assert rows == {"A1000": True, "A1010": False, "A2000": False}


def test_wbs_hierarchy_path_and_cross_version_parent_rejected(w, conn):
    sysmode(conn)
    assert one(conn, "select wbs_path from schedule_wbs where wbs_id=%s", (w.s1a,)) == "/ROOT/S1/S1.A/"
    assert one(conn, "select level from schedule_wbs where wbs_id=%s", (w.s1a,)) == 2
    v = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by,parent_version_id) values (%s,%s,2,'REVISION','Rev 1',%s,%s,%s,%s,%s)",
                 (v, w.project, TODAY, TODAY, TODAY + timedelta(days=9), w.pm, w.v1))
    fails(conn, "insert into schedule_wbs (project_id,version_id,parent_wbs_id,wbs_code,wbs_name,node_type) values (%s,%s,%s,'X','x','AREA')",
          (w.project, v, w.s1), match="not found|foreign key")
    fails(conn, "insert into schedule_wbs (project_id,version_id,wbs_code,wbs_name,node_type) values (%s,%s,'R2','r','PROJECT')",
          (w.project, w.v1), match="immutable|single_root|locked")


def test_single_active_and_single_baseline_per_project(w, conn):
    sysmode(conn)
    v2 = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by,parent_version_id) values (%s,%s,2,'REVISION','Rev 1',%s,%s,%s,%s,%s)",
                 (v2, w.project, TODAY, TODAY, TODAY + timedelta(days=9), w.pm, w.v1))
    conn.execute("update schedule_versions set status='VALIDATED' where version_id=%s", (v2,))
    conn.execute("update schedule_versions set locked_at=now(), locked_by=%s where version_id=%s", (w.pm, v2))
    fails(conn, "update schedule_versions set status='ACTIVE' where version_id=%s", (v2,), match="uq_one_active")
    fails(conn, "insert into schedule_versions (project_id,version_no,kind,baseline_name,data_date,planned_start_date,planned_finish_date,"
                "created_by) values (%s,3,'BASELINE','Second baseline',%s,%s,%s,%s)",
          (w.project, TODAY, TODAY, TODAY + timedelta(days=9), w.pm), match="uq_one_baseline|check")


def test_locked_baseline_is_immutable_and_lifecycle_is_ordered(w, conn):
    sysmode(conn)
    fails(conn, "update baseline_activities set activity_name='renamed' where version_id=%s", (w.v1,), match="immutable")
    fails(conn, "delete from baseline_activities where version_id=%s", (w.v1,), match="immutable")
    fails(conn, "update baseline_resources set baseline_qty=1 where version_id=%s", (w.v1,), match="immutable")
    fails(conn, "update schedule_versions set baseline_name='tamper' where version_id=%s", (w.v1,), match="locked")
    fails(conn, "delete from schedule_versions where version_id=%s", (w.v1,), match="cannot be deleted")
    fails(conn, "update schedule_versions set status='DRAFT' where version_id=%s", (w.v1,), match="illegal")
    d = build_world(conn, do_activate=False)   # a DRAFT can be freely edited, but cannot jump straight to ACTIVE
    sysmode(conn)
    conn.execute("update baseline_activities set activity_name='fine in draft' where version_id=%s", (d.v1,))
    fails(conn, "update schedule_versions set status='ACTIVE' where version_id=%s", (d.v1,), match="illegal")


def test_resource_semantics(w, conn):
    sysmode(conn)
    d = build_world(conn, do_activate=False)
    sysmode(conn)
    fails(conn, "update baseline_resources set measures_progress=true where version_id=%s and unit_of_measure='MH'", (d.v1,),
          match="cannot measure physical progress")
    fails(conn, "update baseline_resources set unit_of_measure='M3' where version_id=%s and unit_of_measure='KM'", (d.v1,),
          match="incompatible")
    fails(conn, "update baseline_resources set baseline_qty=0 where version_id=%s", (d.v1,), match="check")
    fails(conn, "update baseline_resources set progress_weight=0 where version_id=%s", (d.v1,), match="check")


def test_stage_rules_exist_only_on_stage_nodes_and_dependencies_are_sane(conn):
    d = build_world(conn, do_activate=False)
    sysmode(conn)
    conn.execute("insert into wbs_stage_rules (wbs_id,project_id,version_id,weight_pct) values (%s,%s,%s,40)", (d.s1, d.project, d.v1))
    fails(conn, "insert into wbs_stage_rules (wbs_id,project_id,version_id,weight_pct) values (%s,%s,%s,40)", (d.s1a, d.project, d.v1),
          match="foreign key")                                              # an AREA node cannot carry stage rules
    fails(conn, "insert into wbs_stage_rules (wbs_id,project_id,version_id,weight_pct) values (%s,%s,%s,140)", (d.s2, d.project, d.v1), match="check")
    dep = "insert into schedule_dependencies (project_id,version_id,predecessor_uid,successor_uid,relationship_type,lag_days) values (%s,%s,%s,%s,%s,0)"
    conn.execute(dep, (d.project, d.v1, d.a1.uid, d.a2.uid, "FS"))
    fails(conn, dep, (d.project, d.v1, d.a1.uid, d.a2.uid, "FS"), match="uq_dep|duplicate")
    fails(conn, dep, (d.project, d.v1, d.a1.uid, d.a1.uid, "FS"), match="check")
    fails(conn, dep, (d.project, d.v1, d.a1.uid, d.a2.uid, "XX"), match="check")
    fails(conn, dep, (d.project, d.v1, d.a1.uid, u(), "FS"), match="foreign key")      # successor must be in the same version
