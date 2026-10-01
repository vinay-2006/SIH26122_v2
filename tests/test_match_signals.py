"""Unit tests (DB-free) for the explainable matching signals and the multi-claim rules extractor."""
from datetime import date

import pytest

from backend.routers.matching import calculate_hybrid_score, generate_hybrid_candidates, match_claim, rank_and_explain_candidates
from backend.shared import match_signals as ms
from backend.shared.rule_extraction import extract_with_rules_segments, find_document_date, force_rules, track_rules


# ---------------------------------------------------------------------------------------------------------- location
def test_location_is_graded_and_identifiers_must_agree():
    assert ms.location_token_score("Unit 5 pipe rack", "Unit 5 pipe rack") == 1.0
    assert ms.location_token_score("near Unit 5 pipe rack", "Unit 5 pipe rack area") >= 0.6
    assert ms.location_token_score("Pump Station 3", "Pump Station 4") == 0.0, "different station numbers are different places"
    assert ms.location_token_score("Pump Station 3", "Control Room") == 0.0
    assert ms.location_token_score(None, "Unit 5") == 0.0


# ----------------------------------------------------------------------------------------------------- ids and WBS
ACTS = [{"activity_id": "NRL-PEI-020"}, {"activity_id": "NRL-PEI-02"}, {"activity_id": "CIV-1"}]


def test_activity_ids_are_found_as_whole_tokens_only():
    assert ms.find_activity_ids_in_text("progress on nrl-pei-020 today", ACTS) == ["NRL-PEI-020"]
    assert ms.find_activity_ids_in_text("NRL-PEI-0200 is something else", ACTS) == []
    assert ms.find_activity_ids_in_text("see (NRL-PEI-020).", ACTS) == ["NRL-PEI-020"]
    assert ms.find_activity_ids_in_text("", ACTS) == []


def test_wbs_code_is_trusted_only_in_wbs_context():
    assert ms.wbs_in_text("WBS 4.05 piping at 40%", "4.05")
    assert ms.wbs_in_text("| 4.05 | Piping |", "4.05")
    assert not ms.wbs_in_text("poured 14.05 m3", "4.05"), "a quantity must not be read as a WBS code"
    assert not ms.wbs_in_text("anything", None)


# -------------------------------------------------------------------------------------------------- date / quantity
def test_date_signal_rewards_the_planned_window_and_penalises_the_implausible():
    s, f = date(2026, 6, 1), date(2026, 8, 1)
    assert ms.date_signal(date(2026, 7, 1), s, f)[0] == ms.DATE_IN_WINDOW
    assert ms.date_signal(date(2026, 5, 25), s, f)[0] == ms.DATE_IN_WINDOW          # a week before start: mobilisation grace
    assert ms.date_signal(date(2026, 3, 1), s, f)[0] == ms.DATE_FAR_PENALTY         # 92 days before the start
    assert ms.date_signal(date(2027, 3, 1), s, f)[0] == ms.DATE_FAR_PENALTY         # long after the finish
    assert ms.date_signal(None, s, f) == (None, None)


def test_quantity_signal_needs_the_same_unit():
    assert ms.quantity_signal(40, "m", 100, "m")[0] == ms.QUANTITY_AGREE
    assert ms.quantity_signal(900, "m", 100, "m")[0] == ms.QUANTITY_IMPOSSIBLE
    assert ms.quantity_signal(40, "t", 100, "m") == (None, None), "different units say nothing"
    assert ms.quantity_signal(12, "kilometres", 100, "km")[0] == ms.QUANTITY_AGREE


# ------------------------------------------------------------------------------------------------------ batch context
def test_batch_context_gives_a_bounded_prior():
    b = ms.BatchContext()
    assert not b and b.stage_prior("S1") == 0.0
    b.vote("S1", "A"), b.vote("S1", "B"), b.vote("S2", "C")
    assert b.stage_prior("S1") == pytest.approx(2 / 3) and b
    d, sup, _ = ms.extension_adjustment(claim_text="x", claim_date=None, claim_qty=None, claim_uom=None,
                                        activity={"stage_id": "S1"}, stage_name=None, batch=b)
    assert 0 < d <= ms.BATCH_STAGE_MAX and "same stage" in sup[0]


def test_weak_text_evidence_only_penalises_when_the_schedule_data_is_rich():
    plain = ms.extension_adjustment(claim_text="x", claim_date=None, claim_qty=None, claim_uom=None, activity={}, stage_name=None, text_score=0.1)
    assert plain[0] == 0.0, "a bare fixture row carries nothing to judge by"
    rich = ms.extension_adjustment(claim_text="x", claim_date=None, claim_qty=None, claim_uom=None,
                                   activity={"planned_start": date(2026, 1, 1)}, stage_name=None, text_score=0.1)
    assert rich[0] < 0 and "weak text evidence" in rich[2][0]


# --------------------------------------------------------------------------------- distinctive-word coverage (idf)
def test_generic_words_do_not_make_two_different_activities_look_alike():
    names = ["Platforms, ladders and access structures", "DCS and ESD panel installation", "Pump and compressor installation and grouting",
             "Cable tray, cable laying and termination", "Heat exchanger setting", "Piping erection and welding",
             "Field instrument installation and tubing", "Transformer and switchgear installation"]
    idf = ms.build_idf(names)
    claim = "Platforms and ladders fabrication and installation reached 40 percent"
    right = ms.distinctive_coverage(claim, names[0], idf)
    wrong = ms.distinctive_coverage(claim, names[1], idf)
    assert right >= 0.5 and wrong < 0.25 and right > 2 * wrong
    assert ms.capped_text_score(0.70, wrong) < 0.4 and ms.capped_text_score(0.55, right) == pytest.approx(0.55)


# ------------------------------------------------------------------------------------------ matcher integration (pure)
def _acts():
    base = dict(schedule_id="S", project_id=None, stage_id=None, location="Unit 5", discipline="PIPING",
                planned_start=date(2026, 1, 1), planned_finish=date(2026, 12, 31))
    names = [("A-001", "Platforms, ladders and access structures"), ("A-002", "DCS and ESD panel installation"),
             ("A-003", "Pump and compressor installation and grouting"), ("A-004", "Cable tray, cable laying and termination"),
             ("A-005", "Heat exchanger setting"), ("A-006", "Piping erection and welding"),
             ("A-007", "Field instrument installation and tubing"), ("A-008", "Transformer and switchgear installation")]
    return [{**base, "activity_id": i, "activity_name": n, "stage_name": "Erection"} for i, n in names]


def _claim(text, **kw):
    return {"event_id": "E1", "schedule_id": "S", "raw_claim_text": text, "event_date": date(2026, 6, 1), "location": "Unit 5", **kw}


def test_generic_overlap_loses_to_the_distinctive_match():
    cands = match_claim(_claim("Platforms and ladders fabrication and installation reached 40 percent"), _acts())
    assert cands and cands[0].activity_id == "A-001"
    assert cands[0].composite_confidence > cands[1].composite_confidence + 0.05


def test_activity_id_cited_in_text_is_an_exact_match_without_extraction():
    cands = match_claim(_claim("A-006 piping erection 40 percent"), _acts())
    assert len(cands) == 1 and cands[0].activity_id == "A-006" and cands[0].match_tier == "EXACT_ID"
    assert 0.9 < cands[0].composite_confidence < 1.0 and "found in the report text" in cands[0].supporting_signals
    # two different ids in one snippet is ambiguous: not an exact match
    assert match_claim(_claim("A-006 and A-005 both progressing"), _acts())[0].match_tier != "EXACT_ID"


def test_ranking_is_by_composite_and_deterministic():
    cands = generate_hybrid_candidates(_claim("Heat exchanger setting 46 percent complete"), _acts())
    ranked = rank_and_explain_candidates(cands)
    assert ranked[0].activity_id == "A-005" and [c.rank_order for c in ranked] == [1, 2, 3]
    assert [c.composite_confidence for c in ranked] == sorted([c.composite_confidence for c in ranked], reverse=True)
    assert rank_and_explain_candidates(generate_hybrid_candidates(_claim("Heat exchanger setting 46 percent complete"), _acts()))[0].activity_id == "A-005"


def test_legacy_scoring_is_unchanged_for_the_original_inputs():
    assert calculate_hybrid_score(0.8, 0.6, 1.0, 1.0, has_semantic_results=True) == pytest.approx(0.5 * 0.8 + 0.25 * 0.6 + 0.15 + 0.10)
    assert calculate_hybrid_score(0.0, 0.6, 1.0, 1.0, has_semantic_results=False) == pytest.approx(0.5 * 0.6 + 0.3 + 0.2)
    # a pool that was semantically searched is scored on one scale (a candidate the index did not return has semantic 0)
    assert calculate_hybrid_score(0.0, 0.6, 1.0, 1.0, has_semantic_results=True, semantic_scale_for_all=True) == pytest.approx(0.25 * 0.6 + 0.25)


# ------------------------------------------------------------------------------------------------------ fingerprints
def test_claim_fingerprint_identifies_a_claim_not_a_file():
    kw = dict(project_id="P", schedule_id="S", activity_id="A-1", event_date=date(2026, 9, 29), claim_mode="CUMULATIVE_PCT",
              claimed_pct=42.0, claimed_quantity=None, claimed_uom=None, event_type="PROGRESS_UPDATE")
    assert ms.claim_fingerprint(**kw) == ms.claim_fingerprint(**{**kw, "claimed_pct": 42.00})
    assert ms.claim_fingerprint(**kw) != ms.claim_fingerprint(**{**kw, "claimed_pct": 45.0}), "a different value is a different claim"
    assert ms.claim_fingerprint(**kw) != ms.claim_fingerprint(**{**kw, "activity_id": "A-2"})
    assert ms.claim_fingerprint(**kw) != ms.claim_fingerprint(**{**kw, "event_date": date(2026, 9, 30)})


# ---------------------------------------------------------------------------------------------- rules extraction
REPORT = """Daily report
Date: 29/09/2026
NRL-PEI-020 | Piping erection and welding | 42 % complete
Pipe rack steel erection reached 80 percent near Unit 5 pipe rack
Manpower on site: 214 workers
Cable laying 12 km completed at Unit 5 cable corridor
Weather: clear
"""


def test_rules_extractor_yields_one_claim_per_reported_item_with_the_report_date():
    segs = extract_with_rules_segments(REPORT)
    assert len(segs) == 3, "headings, manpower and weather lines are not claims"
    ids = [c.reported_activity_id for _, c in segs]
    assert ids == ["NRL-PEI-020", None, None]
    assert [c.claimed_pct for _, c in segs][:2] == [42.0, 80.0] and segs[2][1].claimed_quantity == 12.0 and segs[2][1].claimed_uom == "km"
    assert {c.event_date for _, c in segs} == {date(2026, 9, 29)}
    assert find_document_date("report 2026-09-29") == date(2026, 9, 29) and find_document_date("no date") is None


def test_rules_fallback_is_switchable_per_request_and_observable():
    from backend.shared.llm_extraction import extract_claim_fields_batch

    with track_rules() as t:
        with force_rules():
            claims = extract_claim_fields_batch(REPORT)
    assert len(claims) == 3 and t.used
