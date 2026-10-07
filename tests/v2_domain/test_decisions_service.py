"""Supervisor decisions: the only way progress is created. Numbers are worked out from the NSP fixture (A2010 welding: 2,000 joints;
A1020: 480 m3 + 36 t, equal weights; A0100: no quantity; A3030: milestone)."""
import json
from decimal import Decimal as D

import pytest

from backend.v2.domain import claims, decisions
from backend.v2.errors import ApiError
from domainkit import TODAY, make_doc
from v2api import connect


class Raises:
    def __init__(self, code, status=None): self.code, self.status = code, status
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        assert et is ApiError, f"expected ApiError {self.code}, got {et}: {ev}"
        assert ev.code == self.code, f"expected {self.code}, got {ev.code}: {ev.message}"
        if self.status:
            assert ev.status == self.status
        self.err = ev
        return True


raises = Raises


def decision_row(decision_id):
    with connect() as c:
        return c.execute("select * from planner_decisions where decision_id = %s", (decision_id,)).fetchone()


def nothing_written(kit, before):
    assert kit.ledger() == before["ledger"]
    for t in ("planner_decisions", "notifications", "audit_logs"):
        assert kit.count(t) == before[t], f"{t} changed"


def snapshot(kit):
    return {"ledger": kit.ledger(), **{t: kit.count(t) for t in ("planner_decisions", "notifications", "audit_logs")}}


# ------------------------------------------------------------------------------------------------ approving what was claimed
def test_approve_as_claimed_records_the_method_the_applied_quantities_and_the_result_atomically(kit):
    r = kit.submit("A2010", 500, "joints")
    d = kit.approve(r["claim_id"])
    assert d["status"] == "APPROVED" and d["action"] == "APPROVE" and d["method"] == "QUANTITIES_AS_CLAIMED"
    row = decision_row(d["decision_id"])
    assert row["method"] == "QUANTITIES_AS_CLAIMED" and row["justification"] == "Approved as reported" and row["approved_pct"] == D("25.000")
    ap = row["applied"][0]
    assert (ap["resource"], ap["unit"], ap["head_cumulative"], ap["approved_cumulative"], ap["incremental"], ap["source"]) == ("WELD_JOINTS", "JOINT", "0", "500.000", "500.000", "CLAIM")
    assert D(row["result"]["activity_pct_before"]) == 0 and row["result"]["activity_pct_after"] == "25.000" and row["result"]["start_inferred"] is True
    led, act = kit.ledger()
    assert len(led) == 1 and led[0]["cumulative_qty"] == 500 and led[0]["incremental_qty"] == 500 and led[0]["prev_entry_id"] is None
    assert act[0]["actual_start"] == TODAY                                         # inferred from the report date, and said so on the decision
    assert led[0]["claim_quantity_id"] == r["quantities"][0]["claim_quantity_id"]   # each approved row points at the reported figure it came from
    assert kit.pct("A2010") == D("25.000") and claims.get_claim(kit.sup, r["claim_id"])["status"] == "APPROVED"
    with connect() as c:
        assert c.execute("select notification_type, recipient_id from notifications where decision_id = %s", (d["decision_id"],)).fetchone() == {"notification_type": "CLAIM_DECISION", "recipient_id": kit.world.se.id}
        acts = [a["action"] for a in c.execute("select action from audit_logs order by log_id").fetchall()]
    assert acts.count("CLAIM_APPROVED") == 1 and acts.count("PROGRESS_RECORDED") == 2


def test_incremental_and_cumulative_claims_never_double_count(kit):
    ids = []
    for days_ago, qty, basis in [(4, 500, "CUMULATIVE"), (3, 300, "INCREMENTAL"), (2, 1000, "CUMULATIVE"), (1, 200, "INCREMENTAL")]:
        r = kit.submit("A2010", qty, "joints", basis=basis, days_ago=days_ago, text=f"{basis} {qty} on day -{days_ago}")
        ids.append(kit.approve(r["claim_id"])["decision_id"])
    led, _ = kit.ledger()
    assert [(e["cumulative_qty"], e["incremental_qty"], e["prev_cumulative_qty"]) for e in led] == [(500, 500, 0), (800, 300, 500), (1000, 200, 800), (1200, 200, 1000)]
    assert sum(e["incremental_qty"] for e in led) == led[-1]["cumulative_qty"] == 1200
    assert [e["prev_entry_id"] for e in led] == [None] + [e["entry_id"] for e in led[:-1]]            # one linear chain
    assert kit.pct("A2010") == D("60.000")


def test_a_cumulative_figure_below_the_ledger_is_refused_and_leaves_the_claim_pending(kit):
    kit.approve(kit.submit("A2010", 800, "joints", days_ago=2)["claim_id"])
    r = kit.submit("A2010", 700, "joints", days_ago=1)
    before = snapshot(kit)
    with raises("WOULD_DECREASE", 409):
        kit.approve(r["claim_id"])
    nothing_written(kit, before)
    assert claims.get_claim(kit.sup, r["claim_id"])["status"] == "MATCHED"                    # still decidable: reject it, or fix and re-decide
    r2 = kit.submit("A2010", 800, "joints", days_ago=0, text="same cumulative again")
    with raises("NO_PROGRESS_CHANGE", 422):
        kit.approve(r2["claim_id"])


def test_a_claim_older_than_the_latest_approved_entry_is_stale(kit):
    kit.approve(kit.submit("A2010", 500, "joints", days_ago=0)["claim_id"])
    old = kit.submit("A2010", 900, "joints", days_ago=5, text="late paperwork")
    with raises("STALE_CLAIM", 409):
        kit.approve(old["claim_id"])


# ------------------------------------------------------------------------------------------------ edits preserve both values
def test_a_supervisor_edit_keeps_what_was_reported_and_what_was_approved(kit):
    r = kit.submit("A2010", 600, "joints")
    manual = {str(kit.asg("A2010", "WELD_JOINTS")): {"cumulative": 500}}
    with raises("ACTION_MISMATCH", 422) as e:
        kit.approve(r["claim_id"], approved_quantities=manual)                           # changing the figures is an EDIT, and must say so
    assert e.err.details["expected_action"] == "EDIT"
    with raises("JUSTIFICATION_REQUIRED", 422):
        kit.approve(r["claim_id"], action="EDIT", approved_quantities=manual)
    d = kit.approve(r["claim_id"], action="EDIT", approved_quantities=manual, justification="Weld log supports 500 joints; 100 awaiting NDT clearance")
    assert d["action"] == "EDIT" and d["method"] == "MANUAL_QUANTITIES"
    got = claims.get_claim(kit.sup, r["claim_id"])
    assert got["quantities"][0]["reported_qty"] == 600 and got["quantities"][0]["normalized_qty"] == 600                # reported value untouched
    led, _ = kit.ledger()
    assert led[0]["cumulative_qty"] == 500 and led[0]["claim_quantity_id"] == r["quantities"][0]["claim_quantity_id"]    # approved value, linked to the reported one
    row = decision_row(d["decision_id"])
    assert row["applied"][0]["source"] == "MANUAL" and row["justification"].startswith("Weld log")
    assert kit.pct("A2010") == D("25.000")


def test_manual_increments_are_supported_and_must_name_a_measured_assignment(kit):
    r = kit.submit("A2010", None, pct=10, text="about ten percent")
    with raises("ASSIGNMENT_NOT_MEASURED", 422):
        kit.approve(r["claim_id"], action="EDIT", justification="x manual", approved_quantities={str(kit.asg("A2010", "MANHOURS")): {"cumulative": 5}})
    with raises("BAD_APPROVED_QUANTITY", 422):
        kit.approve(r["claim_id"], action="EDIT", justification="x manual", approved_quantities={str(kit.asg("A2010", "WELD_JOINTS")): {"cumulative": 5, "increment": 5}})
    d = kit.approve(r["claim_id"], action="EDIT", justification="Measured on site: 150 joints", approved_quantities={str(kit.asg("A2010", "WELD_JOINTS")): {"increment": 150}})
    assert kit.ledger()[0][0]["cumulative_qty"] == 150 and d["applied"][0]["approved_cumulative"] == "150.000"


# ------------------------------------------------------------------------------------------------ percentage-only claims (D1)
def test_a_percentage_is_never_silently_converted_to_quantities(kit):
    r = kit.submit("A2010", None, pct=40, text="Welding about 40% complete")
    before = snapshot(kit)
    with raises("PERCENT_METHOD_REQUIRED", 422) as e:
        kit.approve(r["claim_id"])
    assert e.err.details["choices"] == ["APPLY_PCT_TO_ASSIGNMENTS", "MANUAL_QUANTITIES"] and e.err.details["assignments"][0]["resource"] == "WELD_JOINTS"
    nothing_written(kit, before)
    with raises("NOTHING_TO_APPROVE", 422):
        kit.approve(r["claim_id"], method="QUANTITIES_AS_CLAIMED")                       # there are no claimed quantities to approve
    with raises("ACTIVITY_IS_QUANTITY_BASED", 422):
        kit.approve(r["claim_id"], method="PCT_ONLY_ACTIVITY")
    nothing_written(kit, before)


def test_apply_pct_to_assignments_is_explicit_recorded_and_exact(kit):
    r = kit.submit("A2010", None, pct=40, text="Welding about 40% complete")
    pv = decisions.preview_decision(kit.sup, r["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS")
    assert pv["ok"] and pv["applied"][0]["approved_cumulative"] == "800.000" and pv["result"]["activity_pct_after"] == "40.000"
    d = kit.approve(r["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS")
    assert d["action"] == "APPROVE" and d["method"] == "APPLY_PCT_TO_ASSIGNMENTS"
    row = decision_row(d["decision_id"])
    assert row["method"] == "APPLY_PCT_TO_ASSIGNMENTS" and row["applied"][0]["source"] == "PCT" and row["applied"][0]["approved_cumulative"] == "800.000" and row["result"]["activity_pct_after"] == "40.000"
    assert kit.ledger()[0][0]["cumulative_qty"] == 800 and kit.pct("A2010") == D("40.000")
    assert kit.ledger()[0][0]["claim_quantity_id"] is None                              # there was no reported quantity: the ledger says so


def test_applying_a_different_percentage_than_claimed_is_an_edit(kit):
    r = kit.submit("A2010", None, pct=40, text="about 40")
    with raises("ACTION_MISMATCH", 422):
        kit.approve(r["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS", apply_pct=35)
    d = kit.approve(r["claim_id"], action="EDIT", method="APPLY_PCT_TO_ASSIGNMENTS", apply_pct=35, justification="Only 35% is supported by the weld log")
    assert kit.pct("A2010") == D("35.000") and d["result"]["activity_pct_after"] == "35.000"


def test_applying_a_percentage_to_a_multi_assignment_activity_applies_it_to_each_measured_assignment(kit):
    r = kit.submit("A1020", None, pct=50, text="Foundation 50% complete")
    d = kit.approve(r["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS")
    got = {a["resource"]: (a["approved_cumulative"], a["unit"]) for a in d["applied"]}
    assert got == {"CONCRETE_M3": ("240.000", "M3"), "STEEL_TONNES": ("18.000", "TONNE")} and kit.pct("A1020") == D("50.000")


def test_a_percentage_cannot_lower_what_is_already_approved(kit):
    kit.approve(kit.submit("A2010", 1000, "joints", days_ago=1)["claim_id"])
    r = kit.submit("A2010", None, pct=30, text="thirty percent")
    with raises("WOULD_DECREASE", 409):
        kit.approve(r["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS")


def test_activities_without_a_measured_quantity_use_the_percentage_directly(kit):
    r = kit.submit("A0100", None, pct=30, text="Statutory approvals about 30% obtained")
    d = kit.approve(r["claim_id"])
    assert d["method"] == "PCT_ONLY_ACTIVITY" and kit.pct("A0100") == D("30.000")
    _, act = kit.ledger()
    assert act[0]["reported_pct"] == 30 and act[0]["actual_start"] == TODAY
    low = kit.submit("A0100", None, pct=20, text="Approvals 20%?", days_ago=0)
    with raises("WOULD_DECREASE", 409):
        kit.approve(low["claim_id"])


def test_a_milestone_completes_with_its_finish_date_only(kit):
    r = kit.submit("A3030", None, claimed_finish=TODAY, text="Commissioning complete certificate signed")
    d = kit.approve(r["claim_id"])
    assert d["method"] == "PCT_ONLY_ACTIVITY" and kit.pct("A3030") == D("100.000")
    with connect() as c:
        st = c.execute("select execution_state, actual_finish from activity_progress_as_of((select version_id from schedule_versions where project_id=%s and status='ACTIVE'), current_date) where external_activity_id='A3030'", (kit.project,)).fetchone()
    assert st == {"execution_state": "COMPLETED", "actual_finish": TODAY}


# ------------------------------------------------------------------------------------------------ dates and completion
def test_finishing_below_the_completion_threshold_needs_a_stated_short_close(kit):
    r = kit.submit("A2010", 500, "joints", claimed_finish=TODAY, text="Welding stopped; site closing early")
    with raises("FINISH_BELOW_THRESHOLD", 409) as e:
        kit.approve(r["claim_id"])
    assert D(e.err.details["threshold"]) == 95 and D(e.err.details["percent"]) == 25
    d = kit.approve(r["claim_id"], short_close_note="Scope reduced by client instruction CI-17")
    assert d["result"]["short_close_acknowledged"] is True and kit.pct("A2010") == D("25.000")
    with connect() as c:
        assert c.execute("select execution_state from activity_progress_as_of((select version_id from schedule_versions where project_id=%s and status='ACTIVE'), current_date) where external_activity_id='A2010'", (kit.project,)).fetchone()["execution_state"] == "COMPLETED"


def test_a_full_quantity_with_a_finish_date_completes_the_activity(kit):
    d = kit.approve(kit.submit("A2010", 2000, "joints", claimed_finish=TODAY)["claim_id"])
    assert kit.pct("A2010") == D("100.000") and d["result"]["short_close_acknowledged"] is False


def test_an_approved_start_date_cannot_be_changed_by_a_later_claim(kit):
    kit.approve(kit.submit("A2010", 100, "joints", days_ago=2)["claim_id"])
    r = kit.submit("A2010", 200, "joints", days_ago=1, claimed_start=TODAY, text="we actually started today")
    with raises("START_ALREADY_APPROVED", 409):
        kit.approve(r["claim_id"])


# ------------------------------------------------------------------------------------------------ over-baseline (K2)
def test_an_overrun_within_tolerance_is_flagged_and_never_clamped(kit):
    d = kit.approve(kit.submit("A2010", 2150, "joints")["claim_id"])                          # 7.5 % over 2,000
    row = decision_row(d["decision_id"])
    assert row["overrun_ack"] is False and row["applied"][0]["overrun_pct"] == "7.500" and row["result"]["overruns"][0]["beyond_tolerance"] is False
    led, _ = kit.ledger()
    assert led[0]["cumulative_qty"] == 2150                                                  # the quantity is stored as approved, not capped at 2,000
    assert kit.pct("A2010") == D("100.000")                                                  # only the percentage caps
    with connect() as c:
        assert c.execute("select over_baseline, overrun_pct from approved_resource_progress").fetchone() == {"over_baseline": True, "overrun_pct": D("7.500")}
    summary = __import__("backend.v2.domain.rollups", fromlist=["x"]).project_summary(kit.sup)
    assert summary["any_overrun"] is True


def test_an_overrun_beyond_tolerance_needs_the_deciding_supervisors_acknowledgement_which_is_kept(kit):
    r = kit.submit("A2010", 2300, "joints")                                                   # 15 % over, tolerance 10 %
    pv = decisions.preview_decision(kit.sup, r["claim_id"])
    assert pv["ok"] and pv["requires_overrun_acknowledgement"] is True and pv["result"]["overruns"][0]["beyond_tolerance"] is True
    before = snapshot(kit)
    with raises("OVERRUN_ACK_REQUIRED", 409) as e:
        kit.approve(r["claim_id"])
    assert e.err.details["tolerance_pct"] == "10.00" and e.err.details["overruns"][0]["overrun_pct"] == "15.000"
    with raises("OVERRUN_ACK_REQUIRED", 409):
        kit.approve(r["claim_id"], overrun_ack_note="ok")                                      # a note must actually say something
    nothing_written(kit, before)
    d = kit.approve(r["claim_id"], overrun_ack_note="Re-measured: extra tie-in welds per variation order VO-9")
    row = decision_row(d["decision_id"])
    assert row["overrun_ack"] is True and row["overrun_ack_note"].startswith("Re-measured")
    with connect() as c:
        e = c.execute("select cumulative_qty, overrun_pct, overrun_ack_by, overrun_ack_note from approved_resource_progress").fetchone()
        a = c.execute("select after_state from audit_logs where action = 'CLAIM_APPROVED'").fetchone()
    assert e["cumulative_qty"] == 2300 and e["overrun_pct"] == D("15.000") and e["overrun_ack_by"] == kit.world.sup.id and e["overrun_ack_note"].startswith("Re-measured")
    assert json.loads(a["after_state"])["overrun_ack"] is True                                 # and the audit trail records the acknowledgement
    assert kit.pct("A2010") == D("100.000")


def test_the_tolerance_is_a_project_setting(kit, api):
    assert api.patch(f"/api/v2/projects/{kit.project}/settings", kit.world.pm, json={"over_baseline_tolerance_pct": 25}).status_code == 200
    d = kit.approve(kit.submit("A2010", 2300, "joints")["claim_id"])
    assert decision_row(d["decision_id"])["overrun_ack"] is False and kit.ledger()[0][0]["cumulative_qty"] == 2300


def test_an_unneeded_acknowledgement_is_not_recorded_as_one(kit):
    d = kit.approve(kit.submit("A2010", 500, "joints")["claim_id"], overrun_ack_note="just in case")
    row = decision_row(d["decision_id"])
    assert row["overrun_ack"] is False and row["overrun_ack_note"] is None


# ------------------------------------------------------------------------------------------------ reject / hold / quantities
def test_reject_needs_a_reason_creates_no_progress_and_notifies_the_engineer(kit):
    r = kit.submit("A2010", 500, "joints")
    with raises("REASON_REQUIRED", 422):
        kit.approve(r["claim_id"], action="REJECT")
    d = kit.approve(r["claim_id"], action="REJECT", justification="Not supported by the weld log")
    assert d["status"] == "REJECTED" and decision_row(d["decision_id"])["method"] == "NONE"
    assert kit.ledger() == ([], []) and kit.project_pct() == 0
    with connect() as c:
        assert c.execute("select title from notifications where decision_id = %s", (d["decision_id"],)).fetchone()["title"] == "Claim rejected"
    with raises("CLAIM_NOT_DECIDABLE", 409):
        kit.approve(r["claim_id"])


def test_hold_keeps_the_claim_open_and_progress_untouched(kit):
    r = kit.submit("A2010", 500, "joints")
    with raises("REASON_REQUIRED", 422):
        kit.approve(r["claim_id"], action="HOLD")
    kit.approve(r["claim_id"], action="HOLD", clarification_question="Which weld map?")
    kit.approve(r["claim_id"], action="HOLD", clarification_question="And the NDT report number?")             # holds may repeat
    assert kit.ledger() == ([], []) and claims.get_claim(kit.sup, r["claim_id"])["status"] == "DISPUTED"
    kit.approve(r["claim_id"])
    hist = [(d["action"]) for d in claims.get_claim(kit.sup, r["claim_id"])["decisions"]]
    assert hist == ["HOLD", "HOLD", "APPROVE"]


def test_unbound_quantities_block_a_quantity_approval_until_bound_or_entered_manually(kit):
    r = kit.submit("A2000", 20, "tonne")
    with raises("QUANTITY_UNBOUND", 422):
        kit.approve(r["claim_id"])
    d = kit.approve(r["claim_id"], action="EDIT", justification="Pipe strung measured at 3 km by survey", approved_quantities={str(kit.asg("A2000", "PIPE_STRUNG_KM")): {"cumulative": 3}})
    assert kit.pct("A2000") == D("12.500") and d["method"] == "MANUAL_QUANTITIES"


def test_a_decision_on_a_retired_or_unmatched_claim_is_refused(kit):
    r = kit.submit(None, 300, "joints", reported_activity_ref="some welds")
    with raises("NO_ACTIVITY_MATCHED", 422):
        kit.approve(r["claim_id"])
    with raises("USE_REMATCH_FIRST", 422):
        kit.approve(kit.submit("A2010", 10, "joints", text="rematch check")["claim_id"], activity_uid=kit.uid("A2000"))


# ------------------------------------------------------------------------------------------------ preview parity
def test_the_preview_matches_the_outcome_and_writes_nothing(kit):
    cases = [(kit.submit("A2010", 500, "joints"), dict()), (kit.submit("A1020", None, pct=50, text="foundation half done"), dict(method="APPLY_PCT_TO_ASSIGNMENTS")),
             (kit.submit("A0100", None, pct=30, text="approvals 30"), dict())]
    for r, kw in cases:
        before = snapshot(kit)
        pv = decisions.preview_decision(kit.sup, r["claim_id"], **kw)
        nothing_written(kit, before)
        assert pv["ok"]
        d = kit.approve(r["claim_id"], **kw)
        assert pv["applied"] == d["applied"] and pv["result"] == d["result"] and pv["method"] == d["method"] and pv["action"] == d["action"]
    bad = decisions.preview_decision(kit.sup, kit.submit("A2000", None, pct=10, text="pipe 10")["claim_id"])
    assert bad["ok"] is False and bad["error"]["code"] == "PERCENT_METHOD_REQUIRED"
    with raises("PERMISSION_DENIED", 403):
        decisions.preview_decision(kit.se, cases[0][0]["claim_id"])


# ------------------------------------------------------------------------------------------------ atomicity
@pytest.mark.parametrize("target,pass_first", [("notify", 0), ("audit", 0), ("audit", 1), ("audit", 2)])
def test_a_failure_anywhere_in_a_decision_leaves_no_trace_and_the_claim_decidable(kit, monkeypatch, target, pass_first):
    """crash after the decision row and ledger rows exist (at the notification, or at the 1st / 2nd / 3rd audit entry): everything rolls back"""
    r = kit.submit("A2010", 500, "joints")
    before = snapshot(kit)
    real_audit, calls = decisions.audit.log, {"n": 0}

    def failing_audit(*a, **k):
        if calls["n"] < pass_first:
            calls["n"] += 1
            return real_audit(*a, **k)
        raise RuntimeError("simulated crash mid-decision")

    def failing_notify(*a, **k):
        raise RuntimeError("simulated crash mid-decision")
    with monkeypatch.context() as m:
        if target == "notify":
            m.setattr(decisions, "notify", failing_notify)
        else:
            m.setattr(decisions.audit, "log", failing_audit)
        with pytest.raises(RuntimeError, match="simulated crash"):
            kit.approve(r["claim_id"])
    nothing_written(kit, before)                                                           # no decision, no ledger row, no notification, no audit row
    assert claims.get_claim(kit.sup, r["claim_id"])["status"] == "MATCHED" and kit.pct("A2010") == 0
    assert kit.approve(r["claim_id"])["status"] == "APPROVED"                              # and the very same claim can then be decided normally


def test_the_recorded_result_always_equals_the_ledger_derived_percentage(kit):
    for ext, kw, q in [("A2010", {}, (700, "joints")), ("A1020", dict(method="APPLY_PCT_TO_ASSIGNMENTS"), None), ("A2000", {}, (7, "km"))]:
        r = kit.submit(ext, *(q or (None,)), pct=None if q else 37, text=f"claim for {ext}")
        d = kit.approve(r["claim_id"], **kw)
        assert D(d["result"]["activity_pct_after"]) == kit.pct(ext)


# ------------------------------------------------------------------------------------------------ non-approved claims never move progress
def test_pending_rejected_withdrawn_and_disputed_claims_never_affect_progress(kit):
    big = lambda ext, q, u, t: kit.submit(ext, q, u, text=t)
    pending = big("A2010", 1900, "joints", "pending, never decided")
    unmatched = kit.submit(None, 900, "joints", reported_activity_ref="welds", text="pending unmatched")
    rejected = big("A2000", 24, "km", "to be rejected")
    kit.approve(rejected["claim_id"], action="REJECT", justification="Photos do not show the stated chainage")
    withdrawn = big("A3010", 64, "nos", "to be withdrawn")
    claims.withdraw_claim(kit.se, withdrawn["claim_id"], "wrong activity")
    held = big("A1020", 480, "m3", "to be held")
    kit.approve(held["claim_id"], action="HOLD", clarification_question="Pour records?")
    pctonly = kit.submit("A1010", None, pct=90, text="percent-only pending")
    assert kit.ledger() == ([], []) and kit.project_pct() == 0
    from backend.v2.domain import rollups
    s = rollups.project_summary(kit.sup)
    assert s["physical_pct"] == 0 and s["activities"]["completed"] == 0 and s["activities"]["in_progress"] == 0
    assert all(r["physical_pct"] == 0 for r in rollups.stage_progress(kit.sup)) and all(r["physical_pct"] == 0 for r in rollups.discipline_progress(kit.sup))
    assert all(p["physical_pct"] == 0 for p in rollups.timeline(kit.sup, step_days=30)["points"])
    assert s["claims"]["pending_total"] == 4 and s["claims"]["REJECTED"] == 1 and s["claims"]["WITHDRAWN"] == 1
    kit.approve(pending["claim_id"])                                                         # only an approval moves anything
    assert kit.pct("A2010") == D("95.000") and kit.pct("A2000") == 0 and kit.pct("A1020") == 0 and kit.pct("A3010") == 0
