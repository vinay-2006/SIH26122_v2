"""Claim submission and lifecycle: what is stored, what is bound, what is refused, who sees what. Nothing here can create progress."""
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from backend.v2.domain import claims, decisions
from backend.v2.domain.claims import _bind
from backend.v2.domain.common import resolve_actor
from backend.v2.domain.progress_math import Unit
from backend.v2.errors import ApiError
from backend.v2.schedule_import.mapping import RefData
from domainkit import TODAY, make_doc
from v2api import connect


def raises(code, status=None):
    return pytest.raises(ApiError, match=".*") if code is None else _Raises(code, status)


class _Raises:
    def __init__(self, code, status): self.code, self.status = code, status
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        assert et is ApiError, f"expected ApiError {self.code}, got {et}: {ev}"
        assert ev.code == self.code, f"expected {self.code}, got {ev.code}: {ev.message}"
        if self.status:
            assert ev.status == self.status
        return True


def no_progress(kit):
    led = kit.ledger()
    assert led == ([], []) and kit.project_pct() == 0


# ------------------------------------------------------------------------------------------------ submission and binding
def test_a_quantity_claim_is_stored_as_reported_bound_to_its_assignment_and_creates_no_progress(kit):
    r = kit.submit("A2010", 300, "joints")
    assert r["status"] == "MATCHED" and r["validations"] == []
    q = r["quantities"][0]
    assert (q["reported_qty"], q["reported_uom"], q["basis"], q["resource_code"], q["normalized_qty"], q["normalized_uom"]) == (D(300), "joints", "CUMULATIVE", "WELD_JOINTS", D("300.000"), "JOINT")
    assert q["assignment_uid"] == kit.asg("A2010", "WELD_JOINTS")
    no_progress(kit)


def test_units_convert_only_within_a_kind_and_the_reported_figure_is_preserved(kit):
    r = kit.submit("A2000", 5000, "m")                                        # pipe strung, baseline unit KM
    q = r["quantities"][0]
    assert q["reported_qty"] == 5000 and q["reported_uom"] == "m" and q["normalized_qty"] == D("5.000") and q["normalized_uom"] == "KM"
    bad = kit.submit("A2000", 20, "tonne")                                    # a different kind of unit: kept as reported, never converted, never bound
    assert bad["quantities"][0]["assignment_uid"] is None and bad["quantities"][0]["normalized_qty"] is None
    assert [v["rule"] for v in bad["validations"]] == ["UNIT_DIMENSION_MISMATCH"] and bad["validations"][0]["severity"] == "ERROR"
    no_progress(kit)


def test_a_multi_quantity_claim_binds_each_quantity_to_its_own_assignment(kit):
    r = kit.submit("A1020", quantities=[{"qty": 100, "uom": "m3", "basis": "CUMULATIVE"}, {"qty": 5, "uom": "tonne", "basis": "CUMULATIVE"}])
    bound = {q["resource_code"]: (q["normalized_qty"], q["normalized_uom"]) for q in r["quantities"]}
    assert bound == {"CONCRETE_M3": (D("100.000"), "M3"), "STEEL_TONNES": (D("5.000"), "TONNE")} and r["validations"] == []


def test_two_figures_for_one_assignment_bind_only_the_first(kit):
    r = kit.submit("A2010", quantities=[{"qty": 100, "uom": "joints", "basis": "CUMULATIVE"}, {"qty": 120, "uom": "joint", "basis": "CUMULATIVE"}])
    assert [q["assignment_uid"] is not None for q in r["quantities"]] == [True, False]
    assert "DUPLICATE_QUANTITY" in {v["rule"] for v in r["validations"]}


def test_reported_above_baseline_is_a_warning_not_a_refusal_and_is_not_clamped(kit):
    r = kit.submit("A2010", 2500, "joints")                                    # baseline 2000
    assert r["status"] == "MATCHED" and r["quantities"][0]["normalized_qty"] == D("2500.000")
    assert {v["rule"]: v["severity"] for v in r["validations"]}["REPORTED_ABOVE_BASELINE"] == "WARNING"


def test_a_percentage_only_claim_on_a_quantity_activity_is_stored_as_reported_and_flagged_not_converted(kit):
    r = kit.submit("A2010", None, pct=40, text="Welding about 40% done")
    assert r["quantities"] == [] and {v["rule"]: v["severity"] for v in r["validations"]} == {"PERCENT_ONLY_NEEDS_METHOD": "INFO"}
    row = claims.get_claim(kit.se, r["claim_id"])
    assert row["claimed_pct"] == D("40.000") and row["quantities"] == []             # no quantity was invented
    no_progress(kit)


def test_a_claim_without_an_activity_waits_for_matching_and_the_supervisor_can_rematch_it(kit):
    r = kit.submit(None, 300, "joints", reported_activity_ref="mainline welds")
    assert r["status"] == "EXTRACTED" and r["quantities"][0]["assignment_uid"] is None
    out = claims.rematch_claim(kit.sup, r["claim_id"], kit.uid("A2010"))
    got = claims.get_claim(kit.sup, r["claim_id"])
    assert got["status"] == "MATCHED" and got["matched_activity_uid"] == kit.uid("A2010")
    assert got["quantities"][0]["assignment_uid"] == kit.asg("A2010", "WELD_JOINTS") and got["quantities"][0]["normalized_qty"] == D("300.000")
    assert out["findings"] == []
    for who in (kit.se, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            claims.rematch_claim(who, r["claim_id"], kit.uid("A2010"))


def test_a_supervisor_can_bind_a_quantity_but_not_across_unit_kinds(kit):
    r = kit.submit("A2000", 20, "tonne")
    cq = r["quantities"][0]["claim_quantity_id"]
    with raises("UNIT_DIMENSION_MISMATCH", 422):
        claims.bind_quantities(kit.sup, r["claim_id"], {cq: kit.asg("A2000", "PIPE_STRUNG_KM")})
    with raises("ASSIGNMENT_NOT_MEASURED", 422):
        claims.bind_quantities(kit.sup, r["claim_id"], {cq: kit.asg("A2000", "MANHOURS")})        # effort resources never measure progress
    ok = kit.submit("A2000", 5000, "m", text="second claim, bind check")
    cq2 = ok["quantities"][0]["claim_quantity_id"]
    assert claims.bind_quantities(kit.sup, ok["claim_id"], {cq2: kit.asg("A2000", "PIPE_STRUNG_KM")})["bound"][0]["normalized_qty"] == D("5.000")


def test_ambiguity_is_resolved_by_a_resource_hint_never_by_guessing():
    """pure: two measured assignments of the same unit kind"""
    units = {"KM": Unit("KM", "LENGTH", D(1000)), "M": Unit("M", "LENGTH", D(1))}
    ref = RefData(set(), {}, {"KM": "LENGTH", "M": "LENGTH"})
    a1 = {"assignment_uid": uuid.uuid4(), "unit_of_measure": "KM", "resource_code": "TRENCH_KM", "resource_name": "Trench dug", "baseline_qty": D(10)}
    a2 = {"assignment_uid": uuid.uuid4(), "unit_of_measure": "KM", "resource_code": "PIPE_KM", "resource_name": "Pipe laid", "baseline_qty": D(10)}
    rows, f = _bind(units, ref, [a1, a2], [{"qty": D(2), "uom": "km", "basis": "CUMULATIVE"}])
    assert rows[0]["assignment"] is None and [x[0] for x in f] == ["AMBIGUOUS_QUANTITY"]
    rows, f = _bind(units, ref, [a1, a2], [{"qty": D(2000), "uom": "m", "basis": "CUMULATIVE", "resource_hint": "pipe"}])
    assert rows[0]["assignment"] is a2 and rows[0]["normalized_qty"] == D("2.000") and f == []


# ------------------------------------------------------------------------------------------------ refusals
def test_obviously_bad_claims_are_refused_before_anything_is_stored(kit):
    base = dict(event_date=TODAY, raw_text="Welding done", activity_uid=kit.uid("A2010"), quantities=[{"qty": 10, "uom": "joints", "basis": "CUMULATIVE"}])
    cases = [
        ("FUTURE_DATE", dict(base, event_date=TODAY + timedelta(days=1))), ("CLAIM_TEXT_REQUIRED", dict(base, raw_text="ab")),
        ("EMPTY_CLAIM", dict(base, quantities=[])), ("BAD_PERCENT", dict(base, claimed_pct=101)), ("BAD_QUANTITY", dict(base, quantities=[{"qty": -1, "uom": "joints", "basis": "CUMULATIVE"}])),
        ("BAD_QUANTITY", dict(base, quantities=[{"qty": 1, "uom": "joints", "basis": "TOTAL"}])), ("BAD_QUANTITY", dict(base, quantities=[{"qty": 1, "uom": "", "basis": "CUMULATIVE"}])),
        ("BAD_CHANNEL", dict(base, input_channel="CARRIER_PIGEON")), ("BAD_DATES", dict(base, claimed_start=TODAY, claimed_finish=TODAY - timedelta(days=1))),
        ("ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", dict(base, activity_uid=uuid.uuid4())),
    ]
    for code, kw in cases:
        with raises(code):
            claims.submit_claim(kit.se, **kw)
    assert kit.count("execution_events") == 0 and kit.count("claim_quantities") == 0 and kit.count("notifications") == 0


def test_a_duplicate_claim_is_refused_and_points_at_the_original(kit):
    first = kit.submit("A2010", 300, "joints", text="Same report")
    with raises("DUPLICATE_CLAIM", 409) as e:
        kit.submit("A2010", 300, "joints", text="Same   report")                       # whitespace differences do not make it a different claim
    assert kit.count("execution_events") == 1


def test_only_site_engineers_submit_and_only_into_a_project_with_an_active_schedule(kit, world, api):
    for who in (kit.sup, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            claims.submit_claim(who, event_date=TODAY, raw_text="x progress", quantities=[{"qty": 1, "uom": "joints", "basis": "CUMULATIVE"}])
    # a second project without any schedule
    p2 = api.post("/api/v2/projects", world.pm, json={"project_code": "EMPTY-1", "project_name": "No schedule yet"}).json()["project_id"]
    api.post(f"/api/v2/projects/{p2}/members", world.pm, json={"email": world.se.email, "role": "SITE_ENGINEER"})
    se2 = resolve_actor(world.se.id, p2)
    with raises("NO_ACTIVE_SCHEDULE", 409):
        claims.submit_claim(se2, event_date=TODAY, raw_text="Work started", claimed_start=TODAY)
    api.post(f"/api/v2/projects/{world.project}/archive", world.pm)
    with raises("PROJECT_ARCHIVED", 409):
        kit.submit("A2010", 10, "joints", text="after archive")


def test_the_database_refuses_a_claim_from_a_non_engineer_even_if_the_service_were_bypassed(kit):
    import psycopg
    with connect() as c:
        c.execute("select set_config('app.actor_id', %s, false)", (str(kit.sup.user_id),))
        ver = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (kit.project,)).fetchone()["version_id"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into execution_events (project_id, filed_in_version_id, event_date, raw_claim_text, input_channel, filed_by, status) values (%s,%s,current_date,'x','TYPED',%s,'EXTRACTED')",
                      (kit.project, ver, kit.sup.user_id))


# ------------------------------------------------------------------------------------------------ withdrawal (D5)
def test_an_engineer_can_withdraw_their_own_pending_claim_and_nothing_is_lost_or_counted(kit):
    ev = make_doc(kit)
    r = kit.submit("A2010", 300, "joints", evidence_document_ids=[ev])
    out = claims.withdraw_claim(kit.se, r["claim_id"], "Wrong site reported")
    assert out["status"] == "WITHDRAWN"
    row = claims.get_claim(kit.se, r["claim_id"])
    assert row["status"] == "WITHDRAWN" and row["withdrawn_reason"] == "Wrong site reported" and row["withdrawn_at"] is not None
    assert len(row["quantities"]) == 1 and len(row["evidence"]) == 1                  # original claim and evidence are retained
    assert row["quantities"][0]["reported_qty"] == 300
    no_progress(kit)
    assert kit.count("audit_logs", "action = 'CLAIM_WITHDRAWN'") == 1
    with raises("CLAIM_NOT_DECIDABLE", 409):
        kit.approve(r["claim_id"], method="QUANTITIES_AS_CLAIMED")                    # a withdrawn claim can never be decided
    no_progress(kit)


def test_withdrawal_is_for_the_filer_only_and_only_before_a_final_decision(kit):
    r = kit.submit("A2010", 300, "joints")
    with raises("CLAIM_NOT_FOUND", 404):
        claims.withdraw_claim(kit.se2, r["claim_id"], "not mine")                    # another engineer cannot even tell it exists
    for who in (kit.sup, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            claims.withdraw_claim(who, r["claim_id"], "overruled")
    with raises("REASON_REQUIRED", 422):
        claims.withdraw_claim(kit.se, r["claim_id"], " ")
    d = kit.approve(r["claim_id"])
    assert d["status"] == "APPROVED"
    with raises("CLAIM_ALREADY_FINAL", 409):
        claims.withdraw_claim(kit.se, r["claim_id"], "too late")
    rej = kit.submit("A2000", 3, "km", text="to be rejected")
    kit.approve(rej["claim_id"], action="REJECT", justification="No measurement record attached")
    with raises("CLAIM_ALREADY_FINAL", 409):
        claims.withdraw_claim(kit.se, rej["claim_id"], "too late")
    w = kit.submit("A3010", 10, "nos", text="withdraw twice")
    claims.withdraw_claim(kit.se, w["claim_id"], "mistake")
    with raises("CLAIM_ALREADY_FINAL", 409):
        claims.withdraw_claim(kit.se, w["claim_id"], "again")


def test_the_database_enforces_who_may_withdraw_and_freezes_final_claims(kit):
    import psycopg
    r = kit.submit("A2010", 300, "joints")
    with connect() as c:
        c.execute("select set_config('app.actor_id', %s, false)", (str(kit.sup.user_id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only the engineer who filed"):
            c.execute("update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='by supervisor' where event_id=%s", (r["claim_id"],))
        c.rollback()
        c.execute("select set_config('app.actor_id', %s, false)", (str(kit.pm.user_id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="never"):
            c.execute("update execution_events set priority_score = 99 where event_id=%s", (r["claim_id"],))   # a project manager can never change a claim
        c.rollback()
    kit.approve(r["claim_id"])
    with connect() as c:
        c.execute("select set_config('app.actor_id', %s, false)", (str(kit.se.user_id),))
        with pytest.raises(psycopg.errors.CheckViolation, match="frozen|illegal"):
            c.execute("update execution_events set status='WITHDRAWN', withdrawn_at=now(), withdrawn_reason='after approval' where event_id=%s", (r["claim_id"],))


# ------------------------------------------------------------------------------------------------ clarification, corrections, evidence
def test_hold_clarify_then_decide_flow(kit):
    r = kit.submit("A2010", 300, "joints")
    with raises("NO_OPEN_QUESTION", 409):
        claims.answer_clarification(kit.se, r["claim_id"], "nothing asked")
    kit.approve(r["claim_id"], action="HOLD", clarification_question="Which weld map covers these joints?")
    row = claims.get_claim(kit.se, r["claim_id"])
    assert row["status"] == "DISPUTED" and row["clarification_status"] == "ASKED" and row["clarification_question"].startswith("Which weld map")
    with raises("CLAIM_NOT_FOUND", 404):
        claims.answer_clarification(kit.se2, r["claim_id"], "not my claim")
    claims.answer_clarification(kit.se, r["claim_id"], "Weld map WM-12, sheet 3")
    row = claims.get_claim(kit.sup, r["claim_id"])
    assert row["status"] == "DISPUTED" and row["clarification_status"] == "ANSWERED" and row["clarification_answer"].startswith("Weld map")
    no_progress(kit)                                                                 # a disputed claim contributes nothing
    assert kit.approve(r["claim_id"])["status"] == "APPROVED"


def test_a_correction_after_rejection_is_a_new_linked_claim_never_an_edit(kit):
    r = kit.submit("A2010", 300, "joints", text="first attempt")
    with raises("NOT_A_REJECTED_CLAIM", 409):
        kit.submit("A2010", 280, "joints", text="too early", resubmits_event_id=r["claim_id"])
    kit.approve(r["claim_id"], action="REJECT", justification="Quantity not supported by the weld log")
    fix = kit.submit("A2010", 280, "joints", text="corrected after rejection", resubmits_event_id=r["claim_id"])
    assert fix["claim_id"] != r["claim_id"] and claims.get_claim(kit.se, fix["claim_id"])["resubmits_event_id"] == r["claim_id"]
    orig = claims.get_claim(kit.se, r["claim_id"])
    assert orig["status"] == "REJECTED" and orig["quantities"][0]["reported_qty"] == 300           # the rejected claim is untouched
    with raises("ALREADY_CORRECTED", 409):
        kit.submit("A2010", 270, "joints", text="a second correction", resubmits_event_id=r["claim_id"])
    claims.withdraw_claim(kit.se, fix["claim_id"], "wrong again")                                    # a withdrawn correction frees the slot
    assert kit.submit("A2010", 275, "joints", text="third try", resubmits_event_id=r["claim_id"])["status"] == "MATCHED"
    with raises("NOT_A_REJECTED_CLAIM", 409):
        kit.submit("A2010", 1, "joints", text="someone else's rejection", resubmits_event_id=r["claim_id"], by=kit.se2)


def test_evidence_can_be_added_while_pending_only_by_the_filer_and_is_retained(kit):
    r = kit.submit("A2010", 300, "joints")
    d1, d2 = make_doc(kit, "PHOTO"), make_doc(kit, "SITE_REPORT")
    claims.attach_evidence(kit.se, r["claim_id"], d1)
    with raises("CLAIM_NOT_FOUND", 404):
        claims.attach_evidence(kit.se2, r["claim_id"], d2)
    with raises("DOCUMENT_NOT_FOUND", 422):
        claims.attach_evidence(kit.se, r["claim_id"], uuid.uuid4())
    kit.approve(r["claim_id"])
    with raises("CLAIM_ALREADY_FINAL", 409):
        claims.attach_evidence(kit.se, r["claim_id"], d2)
    assert [e["document_id"] for e in claims.get_claim(kit.sup, r["claim_id"])["evidence"]] == [d1]


# ------------------------------------------------------------------------------------------------ visibility
def test_who_sees_which_claims(kit):
    mine = kit.submit("A2010", 300, "joints", text="mine")["claim_id"]
    theirs = kit.submit("A2000", 3, "km", text="theirs", by=kit.se2)["claim_id"]
    assert claims.get_claim(kit.se, mine)["raw_claim_text"] == "mine"
    with raises("CLAIM_NOT_FOUND", 404):
        claims.get_claim(kit.se, theirs)
    assert claims.get_claim(kit.sup, theirs)["raw_claim_text"] == "theirs"
    with raises("CLAIM_CONTENT_FORBIDDEN", 403):
        claims.get_claim(kit.pm, mine)
    assert [r["event_id"] for r in claims.list_my_claims(kit.se)] == [mine]
    assert {r["event_id"] for r in claims.review_queue(kit.sup)} == {mine, theirs}
    for who in (kit.se, kit.pm):
        with raises("PERMISSION_DENIED", 403):
            claims.review_queue(who)
    with raises("PERMISSION_DENIED", 403):
        claims.list_my_claims(kit.sup)


def test_the_project_manager_sees_aggregate_counts_and_nothing_else(kit):
    a = kit.submit("A2010", 300, "joints", text="secret pending")["claim_id"]
    b = kit.submit("A2000", 3, "km", text="to approve")["claim_id"]
    kit.approve(b)
    c3 = kit.submit("A3010", 10, "nos", text="to withdraw")["claim_id"]
    claims.withdraw_claim(kit.se, c3, "oops")
    counts = claims.claim_counts(kit.pm)
    assert counts["MATCHED"] == 1 and counts["APPROVED"] == 1 and counts["WITHDRAWN"] == 1 and counts["pending_total"] == 1
    assert all(isinstance(v, int) for v in counts.values())                          # numbers only: no ids, no text
    assert claims.claim_counts(kit.sup) == counts
    with raises("PERMISSION_DENIED", 403):
        claims.claim_counts(kit.se)
    # and the database agrees: a project manager's own database session cannot read claim rows
    import psycopg
    with connect() as c:
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(kit.pm.user_id),))
        assert c.execute("select count(*) n from execution_events").fetchone()["n"] == 0
        assert c.execute("select count(*) n from planner_decisions").fetchone()["n"] == 0


def test_submission_notifies_supervisors_and_is_audited(kit):
    r = kit.submit("A2010", 300, "joints")
    with connect() as c:
        n = c.execute("select recipient_id, notification_type, event_id from notifications").fetchall()
        a = c.execute("select action, entity_type, entity_id from audit_logs where action = 'CLAIM_SUBMITTED'").fetchall()
    assert [(x["recipient_id"], x["notification_type"], x["event_id"]) for x in n] == [(kit.world.sup.id, "CLAIM_SUBMITTED", r["claim_id"])]
    assert len(a) == 1 and a[0]["entity_id"] == str(r["claim_id"])


def test_conflicting_percentage_claims_for_the_same_activity_and_day_are_recorded(kit):
    kit.submit("A2010", None, pct=40, text="Foreman says 40")
    kit.submit("A2010", None, pct=55, text="Engineer says 55", by=kit.se2)
    with connect() as c:
        rows = c.execute("select value_a, value_b, variance_pct, status from conflict_records").fetchall()
    assert len(rows) == 1 and {rows[0]["value_a"], rows[0]["value_b"]} == {D(40), D(55)} and rows[0]["status"] == "OPEN"
