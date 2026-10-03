"""Regression: the v2 adapter returns what the EXISTING engine returns, for the supplied matching cases.

Three references, none of them edited to make a test pass:
  1. the legacy path run directly (legacy_reference.py)          -> must equal the v2 adapter exactly (tier, rank, confidence, every sub-score, signals)
  2. legacy_snapshot.json (generated once from the legacy engine) -> pins the engine's numbers so a change of engine OR adapter is noticed
  3. sample_data/expected/matching_results.json (IMMUTABLE)       -> compared on the outcome the file describes (which activity, matched or not)
"""
import json
import uuid
from datetime import date
from pathlib import Path

import pytest

import legacy_reference as ref  # noqa: E402  (tests/v2_matching on sys.path via conftest)
from backend.v2.matching import service as svc

HERE = Path(__file__).parent
GOLDEN = {g["case_id"]: g for g in json.load(open(ref.SAMPLE / "expected" / "matching_results.json"))}
SNAP = json.load(open(HERE / "legacy_snapshot.json"))
PROJECT, VERSION = uuid.uuid4(), uuid.uuid4()
TOL = 1e-6


def v2_activities():
    return [{**a, "schedule_id": str(VERSION), "project_id": str(PROJECT), "activity_uid": uuid.uuid4()} for a in ref.canonical_activities()]


def v2_claim(case, discipline=None):
    return ({"event_id": uuid.uuid4(), "event_date": date.fromisoformat(case["event_date"]), "raw_claim_text": case["raw_claim_text"], "input_channel": case["input_channel"],
             "reported_activity_ref": case.get("reported_activity_id"), "discipline_code": discipline or case.get("discipline"), "asset_tag": case.get("asset_tag"),
             "location": case.get("location"), "claimed_pct": case.get("claimed_pct"), "claim_mode": case.get("claim_mode", "CUMULATIVE_PCT"), "event_type": case.get("event_type")},
            [{"reported_qty": case["claimed_quantity"], "reported_uom": case.get("claimed_uom")}] if case.get("claimed_quantity") is not None else [])


def shape(cands):
    return [dict(activity_id=c.activity_id, rank_order=c.rank_order, match_tier=c.match_tier, composite_confidence=c.composite_confidence, semantic_score=c.semantic_score,
                 fuzzy_score=c.fuzzy_score, location_score=c.location_score, discipline_score=c.discipline_score, supporting_signals=c.supporting_signals,
                 disqualifying_signals=c.disqualifying_signals) for c in cands]


def same(a, b):
    assert [x["activity_id"] for x in a] == [x["activity_id"] for x in b]
    for x, y in zip(a, b):
        for k in x:
            if isinstance(x[k], float) or isinstance(y[k], float):
                assert (x[k] is None) == (y[k] is None) and (x[k] is None or abs(x[k] - y[k]) < TOL), (k, x, y)
            else:
                assert x[k] == y[k], (k, x, y)


@pytest.mark.parametrize("case", ref.cases(), ids=lambda c: c["case_id"])
def test_adapter_equals_legacy_engine(case):
    legacy = ref.summarise(ref.run_legacy(ref.canonical_activities(), case))
    claim, qty = v2_claim(case)
    out = svc.rank_claim(PROJECT, VERSION, claim, qty, v2_activities())
    assert out["semantic_available"], out["semantic_error"]
    # the adapter returns every candidate the engine ranked; ids are external activity ids; compare the persisted top 3 field by field
    same(legacy[:3], ref.summarise(out["candidates"][:3]))


@pytest.mark.parametrize("case", ref.cases(), ids=lambda c: c["case_id"])
def test_adapter_equals_pinned_legacy_snapshot(case):
    claim, qty = v2_claim(case)
    out = svc.rank_claim(PROJECT, VERSION, claim, qty, v2_activities())
    same(SNAP[case["case_id"]][:3], ref.summarise(out["candidates"][:3]))


def test_discipline_code_in_v2_form_gives_the_same_ranking():
    case = next(c for c in ref.cases() if c["case_id"] == "MATCH-003")
    a, qa = v2_claim(case)
    b, qb = v2_claim(case, discipline="PIPING")
    ra = svc.rank_claim(PROJECT, VERSION, a, qa, v2_activities())
    rb = svc.rank_claim(PROJECT, VERSION, b, qb, v2_activities())
    same(ref.summarise(ra["candidates"][:3]), ref.summarise(rb["candidates"][:3]))


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_outcome_matches_the_immutable_golden_file(case_id):
    g = GOLDEN[case_id]
    case = next(c for c in ref.cases() if c["case_id"] == case_id)
    claim, qty = v2_claim(case)
    out = svc.rank_claim(PROJECT, VERSION, claim, qty, v2_activities())
    top = [c.activity_id for c in out["candidates"][:3]]
    if g["expected_status"] == "MATCHED":
        assert out["matched"] and out["activity_id"] == g["matched_activity_id"]
        assert set(g["candidate_activities"]) <= set(top)
    elif g["expected_status"] == "AMBIGUOUS":
        assert not out["matched"] and out["ambiguous"]
        assert set(g["candidate_activities"]) <= set(top[:2])                 # the two ambiguous activities are exactly the engine's rank 1 and 2
    else:
        # the golden file says: not matched, no activity. (The legacy engine also labels this case "ambiguous" -- its rank-1/rank-2 gap of 0.0016 is under 0.05 even
        # though both are far below 0.40 -- and says so in the reason text; the status is UNMATCHED either way. See the finding in docs/V2_PARITY_MATRIX.md.)
        assert not out["matched"] and out["activity_id"] is None


def test_known_golden_differences_are_the_files_own_labels_not_engine_behaviour():
    """MATCH-002/003/005 record tier names (TIER_2_ASSET_TAG ...) and confidences (0.92, 0.86, 0.12) that the existing engine does not produce; the
    engine's real values are pinned in legacy_snapshot.json. This test documents the difference so it is never mistaken for an adapter defect."""
    t = {k: SNAP[k][0] for k in SNAP}
    assert (GOLDEN["MATCH-002"]["confidence_score"], t["MATCH-002"]["composite_confidence"]) == (0.92, 0.875)
    assert t["MATCH-002"]["match_tier"] == "EXACT_ASSET" and GOLDEN["MATCH-002"]["match_tier"] == "TIER_2_ASSET_TAG"
    assert GOLDEN["MATCH-003"]["confidence_score"] == 0.86 and t["MATCH-003"]["composite_confidence"] < 0.86
    assert GOLDEN["MATCH-005"]["candidate_activities"] == [] and len(SNAP["MATCH-005"]) == 3     # the engine keeps its low-confidence guesses as candidates (never matched)
    gap = SNAP["MATCH-005"][0]["composite_confidence"] - SNAP["MATCH-005"][1]["composite_confidence"]
    assert 0 < gap < 0.05 and SNAP["MATCH-005"][0]["composite_confidence"] < 0.40            # unmatched for both reasons in the legacy rules


def test_outcome_thresholds_are_the_legacy_ones():
    """decide_outcome re-states the legacy run_claim_match rules; read the numbers back out of the legacy source so a drift in either place fails here"""
    flat = " ".join((ref.ROOT / "backend" / "routers" / "matching.py").read_text().split())
    assert "if diff < 0.05:" in flat and "candidates[0].composite_confidence > 0.40" in flat
    assert "in [EXACT_ID, EXACT_ASSET, HYBRID_FALLBACK]" in flat
    mine = " ".join((ref.ROOT / "backend" / "v2" / "matching" / "service.py").read_text().split())
    assert "if diff < 0.05:" in mine and "composite_confidence > 0.40" in mine
    assert "(m.EXACT_ID, m.EXACT_ASSET, m.HYBRID_FALLBACK)" in mine
