"""Schedule revision, stable identity, activation gate and historical preservation."""
from datetime import timedelta
from decimal import Decimal

import pytest

from v2world import TODAY, approve_activity, approve_qty, claim, fails, one, sysmode, u


def revise(conn, w, rename=None, qty=None, drop_activities=(), drop_assignments=(), kind="REVISION", parent=None):
    """Create a DRAFT revision of v1 reusing the SAME activity_uid / assignment_uid / wbs_uid (what reconciliation produces)."""
    sysmode(conn)
    rename, qty = rename or {}, qty or {}
    v2 = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by,parent_version_id) values (%s,%s,%s,%s,'Revised plan',%s,%s,%s,%s,%s)",
                 (v2, w.project, 2 + one(conn, "select count(*) from schedule_versions where project_id=%s and version_no>1", (w.project,)),
                  kind, TODAY, TODAY - timedelta(days=110), TODAY + timedelta(days=230), w.pm, parent or w.v1))
    idmap = {}
    for r in conn.execute("select * from schedule_wbs where version_id=%s order by level", (w.v1,)).fetchall():
        nid = u(); idmap[r["wbs_id"]] = nid
        conn.execute("insert into schedule_wbs (wbs_id,project_id,version_id,wbs_uid,parent_wbs_id,wbs_code,wbs_name,node_type,sequence) "
                     "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (nid, w.project, v2, r["wbs_uid"], idmap.get(r["parent_wbs_id"]), r["wbs_code"], r["wbs_name"], r["node_type"], r["sequence"]))
    for a in conn.execute("select * from baseline_activities where version_id=%s", (w.v1,)).fetchall():
        if a["activity_uid"] in drop_activities:
            continue
        row = u()
        conn.execute("insert into baseline_activities (activity_row_id,project_id,version_id,activity_uid,external_activity_id,wbs_id,activity_name,"
                     "discipline_code,activity_type,baseline_duration,baseline_start,baseline_finish,total_float) "
                     "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (row, w.project, v2, a["activity_uid"], rename.get(a["activity_uid"], a["external_activity_id"]), idmap[a["wbs_id"]],
                      a["activity_name"], a["discipline_code"], a["activity_type"], a["baseline_duration"],
                      a["baseline_start"] + timedelta(days=7), a["baseline_finish"] + timedelta(days=7), a["total_float"]))
        for b in conn.execute("select * from baseline_resources where activity_row_id=%s", (a["activity_row_id"],)).fetchall():
            if b["assignment_uid"] in drop_assignments:
                continue
            conn.execute("insert into baseline_resources (assignment_uid,project_id,version_id,activity_row_id,activity_uid,resource_id,"
                         "baseline_qty,unit_of_measure,measures_progress,progress_weight) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (b["assignment_uid"], w.project, v2, row, b["activity_uid"], b["resource_id"],
                          qty.get(b["assignment_uid"], b["baseline_qty"]), b["unit_of_measure"], b["measures_progress"], b["progress_weight"]))
    return v2


def switch(conn, w, old, new):
    sysmode(conn)
    conn.execute("update schedule_versions set status='SUPERSEDED' where version_id=%s and status='ACTIVE'", (old,))
    conn.execute("update schedule_versions set status='VALIDATED' where version_id=%s and status='DRAFT'", (new,))
    conn.execute("update schedule_versions set locked_at=now(), locked_by=%s where version_id=%s and locked_at is null", (w.pm, new))
    conn.execute("update schedule_versions set status='ACTIVE', activated_at=now(), activated_by=%s where version_id=%s", (w.pm, new))


def seed_progress(conn, w):
    sysmode(conn)
    approve_qty(conn, w, w.a1, 0, 5)
    approve_qty(conn, w, w.a1, 1, 50)
    approve_qty(conn, w, w.a2, 0, 250)
    approve_activity(conn, w, w.a3, start=TODAY - timedelta(days=12), finish=TODAY - timedelta(days=2), pct=100)


def ledger_fingerprint(conn):
    return conn.execute("select entry_id, assignment_uid, cumulative_qty, incremental_qty, baseline_qty_at_entry from approved_resource_progress "
                        "order by entry_seq").fetchall(), conn.execute("select entry_id, activity_uid, actual_start, actual_finish, reported_pct "
                        "from approved_activity_progress order by entry_seq").fetchall()


def test_external_id_change_keeps_identity_and_all_approved_progress(w, conn):
    seed_progress(conn, w)
    before = ledger_fingerprint(conn)
    ev = claim(conn, w, w.a2)                                                  # a claim filed under v1
    v2 = revise(conn, w, rename={w.a1.uid: "PL-0100"})
    switch(conn, w, w.v1, v2)
    assert ledger_fingerprint(conn) == before                                  # nothing erased, nothing rewritten
    r = conn.execute("select external_activity_id, physical_pct from v_activity_progress where activity_uid=%s", (w.a1.uid,)).fetchone()
    assert r["external_activity_id"] == "PL-0100" and r["physical_pct"] == Decimal("40.000")      # same activity, new external id
    assert one(conn, "select filed_in_version_id from execution_events where event_id=%s", (ev,)) == w.v1   # claim provenance kept
    assert one(conn, "select status from schedule_versions where version_id=%s", (w.v1,)) == "SUPERSEDED"
    assert one(conn, "select physical_pct from v_project_progress where project_id=%s", (w.project,)) == Decimal("43.662")


def test_activation_blocked_while_executed_activity_is_unaccounted_for(w, conn):
    seed_progress(conn, w)
    v2 = revise(conn, w, drop_activities=(w.a2.uid,))                           # revision silently drops an executed activity
    with pytest.raises(Exception, match="approved progress exists for activities absent"):
        with conn.transaction():
            switch(conn, w, w.v1, v2)
    assert one(conn, "select status from schedule_versions where version_id=%s", (w.v1,)) == "ACTIVE"      # still the active version
    # an unconfirmed lineage row is not enough; a confirmed retirement is
    sysmode(conn)
    conn.execute("insert into activity_lineage (project_id,version_id,from_activity_uid,to_activity_uid,relation) values (%s,%s,%s,null,'RETIRED')",
                 (w.project, v2, w.a2.uid))
    with pytest.raises(Exception, match="approved progress exists"):
        with conn.transaction():
            switch(conn, w, w.v1, v2)
    conn.execute("update activity_lineage set confirmed_by=%s, confirmed_at=now() where version_id=%s", (w.pm, v2))
    before = ledger_fingerprint(conn)
    switch(conn, w, w.v1, v2)
    assert ledger_fingerprint(conn) == before                                   # history retained for the retired activity
    assert one(conn, "select count(*) from v_activity_progress where activity_uid=%s", (w.a2.uid,)) == 0   # no longer in scope
    boom = pytest.raises(Exception, match="not part of the project's active schedule")
    with boom:
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 300)                                  # cannot add new progress to a retired activity


def test_dropped_assignment_with_progress_blocks_activation(w, conn):
    seed_progress(conn, w)
    v2 = revise(conn, w, drop_assignments=(w.a1.assign[1],))                     # weld-joint assignment removed but has approved qty
    with pytest.raises(Exception, match="assignment with approved quantities is missing"):
        with conn.transaction():
            switch(conn, w, w.v1, v2)


def test_unexecuted_activities_can_be_dropped_or_added_freely(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 100)
    v2 = revise(conn, w, drop_activities=(w.a3.uid,))                            # a3 has no progress
    switch(conn, w, w.v1, v2)
    assert one(conn, "select count(*) from v_activity_progress where project_id=%s", (w.project,)) == 2


def test_rollback_to_previous_version_is_possible_and_lossless(w, conn):
    seed_progress(conn, w)
    before = ledger_fingerprint(conn)
    v2 = revise(conn, w, rename={w.a1.uid: "PL-0100"})
    switch(conn, w, w.v1, v2)
    sysmode(conn)
    conn.execute("update schedule_versions set status='SUPERSEDED' where version_id=%s", (v2,))
    conn.execute("update schedule_versions set status='ACTIVE', activated_at=now(), activated_by=%s where version_id=%s", (w.pm, w.v1))
    assert one(conn, "select external_activity_id from v_activity_progress where activity_uid=%s", (w.a1.uid,)) == "A1000"
    assert ledger_fingerprint(conn) == before


def test_changed_baseline_quantity_changes_percent_but_preserves_the_original_approval(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 250)                                            # 250 / 500 m3 = 50 %
    v2 = revise(conn, w, qty={w.a2.assign[0]: 1000})                              # scope doubled in the revision
    diff = conn.execute("select * from version_assignment_diff(%s,%s) where assignment_uid=%s", (w.v1, v2, w.a2.assign[0])).fetchone()
    assert (diff["old_qty"], diff["new_qty"], diff["change_kind"]) == (500, 1000, "CHANGED")
    switch(conn, w, w.v1, v2)
    assert one(conn, "select physical_pct from v_activity_progress where activity_uid=%s", (w.a2.uid,)) == Decimal("25.000")
    entry = conn.execute("select cumulative_qty, baseline_qty_at_entry from approved_resource_progress").fetchone()
    assert (entry["cumulative_qty"], entry["baseline_qty_at_entry"]) == (250, 500)  # the quantity and denominator approved then are preserved
    approve_qty(conn, w, w.a2, 0, 400)                                            # new approvals use the NEW denominator
    assert one(conn, "select baseline_qty_at_entry from approved_resource_progress order by entry_seq desc limit 1") == 1000


def test_version_activity_diff_classifies_planned_scope_changes(w, conn):
    v2 = revise(conn, w, rename={w.a1.uid: "PL-0100"}, drop_activities=(w.a3.uid,))
    kinds = {r["activity_uid"]: r for r in conn.execute("select * from version_activity_diff(%s,%s)", (w.v1, v2)).fetchall()}
    assert kinds[w.a1.uid]["change_kind"] == "CHANGED" and (kinds[w.a1.uid]["old_external_id"], kinds[w.a1.uid]["new_external_id"]) == ("A1000", "PL-0100")
    assert kinds[w.a1.uid]["start_shift_days"] == 7 and kinds[w.a1.uid]["finish_shift_days"] == 7
    assert kinds[w.a3.uid]["change_kind"] == "REMOVED"


def test_claims_only_against_activated_versions_and_history_stays_with_old_version(w, conn):
    v2 = revise(conn, w)
    with pytest.raises(Exception, match="activated schedule version"):
        with conn.transaction():
            claim(conn, w, w.a2, version=v2)                                       # v2 is still a DRAFT
    old_claim = claim(conn, w, w.a2)
    switch(conn, w, w.v1, v2)
    claim(conn, w, w.a2, version=v2)
    claim(conn, w, w.a2, version=w.v1)                                             # SUPERSEDED versions remain valid provenance
    assert one(conn, "select filed_in_version_id from execution_events where event_id=%s", (old_claim,)) == w.v1


def test_superseded_version_stays_queryable_and_undeletable(w, conn):
    v2 = revise(conn, w)
    switch(conn, w, w.v1, v2)
    assert one(conn, "select count(*) from baseline_activities where version_id=%s", (w.v1,)) == 3
    fails(conn, "delete from schedule_versions where version_id=%s", (w.v1,), match="cannot be deleted")
    sysmode(conn)
    conn.execute("delete from schedule_versions where version_id=%s", (revise(conn, w),))   # an unlocked DRAFT can be discarded, with its rows
