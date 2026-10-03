"""Migration 0013 constraints, exercised directly in SQL (rolled back): claim lifecycle, who may change what, quantity binding, evidence, chain links."""
import psycopg
import pytest

from v2world import TODAY, add_activity, approve_qty, as_actor, claim, decide, doc, fails, one, sysmode, u


def cq(conn, w, event, assignment=None, qty=10, uom="KM", basis="CUMULATIVE", norm=None):
    cid = u()
    conn.execute("insert into claim_quantities (claim_quantity_id, project_id, event_id, assignment_uid, qty_basis, reported_qty, reported_uom, normalized_uom, normalized_qty) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (cid, w.project, event, assignment, basis, qty, uom, uom if norm is not None else None, norm))
    return cid


# ------------------------------------------------------------------------------------------------ decisions
def test_a_decision_must_state_how_progress_was_derived(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    ins = ("insert into planner_decisions (project_id,event_id,selected_activity_uid,action,method,justification,decided_by) values (%s,%s,%s,%s,%s,'a reason',%s)")
    fails(conn, ins, (w.project, ev, w.a2.uid, "APPROVE", "NONE", w.sup), match="method_pairing")             # an approval without a method
    fails(conn, ins, (w.project, ev, w.a2.uid, "REJECT", "MANUAL_QUANTITIES", w.sup), match="method_pairing")   # a rejection with one
    fails(conn, ins, (w.project, ev, w.a2.uid, "APPROVE", "GUESS", w.sup), match="method_check")
    fails(conn, "insert into planner_decisions (project_id,event_id,selected_activity_uid,action,justification,decided_by) values (%s,%s,%s,'APPROVE','x reason',%s)",
          (w.project, ev, w.a2.uid, w.sup), match="null value")                                                  # no default: the method is never implied


def test_hold_may_repeat_but_a_final_decision_is_unique_per_claim(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    decide(conn, w, ev, w.a2, action="HOLD"); decide(conn, w, ev, w.a2, action="HOLD")
    decide(conn, w, ev, w.a2, action="REJECT")
    assert one(conn, "select count(*) from planner_decisions where event_id = %s", (ev,)) == 3
    fails(conn, "insert into planner_decisions (project_id,event_id,selected_activity_uid,action,method,justification,decided_by) values (%s,%s,%s,'APPROVE','QUANTITIES_AS_CLAIMED','again',%s)",
          (w.project, ev, w.a2.uid, w.sup), match="cannot be decided|uq_decision_final")


def test_the_decision_to_claim_link_tells_which_claims_are_approved(w, conn):
    sysmode(conn)
    ok, held, rej = claim(conn, w, w.a2), claim(conn, w, w.a2), claim(conn, w, w.a1)
    d1, d2, d3 = decide(conn, w, ok, w.a2), decide(conn, w, held, w.a2, action="HOLD"), decide(conn, w, rej, w.a1, action="REJECT")
    f = lambda d: one(conn, "select decision_claim_is_approved(%s,%s)", (w.project, d))
    assert (f(d1), f(d2), f(d3)) == (True, False, False)


# ------------------------------------------------------------------------------------------------ claims: withdrawal, freeze, who may change
def test_withdrawal_rules(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    fails(conn, "update execution_events set status='WITHDRAWN' where event_id=%s", (ev,), match="chk_event_withdrawn")             # needs a time and a reason
    fails(conn, "update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='x' where event_id=%s", (ev,), match="chk_event_withdrawn")
    conn.execute("update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='Entered on the wrong day' where event_id=%s", (ev,))
    fails(conn, "update execution_events set status='MATCHED', withdrawn_at=null, withdrawn_reason=null where event_id=%s", (ev,), match="frozen|illegal")
    fails(conn, "update execution_events set claimed_pct = 5 where event_id=%s", (ev,), match="frozen")
    with pytest.raises(Exception, match="cannot be decided|WITHDRAWN"):
        with conn.transaction():
            decide(conn, w, ev, w.a2)
    fails(conn, "insert into execution_events (project_id,filed_in_version_id,event_date,raw_claim_text,input_channel,filed_by,status,withdrawn_at,withdrawn_reason) values (%s,%s,current_date,'x','TYPED',%s,'WITHDRAWN',now(),'born withdrawn')",
          (w.project, w.v1, w.se), match="cannot be created withdrawn")


def test_who_may_touch_a_claim(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    for who, ok in ((w.se, True), (w.sup, True), (w.pm, False), (w.outsider, False), (w.pm2, False)):
        as_actor(conn, who)
        if ok:
            conn.execute("update execution_events set priority_score = priority_score + 1 where event_id=%s", (ev,))
        else:
            fails(conn, "update execution_events set priority_score = 99 where event_id=%s", (ev,), match="only the filing site engineer or a supervisor")
    as_actor(conn, w.sup)                                                       # a supervisor may not withdraw on the engineer's behalf
    fails(conn, "update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='by supervisor' where event_id=%s", (ev,), match="only the engineer who filed")
    as_actor(conn, w.se)
    conn.execute("update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='my own claim' where event_id=%s", (ev,))


def test_a_correction_must_link_to_the_engineers_own_rejected_claim(w, conn):
    sysmode(conn)
    first = claim(conn, w, w.a2)
    ins = ("insert into execution_events (project_id,filed_in_version_id,event_date,raw_claim_text,input_channel,filed_by,status,resubmits_event_id) "
           "values (%s,%s,current_date,%s,'TYPED',%s,'MATCHED',%s) returning event_id")
    fails(conn, ins, (w.project, w.v1, "correction attempt", w.se, first), match="REJECTED claim")                  # not rejected yet
    decide(conn, w, first, w.a2, action="REJECT")
    other = make_se2(conn, w)
    fails(conn, ins, (w.project, w.v1, "someone else's correction", other, first), match="REJECTED claim")          # not the same engineer
    fix = conn.execute(ins, (w.project, w.v1, "my correction", w.se, first)).fetchone()["event_id"]
    fails(conn, ins, (w.project, w.v1, "second correction", w.se, first), match="uq_event_one_correction")           # one live correction per rejection
    conn.execute("update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='wrong again' where event_id=%s", (fix,))
    conn.execute(ins, (w.project, w.v1, "third try", w.se, first))                                                  # a withdrawn correction frees the slot
    fails(conn, "update execution_events set resubmits_event_id = null where event_id=%s", (fix,), match="frozen|immutable")


def make_se2(conn, w):
    from v2world import make_member, make_user
    se2 = make_user(conn, "se2")
    make_member(conn, w.project, se2, "SITE_ENGINEER", w.pm)
    return se2


# ------------------------------------------------------------------------------------------------ claim quantities and evidence
def test_reported_quantities_are_immutable_but_binding_may_be_set_before_a_final_decision(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a1)
    q = cq(conn, w, ev, None, qty=5, uom="KM")
    conn.execute("update claim_quantities set assignment_uid=%s, normalized_uom='KM', normalized_qty=5 where claim_quantity_id=%s", (w.a1.assign[0], q))     # binding: allowed
    for col, val in (("reported_qty", 6), ("reported_uom", "'M'"), ("qty_basis", "'INCREMENTAL'")):
        fails(conn, f"update claim_quantities set {col} = {val} where claim_quantity_id=%s", (q,), match="immutable")
    fails(conn, "delete from claim_quantities where claim_quantity_id=%s", (q,), match="never deleted")
    decide(conn, w, ev, w.a1)
    fails(conn, "update claim_quantities set normalized_qty = 4 where claim_quantity_id=%s", (q,), match="frozen")


def test_binding_must_stay_inside_the_claims_own_activity_and_unit_kind(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a1)                                                   # a1: PIPELINE_KM (idx 0), WELD_JOINTS (1), MANHOURS (2)
    fails(conn, "insert into claim_quantities (project_id,event_id,assignment_uid,qty_basis,reported_qty,reported_uom) values (%s,%s,%s,'CUMULATIVE',5,'M3')",
          (w.project, ev, w.a2.assign[0]), match="does not belong")             # an assignment of ANOTHER activity
    fails(conn, "insert into claim_quantities (project_id,event_id,assignment_uid,qty_basis,reported_qty,reported_uom,normalized_uom,normalized_qty) values (%s,%s,%s,'CUMULATIVE',5,'M3',%s,5)",
          (w.project, ev, w.a1.assign[0], "M3"), match="not the same kind")     # m3 into a length assignment: never converted across kinds
    conn.execute("insert into claim_quantities (project_id,event_id,assignment_uid,qty_basis,reported_qty,reported_uom,normalized_uom,normalized_qty) values (%s,%s,%s,'CUMULATIVE',5000,'M','KM',5)",
                 (w.project, ev, w.a1.assign[0]))                               # m -> km is the same kind: fine


def test_quantity_and_evidence_changes_need_the_engineer_or_a_supervisor(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a1)
    d = doc(conn, w, "EVIDENCE", by=w.se)
    ins_q = ("insert into claim_quantities (project_id,event_id,qty_basis,reported_qty,reported_uom) values (%s,%s,'CUMULATIVE',1,'KM')", (w.project, ev))
    ins_e = ("insert into claim_evidence (project_id,event_id,document_id) values (%s,%s,%s)", (w.project, ev, d))
    as_actor(conn, w.pm)
    fails(conn, *ins_q, match="only the filing site engineer or a supervisor")
    fails(conn, *ins_e, match="only the engineer who filed")
    as_actor(conn, w.sup)
    fails(conn, *ins_e, match="only the engineer who filed")                    # evidence belongs to the filer, not the reviewer
    as_actor(conn, w.se)
    conn.execute(*ins_q); conn.execute(*ins_e)
    fails(conn, "update claim_evidence set document_id = document_id", match="never changed")
    fails(conn, "delete from claim_evidence", match="never changed")
    decide(conn, w, ev, w.a1, action="REJECT")
    d2 = doc(conn, w, "PHOTO", by=w.se)
    fails(conn, "insert into claim_evidence (project_id,event_id,document_id) values (%s,%s,%s)", (w.project, ev, d2), match="can no longer be attached")


# ------------------------------------------------------------------------------------------------ ledger linkage
def test_the_ledger_row_must_point_at_a_quantity_of_the_decided_claim(w, conn):
    sysmode(conn)
    ev, other = claim(conn, w, w.a2), claim(conn, w, w.a2)
    q_other = cq(conn, w, other, w.a2.assign[0], qty=100, uom="M3", norm=100)
    dec = decide(conn, w, ev, w.a2)
    fails(conn, "insert into approved_resource_progress (project_id,activity_uid,assignment_uid,decision_id,as_of_date,cumulative_qty,claim_quantity_id) values (%s,%s,%s,%s,current_date,100,%s)",
          (w.project, w.a2.uid, w.a2.assign[0], dec, q_other), match="must be a quantity of the decided claim")


def test_the_chain_links_are_set_by_the_database_and_cannot_be_chosen_by_the_writer(w, conn):
    sysmode(conn)
    e1, _ = approve_qty(conn, w, w.a2, 0, 100)
    ev = claim(conn, w, w.a2)
    dec = decide(conn, w, ev, w.a2)
    bogus = u()
    conn.execute("insert into approved_resource_progress (entry_id,project_id,activity_uid,assignment_uid,decision_id,as_of_date,cumulative_qty,prev_entry_id) values (%s,%s,%s,%s,%s,current_date,150,null)",
                 (bogus, w.project, w.a2.uid, w.a2.assign[0], dec))               # the writer says 'no predecessor'
    row = conn.execute("select prev_entry_id, prev_cumulative_qty, incremental_qty from approved_resource_progress where entry_id=%s", (bogus,)).fetchone()
    assert row == {"prev_entry_id": e1, "prev_cumulative_qty": 100, "incremental_qty": 50}          # the trigger overrides it with the real head


# ------------------------------------------------------------------------------------------------ metadata, issues, notifications
def test_document_gps_issue_delay_and_notification_constraints(w, conn):
    sysmode(conn)
    d = doc(conn, w, "PHOTO", by=w.se)
    fails(conn, "update source_documents set gps_lat = 26.1 where document_id=%s", (d,), match="chk_doc_gps_pair")
    fails(conn, "update source_documents set gps_lat = 95, gps_lon = 91 where document_id=%s", (d,), match="check")
    conn.execute("update source_documents set gps_lat = 26.1, gps_lon = 91.7, captured_at = now(), exif = '{\"make\":\"x\"}'::jsonb where document_id=%s", (d,))
    ins = ("insert into issues (project_id,activity_uid,category_code,title,reported_date,reported_by,delay_started_on,delay_ended_on,impact_days_estimated) values (%s,%s,'WEATHER','Flooded ROW',%s,%s,%s,%s,%s)")
    fails(conn, ins, (w.project, w.a1.uid, TODAY, w.se, TODAY, TODAY.replace(year=TODAY.year - 1), 2), match="chk_issue_delay_dates")
    fails(conn, ins, (w.project, w.a1.uid, TODAY, w.se, TODAY, TODAY, -1), match="check")
    conn.execute(ins, (w.project, w.a1.uid, TODAY, w.se, TODAY, TODAY, 3))
    ev = claim(conn, w, w.a1)
    for t in ("CLAIM_SUBMITTED", "CLAIM_CLARIFICATION"):
        conn.execute("insert into notifications (project_id,recipient_id,notification_type,event_id,title) values (%s,%s,%s,%s,'t')", (w.project, w.sup, t, ev))
    fails(conn, "insert into notifications (project_id,recipient_id,notification_type,title) values (%s,%s,'CLAIM_SUBMITTED','no event')", (w.project, w.sup), match="notifications_subject_chk")
    fails(conn, "insert into notifications (project_id,recipient_id,notification_type,event_id,title) values (%s,%s,'SOMETHING_ELSE',%s,'t')", (w.project, w.sup, ev), match="notification_type_check")
