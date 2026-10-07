"""Append-only progress ledgers, quantity rules, over-baseline acknowledgement and derived rollups."""
from datetime import timedelta
from decimal import Decimal

import pytest

from v2world import (TODAY, approve_activity, approve_qty, as_actor, build_world, claim, decide, fails, one, sysmode, u)


def D(x):
    return Decimal(str(x))


def current_pct(conn, w, activity):
    return one(conn, "select physical_pct from v_activity_progress where activity_uid=%s", (activity.uid,))


# ------------------------------------------------------------------ ledger rules
def test_incremental_and_previous_are_derived_not_trusted(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 100)
    approve_qty(conn, w, w.a2, 0, 250)
    rows = conn.execute("select cumulative_qty, incremental_qty, prev_cumulative_qty, baseline_qty_at_entry from approved_resource_progress "
                        "where assignment_uid=%s order by entry_seq", (w.a2.assign[0],)).fetchall()
    assert [(r["cumulative_qty"], r["incremental_qty"], r["prev_cumulative_qty"]) for r in rows] == [(100, 100, 0), (250, 150, 100)]
    assert rows[0]["baseline_qty_at_entry"] == 500
    assert sum(r["incremental_qty"] for r in rows) == rows[-1]["cumulative_qty"]      # no double counting


def test_wrong_incremental_is_rejected(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 100)
    fails(conn, "select 1")  if False else None
    with pytest.raises(Exception, match="double counting"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 250, incremental=250)   # re-adding the whole cumulative as an increment


def test_cumulative_cannot_decrease_without_reopen_but_can_when_superseding(w, conn):
    sysmode(conn)
    first, _ = approve_qty(conn, w, w.a2, 0, 300)
    with pytest.raises(Exception, match="cannot decrease"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 200)
    with pytest.raises(Exception, match="supersede the latest"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 200, supersedes=u())
    approve_qty(conn, w, w.a2, 0, 200, supersedes=first)              # reopen / correction: explicit and traceable
    assert one(conn, "select cumulative_qty from v_current_resource_progress where assignment_uid=%s", (w.a2.assign[0],)) == 200
    assert one(conn, "select count(*) from approved_resource_progress where assignment_uid=%s", (w.a2.assign[0],)) == 2   # history kept


def test_stale_entry_is_rejected(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 100, as_of=TODAY)
    with pytest.raises(Exception, match="stale"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 150, as_of=TODAY - timedelta(days=3))


def test_ledgers_and_decisions_are_append_only(w, conn):
    sysmode(conn)
    eid, dec = approve_qty(conn, w, w.a2, 0, 100)
    fails(conn, "update approved_resource_progress set cumulative_qty=1 where entry_id=%s", (eid,), match="append-only")
    fails(conn, "delete from approved_resource_progress where entry_id=%s", (eid,), match="append-only")
    fails(conn, "update planner_decisions set justification='edited later' where decision_id=%s", (dec,), match="append-only")
    fails(conn, "delete from planner_decisions where decision_id=%s", (dec,), match="append-only")
    aid, _ = approve_activity(conn, w, w.a3, start=TODAY - timedelta(days=5), pct=50)
    fails(conn, "update approved_activity_progress set reported_pct=99 where entry_id=%s", (aid,), match="append-only")
    conn.execute("insert into audit_logs (project_id,actor_id,action,entity_type,entity_id,payload_hash,previous_hash,current_hash) "
                 "values (%s,%s,'TEST','X','1','h','p',%s)", (w.project, w.sup, "c" + u().hex))
    fails(conn, "update audit_logs set action='FORGED'", match="append-only")
    fails(conn, "delete from audit_logs", match="append-only")


def test_only_approve_or_edit_decisions_create_progress(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    dec = decide(conn, w, ev, w.a2, action="REJECT")
    with pytest.raises(Exception, match="APPROVE/EDIT"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 100, event=ev) if False else conn.execute(
                "insert into approved_resource_progress (project_id,activity_uid,assignment_uid,decision_id,as_of_date,cumulative_qty) "
                "values (%s,%s,%s,%s,%s,100)", (w.project, w.a2.uid, w.a2.assign[0], dec, TODAY))
    assert one(conn, "select status from execution_events where event_id=%s", (ev,)) == "REJECTED"
    assert one(conn, "select count(*) from approved_resource_progress") == 0


def test_decision_drives_claim_status_and_rejects_bad_transitions(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2, status="MATCHED")
    decide(conn, w, ev, w.a2, action="HOLD")
    assert one(conn, "select status from execution_events where event_id=%s", (ev,)) == "DISPUTED"
    decide(conn, w, ev, w.a2, action="APPROVE")
    assert one(conn, "select status from execution_events where event_id=%s", (ev,)) == "APPROVED"
    fails(conn, "update execution_events set status='EXTRACTED' where event_id=%s", (ev,), match="illegal claim status")
    with pytest.raises(Exception, match="cannot be decided"):
        with conn.transaction():
            decide(conn, w, ev, w.a2)                                   # already APPROVED


def test_decision_cannot_precede_the_claim(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    with pytest.raises(Exception, match="cannot precede"):
        with conn.transaction():
            decide(conn, w, ev, w.a2, decided_at=conn.execute("select now() - interval '2 hours' t").fetchone()["t"])


def test_claim_provenance_is_immutable(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    fails(conn, "update execution_events set raw_claim_text='rewritten' where event_id=%s", (ev,), match="immutable")
    fails(conn, "update execution_events set filed_by=%s where event_id=%s", (w.sup, ev), match="immutable")
    fails(conn, "delete from execution_events where event_id=%s", (ev,), match="append-only")


def test_activity_ledger_rules(w, conn):
    sysmode(conn)
    s = TODAY - timedelta(days=20)
    first, _ = approve_activity(conn, w, w.a3, start=s, pct=40)
    with pytest.raises(Exception, match="cannot decrease"):
        with conn.transaction():
            approve_activity(conn, w, w.a3, pct=30)
    with pytest.raises(Exception, match="actual_start already approved"):
        with conn.transaction():
            approve_activity(conn, w, w.a3, start=s + timedelta(days=1), pct=50)
    with pytest.raises(Exception, match="future"):
        with conn.transaction():
            approve_activity(conn, w, w.a3, finish=TODAY + timedelta(days=3), pct=100)
    approve_activity(conn, w, w.a3, finish=TODAY - timedelta(days=1), pct=100)        # carries start forward
    cur = conn.execute("select * from v_current_activity_progress where activity_uid=%s", (w.a3.uid,)).fetchone()
    assert cur["actual_start"] == s and cur["reported_pct"] == 100
    sysmode(conn)
    ev = claim(conn, w, w.a2); dec = decide(conn, w, ev, w.a2)
    fails(conn, "insert into approved_activity_progress (project_id,activity_uid,decision_id,as_of_date,actual_finish) values (%s,%s,%s,%s,%s)",
          (w.project, w.a2.uid, dec, TODAY, TODAY), match="requires an actual_start|check")


# ------------------------------------------------------------------ over-baseline (K2)
def test_overrun_within_tolerance_is_flagged_but_needs_no_ack(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 540)                                  # 8 % over a 500 m3 baseline
    row = conn.execute("select over_baseline, overrun_pct, cumulative_qty from approved_resource_progress").fetchone()
    assert row["over_baseline"] is True and row["overrun_pct"] == D("8.000") and row["cumulative_qty"] == 540   # not clamped
    pa = conn.execute("select physical_pct, any_overrun from v_activity_progress where activity_uid=%s", (w.a2.uid,)).fetchone()
    assert pa["physical_pct"] == 100 and pa["any_overrun"] is True      # progress is capped at 100, the quantity is not


def test_overrun_beyond_tolerance_requires_supervisor_acknowledgement(w, conn):
    sysmode(conn)
    with pytest.raises(Exception, match="acknowledgement required"):
        with conn.transaction():
            approve_qty(conn, w, w.a2, 0, 600)                          # 20 % over, no ack
    with pytest.raises(Exception, match="acknowledgement required"):
        with conn.transaction():                                        # ack by someone other than the deciding supervisor
            approve_qty(conn, w, w.a2, 0, 600, ack_by=w.pm, ack_note="pm says fine")
    approve_qty(conn, w, w.a2, 0, 600, ack_by=w.sup, ack_note="Re-measured: additional blinding concrete, see MB-17")
    row = conn.execute("select cumulative_qty, overrun_pct, overrun_ack_by, overrun_ack_note from approved_resource_progress").fetchone()
    assert row["cumulative_qty"] == 600 and row["overrun_pct"] == 20 and row["overrun_ack_by"] == w.sup and "MB-17" in row["overrun_ack_note"]


def test_tolerance_is_a_project_setting(w, conn):
    sysmode(conn)
    conn.execute("update project_settings set over_baseline_tolerance_pct=25 where project_id=%s", (w.project,))
    approve_qty(conn, w, w.a2, 0, 600)                                  # 20 % now inside tolerance
    assert one(conn, "select count(*) from approved_resource_progress") == 1


# ------------------------------------------------------------------ rollups (K1)
def build_progress(conn, w):
    """a1: 5/10 km (w .6 -> 50 %) and 50/200 joints (w .4 -> 25 %) => 40 %.  a2: 250/500 m3 => 50 %.  a3: approved 100 %."""
    sysmode(conn)
    approve_qty(conn, w, w.a1, 0, 5)
    approve_qty(conn, w, w.a1, 1, 50)
    approve_qty(conn, w, w.a2, 0, 250)
    approve_activity(conn, w, w.a3, start=TODAY - timedelta(days=12), finish=TODAY - timedelta(days=2), pct=100)


def test_multi_assignment_activity_progress_is_weighted_and_units_never_added(w, conn):
    build_progress(conn, w)
    assert current_pct(conn, w, w.a1) == D("40.000")         # (0.6*50 + 0.4*25) / 1.0 ; km and joints never summed
    assert current_pct(conn, w, w.a2) == D("50.000")
    assert current_pct(conn, w, w.a3) == D("100.000")
    assert one(conn, "select progress_basis from v_activity_progress where activity_uid=%s", (w.a1.uid,)) == "QUANTITY"
    assert one(conn, "select progress_basis from v_activity_progress where activity_uid=%s", (w.a3.uid,)) == "APPROVED_PCT"


def test_manhours_do_not_contribute_to_physical_progress(w, conn):
    sysmode(conn)
    # a manhours assignment exists on a1 with measures_progress=false; even a huge "consumption" cannot move progress
    assert one(conn, "select measured_assignments from v_activity_progress where activity_uid=%s", (w.a1.uid,)) == 2
    assert current_pct(conn, w, w.a1) == 0


def test_rollups_by_project_stage_and_discipline_use_manhour_weights(w, conn):
    build_progress(conn, w)
    proj = conn.execute("select * from v_project_progress where project_id=%s", (w.project,)).fetchone()
    assert proj["weight_basis"] == "MANHOURS"
    assert proj["physical_pct"] == D("43.662")               # (5000*40 + 2000*50 + 100*100) / 7100
    assert (proj["completed"], proj["in_progress"], proj["not_started"]) == (1, 2, 0)
    stages = {r["wbs_code"]: r["physical_pct"] for r in conn.execute(
        "select wbs_code, physical_pct from v_wbs_progress where project_id=%s", (w.project,)).fetchall()}
    assert stages["S1"] == D("42.857") and stages["S2"] == D("100.000") and stages["ROOT"] == D("43.662") and stages["S1.A"] == D("42.857")
    disc = {r["discipline_code"]: r["physical_pct"] for r in conn.execute(
        "select discipline_code, physical_pct from v_discipline_progress where project_id=%s", (w.project,)).fetchall()}
    assert disc == {"PIPING": D("40.000"), "CIVIL": D("50.000"), "HSE": D("100.000")}


def test_weight_basis_falls_back_to_duration_when_manhours_are_missing(conn):
    w = build_world(conn, a3_manhours=False)
    build_progress(conn, w)
    proj = conn.execute("select * from v_project_progress where project_id=%s", (w.project,)).fetchone()
    assert proj["weight_basis"] == "DURATION"
    assert proj["physical_pct"] == D("51.429")               # (40*40 + 20*50 + 10*100) / 70


def test_pending_claims_never_count_as_progress(w, conn):
    sysmode(conn)
    for st in ("REPORTED", "EXTRACTED", "MATCHED", "VALIDATED"):
        claim(conn, w, w.a2, status=st)
    ev = claim(conn, w, w.a2); decide(conn, w, ev, w.a2, action="REJECT")
    ev2 = claim(conn, w, w.a2); decide(conn, w, ev2, w.a2, action="HOLD")
    assert one(conn, "select physical_pct from v_project_progress where project_id=%s", (w.project,)) == 0
    pend = {r["status"]: r["claims"] for r in conn.execute("select status, claims from v_pending_claims where project_id=%s", (w.project,)).fetchall()}
    assert pend == {"REPORTED": 1, "EXTRACTED": 1, "MATCHED": 1, "VALIDATED": 1, "DISPUTED": 1}


def test_start_and_finish_variance(w, conn):
    sysmode(conn)
    approve_activity(conn, w, w.a3, start=w.a3.start + timedelta(days=4), finish=w.a3.start + timedelta(days=16), pct=100)
    r = conn.execute("select start_variance_days, finish_variance_days from v_activity_progress where activity_uid=%s", (w.a3.uid,)).fetchone()
    assert r["start_variance_days"] == 4        # actual start - baseline start
    assert r["finish_variance_days"] == 6       # actual finish - baseline finish (baseline finish = start + 10)


def test_quantity_progress_without_a_recorded_start_is_in_progress_and_flagged(w, conn):
    sysmode(conn)
    approve_qty(conn, w, w.a2, 0, 100)
    r = conn.execute("select execution_state, start_date_unrecorded from v_activity_progress where activity_uid=%s", (w.a2.uid,)).fetchone()
    assert r["execution_state"] == "IN_PROGRESS" and r["start_date_unrecorded"] is True


def test_zero_execution_project_reports_zero_everywhere(conn):
    w = build_world(conn)
    sysmode(conn)
    assert one(conn, "select physical_pct from v_project_progress where project_id=%s", (w.project,)) == 0
    assert one(conn, "select max(physical_pct) from v_wbs_progress where project_id=%s", (w.project,)) == 0
    assert one(conn, "select max(physical_pct) from v_discipline_progress where project_id=%s", (w.project,)) == 0
    for t in ("execution_events", "planner_decisions", "approved_activity_progress", "approved_resource_progress", "notifications"):
        assert one(conn, f"select count(*) from {t} where project_id=%s", (w.project,)) == 0
