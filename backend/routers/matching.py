import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.context import gates
from backend.shared.workflow_flags import with_workflow_flags
from backend.context.event import EventContext
from backend.shared.auth import UserProfile
from backend.shared.schemas import CandidateMatch, ExecutionClaim, ScheduleActivity
from backend.shared.discipline_normalize import normalize_discipline as _shared_normalize_discipline
from backend.shared.match_signals import (
    MIN_POOL_FOR_IDF,
    BatchContext,
    build_idf,
    capped_text_score,
    distinctive_coverage,
    extension_adjustment,
    find_activity_ids_in_text,
    location_token_score,
)
from backend.shared.wbs_split import (
    SplitEditError,
    allocate_split,
    apply_manual_split_edit,
    classify_claim_scope,
    find_wbs_group,
)

logger = logging.getLogger(__name__)

try:
    from backend.shared.db import get_connection
except ImportError:
    try:
        from backend.shared.db import get_conn as get_connection
    except ImportError:
        get_connection = None

try:
    from backend.shared.audit import write_audit_log
except ImportError:
    write_audit_log = None

try:
    from backend.shared import schedule_index
except ImportError:
    schedule_index = None

from backend.services.matching_eligibility_service import (
    MatchingEligibilityService,
    REASON_COMPLETED,
    REASON_ELIGIBLE,
    REASON_WRONG_PROJECT,
    REASON_WRONG_SCHEDULE,
    REASON_WRONG_STAGE,
)


# M3 Match-Tier Constants
EXACT_ID = "EXACT_ID"
EXACT_ASSET = "EXACT_ASSET"
HYBRID_FALLBACK = "HYBRID_FALLBACK"
HARD_MISMATCH = "HARD_MISMATCH"

router = APIRouter(prefix="/api/v1/claims", tags=["matching"])


@router.get("/matching/health")
def health():
    return {"router": "matching", "status": "ok"}


@router.get("/{event_id}/candidates")
def get_candidates_endpoint(
    event_id: str,
    _event_context: EventContext = Depends(gates.event_view),
):
    """
    GET /api/v1/claims/{event_id}/candidates
    Fetches the stored top-3 candidate_matches rows for a claim, as written
    by the most recent /match or /rematch call.
    Requires authentication and active membership (VIEW_EXECUTION_EVENTS) in the claim's project.
    """
    if get_connection is None:
        raise HTTPException(
            status_code=500, detail="Database connection module unavailable."
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM execution_events WHERE event_id = %s",
                (event_id,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution claim '{event_id}' not found.",
                )

            cur.execute(
                """
                SELECT * FROM candidate_matches
                WHERE event_id = %s
                ORDER BY rank_order ASC
                """,
                (event_id,),
            )
            rows = cur.fetchall()

    return {
        "event_id": event_id,
        "candidates": [dict(r) for r in rows],
    }


def _get_act_dict(act: Union[ScheduleActivity, dict]) -> dict:
    if isinstance(act, dict):
        return act
    if hasattr(act, "model_dump"):
        return act.model_dump()
    return dict(act)


def match_exact_id(
    claim: Union[ExecutionClaim, dict],
    schedule_activities: List[Union[ScheduleActivity, dict]],
) -> Optional[CandidateMatch]:
    """
    Tier 1: EXACT_ID Matching.
    If reported_activity_id is provided and exactly matches a schedule activity_id
    within the same schedule_id, return CandidateMatch with EXACT_ID tier and 1.00 confidence,
    ONLY IF the activity is eligible for matching (e.g. not canonically COMPLETED).
    The activity_id remains an unchanged source string.
    """
    if isinstance(claim, ExecutionClaim):
        reported_id = claim.reported_activity_id
        claim_schedule_id = claim.schedule_id
        event_id = claim.event_id
        claim_project_id = getattr(claim, "project_id", None)
        claim_stage_id = getattr(claim, "stage_id", None)
        is_rework = bool(getattr(claim, "is_rework", False) or getattr(claim, "reopened_from_actual_id", None) or getattr(claim, "event_type", None) == "REWORK")
    elif isinstance(claim, dict):
        reported_id = claim.get("reported_activity_id")
        claim_schedule_id = claim.get("schedule_id")
        event_id = claim.get("event_id", "evt_unknown")
        claim_project_id = claim.get("project_id")
        claim_stage_id = claim.get("stage_id")
        is_rework = bool(claim.get("is_rework") or claim.get("reopened_from_actual_id") or claim.get("event_type") == "REWORK")
    else:
        return None

    if not reported_id:
        return None

    for act in schedule_activities:
        act_dict = _get_act_dict(act)
        act_id = act_dict.get("activity_id")
        act_schedule_id = act_dict.get("schedule_id")

        if not act_id or not act_schedule_id:
            continue

        if act_schedule_id == claim_schedule_id and act_id == reported_id:
            # Enforce Phase 6/7 Eligibility Gate: exact match must not bypass eligibility
            eligibility = MatchingEligibilityService.get_activity_eligibility(
                act_dict,
                expected_project_id=claim_project_id,
                expected_schedule_id=claim_schedule_id,
                expected_stage_id=claim_stage_id,
                is_rework=is_rework,
            )
            if not eligibility.eligible:
                logger.info(
                    "Exact ID candidate '%s' disqualified: %s", act_id, eligibility.reason
                )
                return None

            return CandidateMatch(
                candidate_id=f"cand_{event_id}_{act_id}",
                event_id=event_id,
                schedule_id=act_schedule_id,
                activity_id=act_id,
                rank_order=1,
                match_tier=EXACT_ID,
                composite_confidence=1.00,
                supporting_signals="Exact activity ID match",
            )

    return None


def match_exact_asset(
    claim: Union[ExecutionClaim, dict],
    schedule_activities: List[Union[ScheduleActivity, dict]],
) -> List[CandidateMatch]:
    """
    Tier 2: EXACT_ASSET Matching.
    Matches claims to schedule activities where asset_tag exactly matches
    within the same schedule_id. Both asset_tags must be non-null and non-empty.
    Confidence ranges between 0.80 and 0.95 based on discipline and location agreement,
    and is capped at 0.40 if there is a hard discipline mismatch.
    """
    if isinstance(claim, ExecutionClaim):
        claim_asset = claim.asset_tag
        claim_schedule_id = claim.schedule_id
        claim_disc = claim.discipline
        claim_loc = claim.location
        event_id = claim.event_id
    elif isinstance(claim, dict):
        claim_asset = claim.get("asset_tag")
        claim_schedule_id = claim.get("schedule_id")
        claim_disc = claim.get("discipline")
        claim_loc = claim.get("location")
        event_id = claim.get("event_id", "evt_unknown")
    else:
        return []

    if not claim_asset or not str(claim_asset).strip():
        return []

    claim_asset_clean = str(claim_asset).strip()
    candidates: List[CandidateMatch] = []

    for act in schedule_activities:
        act_dict = _get_act_dict(act)
        act_asset = act_dict.get("asset_tag")
        act_schedule_id = act_dict.get("schedule_id")
        act_id = act_dict.get("activity_id")
        act_disc = act_dict.get("discipline")
        act_loc = act_dict.get("location")

        if not act_asset or not str(act_asset).strip():
            continue

        if act_schedule_id != claim_schedule_id:
            continue

        if str(act_asset).strip() != claim_asset_clean:
            continue

        # Enforce Phase 6/7 Eligibility Gate: exact asset match must not bypass eligibility
        claim_project_id = getattr(claim, "project_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("project_id") if isinstance(claim, dict) else None)
        claim_stage_id = getattr(claim, "stage_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("stage_id") if isinstance(claim, dict) else None)
        is_rework = bool(getattr(claim, "is_rework", False) if isinstance(claim, ExecutionClaim) else (claim.get("is_rework") or claim.get("reopened_from_actual_id") or claim.get("event_type") == "REWORK"))
        eligibility = MatchingEligibilityService.get_activity_eligibility(
            act_dict,
            expected_project_id=claim_project_id,
            expected_schedule_id=claim_schedule_id,
            expected_stage_id=claim_stage_id,
            is_rework=is_rework,
        )
        if not eligibility.eligible:
            logger.info(
                "Exact asset candidate '%s' disqualified: %s", act_id, eligibility.reason
            )
            continue

        # Asset tag matches within same schedule_id
        supporting: List[str] = ["asset tag matched"]
        disqualifying: List[str] = []

        disc_match = False
        disc_hard_mismatch = False
        if claim_disc and act_disc:
            if str(claim_disc).strip().lower() == str(act_disc).strip().lower():
                disc_match = True
                supporting.append("discipline matched")
            else:
                disc_hard_mismatch = True
                disqualifying.append(
                    f"discipline mismatch: claim={claim_disc}, activity={act_disc}"
                )

        loc_match = False
        if claim_loc and act_loc:
            if str(claim_loc).strip().lower() == str(act_loc).strip().lower():
                loc_match = True
                supporting.append("location matched")
            else:
                disqualifying.append(
                    f"location mismatch: claim={claim_loc}, activity={act_loc}"
                )

        # Confidence calculation
        if disc_hard_mismatch:
            confidence = 0.40
        else:
            agreements = (1 if disc_match else 0) + (1 if loc_match else 0)
            if agreements == 2:
                confidence = 0.95
            elif agreements == 1:
                confidence = 0.875
            else:
                confidence = 0.80

        cand = CandidateMatch(
            candidate_id=f"cand_{event_id}_{act_id}",
            event_id=event_id,
            schedule_id=act_schedule_id,
            activity_id=act_id,  # Unchanged source string
            rank_order=1,
            match_tier=EXACT_ASSET,
            composite_confidence=confidence,
            supporting_signals="; ".join(supporting) if supporting else None,
            disqualifying_signals="; ".join(disqualifying)
            if disqualifying
            else None,
        )
        candidates.append(cand)

    return candidates


DISCIPLINE_MAP = {
    "civil": "CIVIL",
    "piping": "PIPING",
    "static/rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "static equipment": "STATIC_ROTATING_EQUIPMENT",
    "rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "equipment": "STATIC_ROTATING_EQUIPMENT",
    "mech": "STATIC_ROTATING_EQUIPMENT",
    "mechanical": "STATIC_ROTATING_EQUIPMENT",
    "electrical": "ELECTRICAL",
    "elec": "ELECTRICAL",
    "instrumentation": "INSTRUMENTATION",
    "inst": "INSTRUMENTATION",
    "hse": "HSE",
    "safety": "HSE",
    "general works": "GENERAL_WORKS",
}


def normalize_discipline(disc: Optional[str]) -> Optional[str]:
    """Canonical discipline code; delegates to the one shared normaliser (same vocabulary as intake and the database)."""
    if not disc or not str(disc).strip():
        return None
    return _shared_normalize_discipline(str(disc))


try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


CONSTRUCTION_SYNONYMS = [
    ("reinforcement steel", "rebar"),
    ("reinforcement", "rebar"),
    ("shuttering", "formwork"),
    ("concreting", "concrete pour"),
]


def normalize_construction_text(text: Optional[str]) -> str:
    if not text:
        return ""
    res = str(text).lower()
    for orig, replacement in CONSTRUCTION_SYNONYMS:
        res = res.replace(orig, replacement)
    return res


def calculate_fuzzy_score(text1: Optional[str], text2: Optional[str]) -> float:
    """
    Calculates normalized text fuzzy similarity in [0.0, 1.0] using RapidFuzz.
    Combines ratio, token_set_ratio, and token_sort_ratio after construction terminology normalization.
    """
    if not text1 or not text2:
        return 0.0
    s1 = normalize_construction_text(text1).strip()
    s2 = normalize_construction_text(text2).strip()
    if not s1 or not s2:
        return 0.0

    if fuzz is not None:
        r1 = fuzz.ratio(s1, s2)
        r2 = fuzz.token_set_ratio(s1, s2)
        r3 = fuzz.token_sort_ratio(s1, s2)
        raw_score = max(r1, r2, r3)
        score = float(raw_score) / 100.0
    else:
        from difflib import SequenceMatcher

        score = SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    return max(0.0, min(1.0, float(score)))


def calculate_location_score(
    claim_location: Optional[str], activity_location: Optional[str]
) -> float:
    """
    Calculates normalized location agreement score [0.0, 1.0].
    Supports exact match, substring containment, and location aliases.
    """
    if not claim_location or not activity_location:
        return 0.0
    loc1 = str(claim_location).strip().lower()
    loc2 = str(activity_location).strip().lower()
    if not loc1 or not loc2:
        return 0.0

    if loc1 == loc2 or loc1 in loc2 or loc2 in loc1:
        return 1.0

    aliases = {
        "ps3": "pump station 3",
        "pump station 3": "ps3",
        "mcc-02": "mcc building",
        "mcc-01": "mcc building",
        "substation yard": "substation",
    }
    if aliases.get(loc1) == loc2 or aliases.get(loc2) == loc1:
        return 1.0

    return 0.0


def calculate_discipline_score(
    claim_discipline: Optional[str], activity_discipline: Optional[str]
) -> float:
    """
    Calculates normalized discipline agreement score [0.0, 1.0].
    Uses normalized discipline mapping.
    """
    d1 = normalize_discipline(claim_discipline)
    d2 = normalize_discipline(activity_discipline)
    if not d1 or not d2:
        return 0.0

    return 1.0 if d1 == d2 else 0.0


def calculate_hybrid_score(
    semantic_score: float,
    fuzzy_score: float,
    location_score: float,
    discipline_score: float,
    has_semantic_results: bool = True,
    semantic_scale_for_all: bool = False,
) -> float:
    """
    Calculates the composite confidence score for HYBRID_FALLBACK.
    Normal weights (when semantic scoring is available): 0.50 * sem + 0.25 * fuz + 0.15 * loc + 0.10 * disc.
    Dynamic re-scaled weights (when semantic score is unavailable/0 due to unindexed FAISS): 0.50 * fuz + 0.30 * loc + 0.20 * disc.
    Result is always clamped to [0.0, 1.0].
    """
    sem = max(0.0, min(1.0, float(semantic_score)))
    fuz = max(0.0, min(1.0, float(fuzzy_score)))
    loc = max(0.0, min(1.0, float(location_score)))
    disc = max(0.0, min(1.0, float(discipline_score)))

    # semantic_scale_for_all: the pool was semantically searched, so EVERY candidate is scored on the same weights (a
    # candidate the index did not return simply has semantic 0), instead of a fuzzy-heavy formula only for those.
    if not has_semantic_results or (sem == 0.0 and not semantic_scale_for_all):
        composite = 0.50 * fuz + 0.30 * loc + 0.20 * disc
    else:
        composite = 0.50 * sem + 0.25 * fuz + 0.15 * loc + 0.10 * disc

    return max(0.0, min(1.0, round(composite, 6)))


def process_semantic_results(
    claim: Union[ExecutionClaim, dict],
    semantic_results: List[Union[dict, object]],
) -> List[dict]:
    """
    M3 Semantic Retrieval Interface.
    Normalizes semantic retrieval outputs by clamping semantic_score to [0.0, 1.0]
    and preserving activity_id unchanged. Does NOT perform model loading or embeddings.
    """
    normalized: List[dict] = []
    if not semantic_results:
        return []

    for item in semantic_results:
        if isinstance(item, dict):
            act_id = item.get("activity_id")
            # Accept either key: "semantic_score" (this module's own
            # convention) or "score" (schedule_index.SearchCandidate's
            # field name, when passed in as a dict).
            raw_score = item.get("semantic_score", item.get("score", 0.0))
        else:
            act_id = getattr(item, "activity_id", None)
            # schedule_index.SearchCandidate exposes .score, not
            # .semantic_score -- check both so real FAISS results aren't
            # silently zeroed out.
            raw_score = getattr(item, "semantic_score", None)
            if raw_score is None:
                raw_score = getattr(item, "score", 0.0)

        if not act_id:
            continue

        act_id_preserved = act_id if isinstance(act_id, str) else str(act_id)

        try:
            score = float(raw_score)
        except (ValueError, TypeError):
            score = 0.0

        clamped_score = max(0.0, min(1.0, score))

        normalized.append(
            {
                "activity_id": act_id_preserved,
                "semantic_score": clamped_score,
            }
        )

    return normalized


def generate_hybrid_candidates(
    claim: Union[ExecutionClaim, dict],
    schedule_activities: List[Union[ScheduleActivity, dict]],
    semantic_results: Optional[List[Union[dict, object]]] = None,
    batch_context: Optional[BatchContext] = None,
) -> List[CandidateMatch]:
    """
    Tier 3: HYBRID_FALLBACK Candidate Generation.
    Generates CandidateMatch objects for semantic candidates (if provided) or schedule activities.
    When semantic_results is missing or empty, generates candidates for schedule activities using semantic_score = 0.0.
    Calculates 4 component scores: semantic_score, fuzzy_score, location_score, discipline_score,
    and combines them with formula: 0.50*sem + 0.25*fuz + 0.15*loc + 0.10*disc.

    On top of that base score (unchanged), activities that carry the data add explainable adjustments
    (see backend/shared/match_signals.py): stage name, planned-window date, quantity+unit, cited WBS code and the stage
    prior from the other claims of the same upload batch. The activity description joins the text comparison, and a
    location is "matched" on a graded token overlap, not only on exact substring. Activities that carry none of these
    (no description / stage / dates) score exactly as before.
    """
    if isinstance(claim, ExecutionClaim):
        claim_schedule_id = claim.schedule_id
        event_id = claim.event_id
        claim_text = claim.raw_claim_text
        claim_loc = claim.location
        claim_disc = claim.discipline
        claim_date = claim.event_date
        claim_qty = claim.claimed_quantity
        claim_uom = claim.claimed_uom
    elif isinstance(claim, dict):
        claim_schedule_id = claim.get("schedule_id")
        event_id = claim.get("event_id", "evt_unknown")
        claim_text = claim.get("raw_claim_text", "")
        claim_loc = claim.get("location")
        claim_disc = claim.get("discipline")
        claim_date = claim.get("event_date")
        claim_qty = claim.get("claimed_quantity")
        claim_uom = claim.get("claimed_uom")
    else:
        return []

    claim_project_id = getattr(claim, "project_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("project_id") if isinstance(claim, dict) else None)
    claim_stage_id = getattr(claim, "stage_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("stage_id") if isinstance(claim, dict) else None)
    is_rework = bool(getattr(claim, "is_rework", False) if isinstance(claim, ExecutionClaim) else (claim.get("is_rework") or claim.get("reopened_from_actual_id") or claim.get("event_type") == "REWORK"))

    act_map: dict[str, Union[ScheduleActivity, dict]] = {}
    for act in schedule_activities:
        act_dict = _get_act_dict(act)
        act_id = act_dict.get("activity_id")
        act_sched = act_dict.get("schedule_id")
        if not act_id or act_sched != claim_schedule_id:
            continue

        # Enforce Phase 6/7 Eligibility Gate: only eligible activities enter candidate generation
        eligibility = MatchingEligibilityService.get_activity_eligibility(
            act_dict,
            expected_project_id=claim_project_id,
            expected_schedule_id=claim_schedule_id,
            expected_stage_id=claim_stage_id,
            is_rework=is_rework,
        )
        if eligibility.eligible:
            act_map[act_id] = act

    if not act_map:
        return []

    sem_score_map: dict[str, float] = {}
    if semantic_results:
        proc_sem = process_semantic_results(claim, semantic_results)
        for item in proc_sem:
            sem_score_map[item["activity_id"]] = item["semantic_score"]

    targets: List[dict] = []
    for act_id in act_map.keys():
        targets.append(
            {
                "activity_id": act_id,
                "semantic_score": sem_score_map.get(act_id, 0.0),
            }
        )

    candidates: List[CandidateMatch] = []

    # distinctive-word weights from this schedule's own activity texts (only meaningful for a real schedule, not a 3-row fixture)
    idf = None
    if len(act_map) >= MIN_POOL_FOR_IDF:
        idf = build_idf(
            f"{_get_act_dict(a).get('activity_name') or ''} {_get_act_dict(a).get('description') or ''}" for a in act_map.values()
        )

    for item in targets:
        act_id = item["activity_id"]
        sem_score = item["semantic_score"]

        act = act_map.get(act_id)
        if not act:
            continue

        if isinstance(act, ScheduleActivity):
            act_name = act.activity_name
            act_loc = act.location
            act_disc = act.discipline
            act_schedule_id = act.schedule_id
        else:
            act_name = act.get("activity_name", "")
            act_loc = act.get("location")
            act_disc = act.get("discipline")
            act_schedule_id = act.get("schedule_id")
        act_extra = _get_act_dict(act)
        act_desc = act_extra.get("description")

        fuz_score = calculate_fuzzy_score(claim_text, act_name)
        if act_desc:  # the description is extra evidence about what the activity is; it can raise, never lower, the score
            fuz_score = max(fuz_score, round(0.95 * calculate_fuzzy_score(claim_text, f"{act_name}. {act_desc}"), 6))
        generic_overlap = None
        if idf is not None:
            # the activity NAME defines the activity; the description can only add recall, never dilute a name that is fully present
            coverage = max(distinctive_coverage(claim_text, act_name, idf),
                           distinctive_coverage(claim_text, f"{act_name} {act_desc or ''}", idf))
            capped = capped_text_score(fuz_score, coverage)
            if capped < fuz_score - 0.02:
                generic_overlap = (fuz_score, capped, coverage)
            fuz_score = capped
        loc_score = calculate_location_score(claim_loc, act_loc)
        if loc_score == 0.0:
            graded = location_token_score(claim_loc, act_loc)
            if graded >= 0.6:
                loc_score = graded
        disc_score = calculate_discipline_score(claim_disc, act_disc)

        comp_confidence = calculate_hybrid_score(
            sem_score,
            fuz_score,
            loc_score,
            disc_score,
            has_semantic_results=bool(semantic_results),
            semantic_scale_for_all=bool(semantic_results) and idf is not None,
        )

        supporting: List[str] = [
            f"semantic score: {sem_score:.2f}",
            f"fuzzy score: {fuz_score:.2f}",
        ]
        disqualifying: List[str] = []
        if generic_overlap:
            disqualifying.append(
                f"overlap is mostly generic words (text similarity {generic_overlap[0]:.2f} limited to {generic_overlap[1]:.2f}; "
                f"distinctive-word coverage {generic_overlap[2]:.2f})"
            )

        has_disc_mismatch = False
        if claim_disc and act_disc:
            if disc_score > 0.0:
                supporting.append("discipline matched")
            else:
                has_disc_mismatch = True
                disqualifying.append(
                    f"discipline mismatch: claim={claim_disc}, activity={act_disc}"
                )

        has_loc_mismatch = False
        if claim_loc and act_loc:
            if loc_score > 0.0:
                supporting.append("location matched")
            else:
                has_loc_mismatch = True
                disqualifying.append(
                    f"location mismatch: claim={claim_loc}, activity={act_loc}"
                )

        # Additional explainable signals (stage / date / quantity / WBS / batch prior); never past the conflict cap below
        delta, extra_support, extra_disq = extension_adjustment(
            claim_text=claim_text, claim_date=claim_date, claim_qty=claim_qty, claim_uom=claim_uom,
            activity=act_extra, stage_name=act_extra.get("stage_name"), batch=batch_context,
            text_score=max(fuz_score, sem_score),
        )
        if delta:
            comp_confidence = max(0.0, min(1.0, round(comp_confidence + delta, 6)))
        supporting.extend(extra_support)
        disqualifying.extend(extra_disq)

        # Contextual Matching Gates / Confidence Caps for HYBRID_FALLBACK:
        # Prevent high semantic or fuzzy text similarity from overriding explicit context conflicts
        if has_disc_mismatch or has_loc_mismatch:
            comp_confidence = min(0.40, comp_confidence)

        cand = CandidateMatch(
            candidate_id=f"cand_{event_id}_{act_id}",
            event_id=event_id,
            schedule_id=act_schedule_id,
            activity_id=act_id,  # Unchanged source string
            rank_order=1,
            match_tier=HYBRID_FALLBACK,
            composite_confidence=comp_confidence,
            semantic_score=sem_score,
            fuzzy_score=fuz_score,
            location_score=loc_score,
            discipline_score=disc_score,
            supporting_signals="; ".join(supporting) if supporting else None,
            disqualifying_signals="; ".join(disqualifying)
            if disqualifying
            else None,
        )
        candidates.append(cand)

    return candidates


def rank_and_explain_candidates(
    candidates: List[CandidateMatch],
) -> List[CandidateMatch]:
    """
    Ranks CandidateMatch objects by composite_confidence descending (with deterministic activity_id tie-breaker),
    assigns rank_order (1, 2, 3 max), populates supporting/disqualifying explanation signals,
    and returns at most top 3 candidates.
    """
    if not candidates:
        return []

    # Rank by composite confidence (it carries every signal, including the stage/date/quantity/batch adjustments),
    # then by text/semantic relevance, then activity_id ascending (deterministic)
    sorted_cands = sorted(
        candidates,
        key=lambda c: (
            -round(c.composite_confidence, 4),
            -max(c.fuzzy_score or 0.0, c.semantic_score or 0.0),
            str(c.activity_id),
        ),
    )

    top_cands = sorted_cands[:3]

    ranked: List[CandidateMatch] = []
    for idx, cand in enumerate(top_cands, start=1):
        supporting_list: List[str] = []
        disqualifying_list: List[str] = []

        if cand.supporting_signals:
            supporting_list = [
                s.strip()
                for s in cand.supporting_signals.split(";")
                if s.strip()
            ]
        if cand.disqualifying_signals:
            disqualifying_list = [
                s.strip()
                for s in cand.disqualifying_signals.split(";")
                if s.strip()
            ]

        # Add explicit explanation signals based on scores if not already present
        if cand.semantic_score is not None and cand.semantic_score > 0.0:
            if not any("semantic" in s.lower() for s in supporting_list):
                supporting_list.append(
                    f"semantic similarity ({cand.semantic_score:.2f})"
                )
        if cand.fuzzy_score is not None and cand.fuzzy_score > 0.0:
            if not any(
                "fuzzy" in s.lower() or "text similarity" in s.lower()
                for s in supporting_list
            ):
                supporting_list.append(
                    f"activity text similarity ({cand.fuzzy_score:.2f})"
                )
        if cand.location_score is not None and cand.location_score > 0.0:
            if not any("location" in s.lower() for s in supporting_list):
                supporting_list.append("location agreement")
        if cand.discipline_score is not None and cand.discipline_score > 0.0:
            if not any("discipline" in s.lower() for s in supporting_list):
                supporting_list.append("discipline agreement")

        cand_dict = cand.model_dump()
        cand_dict["rank_order"] = idx
        cand_dict["supporting_signals"] = (
            "; ".join(supporting_list) if supporting_list else None
        )
        cand_dict["disqualifying_signals"] = (
            "; ".join(disqualifying_list) if disqualifying_list else None
        )

        ranked.append(CandidateMatch(**cand_dict))

    return ranked


def evaluate_hard_mismatch(
    claim: Union[ExecutionClaim, dict],
    activity: Union[ScheduleActivity, dict],
    base_confidence: Optional[float] = None,
) -> Optional[CandidateMatch]:
    """
    Tier 4: HARD_MISMATCH evaluation helper.
    Evaluates whether a claim and activity pair exhibit a clear disqualifying conflict
    (e.g., conflicting discipline or location when both are specified).
    If a hard mismatch exists, returns a CandidateMatch with match_tier = HARD_MISMATCH,
    confidence capped at <= 0.40, and detailed disqualifying_signals.
    If missing metadata alone or no conflict, returns None.
    """
    if isinstance(claim, ExecutionClaim):
        event_id = claim.event_id
        claim_schedule_id = claim.schedule_id
        claim_disc = claim.discipline
        claim_loc = claim.location
        claim_asset = claim.asset_tag
    elif isinstance(claim, dict):
        event_id = claim.get("event_id", "evt_unknown")
        claim_schedule_id = claim.get("schedule_id")
        claim_disc = claim.get("discipline")
        claim_loc = claim.get("location")
        claim_asset = claim.get("asset_tag")
    else:
        return None

    if isinstance(activity, ScheduleActivity):
        act_id = activity.activity_id
        act_schedule_id = activity.schedule_id
        act_disc = activity.discipline
        act_loc = activity.location
        act_asset = activity.asset_tag
    elif isinstance(activity, dict):
        act_id = activity.get("activity_id")
        act_schedule_id = activity.get("schedule_id")
        act_disc = activity.get("discipline")
        act_loc = activity.get("location")
        act_asset = activity.get("asset_tag")
    else:
        return None

    if not act_id:
        return None

    disqualifying: List[str] = []

    # Check discipline conflict if both present
    if claim_disc and act_disc:
        c_disc_clean = str(claim_disc).strip().lower()
        a_disc_clean = str(act_disc).strip().lower()
        if c_disc_clean and a_disc_clean and c_disc_clean != a_disc_clean:
            disqualifying.append(
                f"discipline mismatch: claim={claim_disc}, activity={act_disc}"
            )

    # Check location conflict if both present
    if claim_loc and act_loc:
        c_loc_clean = str(claim_loc).strip().lower()
        a_loc_clean = str(act_loc).strip().lower()
        if c_loc_clean and a_loc_clean and c_loc_clean != a_loc_clean:
            disqualifying.append(
                f"location mismatch: claim={claim_loc}, activity={act_loc}"
            )

    # Check asset conflict if both present
    if claim_asset and act_asset:
        c_asset_clean = str(claim_asset).strip().lower()
        a_asset_clean = str(act_asset).strip().lower()
        if c_asset_clean and a_asset_clean and c_asset_clean != a_asset_clean:
            disqualifying.append(
                f"asset mismatch: claim={claim_asset}, activity={act_asset}"
            )

    if not disqualifying:
        # No hard conflict found (compatible metadata or missing metadata alone)
        return None

    # Calculate confidence: capped at <= 0.40
    if base_confidence is not None:
        confidence = min(0.40, max(0.0, float(base_confidence)))
    else:
        confidence = 0.20

    return CandidateMatch(
        candidate_id=f"cand_{event_id}_{act_id}",
        event_id=event_id,
        schedule_id=act_schedule_id or claim_schedule_id or "SCH_UNKNOWN",
        activity_id=act_id,  # Unchanged source string
        rank_order=3,
        match_tier=HARD_MISMATCH,
        composite_confidence=confidence,
        disqualifying_signals="; ".join(disqualifying),
    )


def _text_cited_activity(claim, schedule_activities) -> Optional[CandidateMatch]:
    """
    Deterministic fallback for a claim whose extraction produced no activity id (LLM unavailable / report phrased freely):
    if the report text itself cites exactly one schedule activity id, that is an exact-ID match (0.97, not 1.0, because
    the id was found by scanning rather than extracted). Several different ids in one snippet is ambiguous: no match here.
    """
    text = claim.raw_claim_text if isinstance(claim, ExecutionClaim) else (claim.get("raw_claim_text") if isinstance(claim, dict) else None)
    reported = claim.reported_activity_id if isinstance(claim, ExecutionClaim) else (claim.get("reported_activity_id") if isinstance(claim, dict) else None)
    if reported or not text:
        return None
    cited = find_activity_ids_in_text(text, [_get_act_dict(a) for a in schedule_activities])
    if len(cited) != 1:
        return None
    probe = claim.model_copy(update={"reported_activity_id": cited[0]}) if isinstance(claim, ExecutionClaim) else {**claim, "reported_activity_id": cited[0]}
    cand = match_exact_id(probe, schedule_activities)
    if cand is None:
        return None
    return cand.model_copy(update={"composite_confidence": 0.97, "supporting_signals": "Activity ID found in the report text"})


def match_claim(
    claim: Union[ExecutionClaim, dict],
    schedule_activities: List[Union[ScheduleActivity, dict]],
    semantic_results: Optional[List[Union[dict, object]]] = None,
    batch_context: Optional[BatchContext] = None,
) -> List[CandidateMatch]:
    """
    M3 4-Tier Matching Cascade:
    1. EXACT_ID (extracted id, else an id cited verbatim in the report text)
    2. EXACT_ASSET
    3. HYBRID_FALLBACK (+ explainable stage/date/quantity/WBS signals and the upload-batch stage prior)
    4. HARD_MISMATCH
    Returns a ranked list of up to 3 CandidateMatch objects.
    """
    # Tier 1: EXACT_ID
    exact_cand = match_exact_id(claim, schedule_activities) or _text_cited_activity(claim, schedule_activities)
    if exact_cand:
        return [exact_cand]

    # Tier 2: EXACT_ASSET
    asset_cands = match_exact_asset(claim, schedule_activities)
    if asset_cands:
        ranked_asset = rank_and_explain_candidates(asset_cands)
        if ranked_asset and ranked_asset[0].composite_confidence > 0.40:
            return ranked_asset

    # Tier 3: HYBRID_FALLBACK (uses semantic_results if present, else fallback mode with semantic_score = 0.0)
    hybrid_cands = generate_hybrid_candidates(
        claim, schedule_activities, semantic_results, batch_context=batch_context
    )
    if hybrid_cands:
        ranked_hybrid = rank_and_explain_candidates(hybrid_cands)
        if ranked_hybrid and ranked_hybrid[0].composite_confidence > 0.0:
            return ranked_hybrid

    # Tier 4: HARD_MISMATCH evaluation (evaluated only for eligible activities)
    claim_project_id = getattr(claim, "project_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("project_id") if isinstance(claim, dict) else None)
    claim_schedule_id = claim.schedule_id if isinstance(claim, ExecutionClaim) else claim.get("schedule_id")
    claim_stage_id = getattr(claim, "stage_id", None) if isinstance(claim, ExecutionClaim) else (claim.get("stage_id") if isinstance(claim, dict) else None)
    is_rework = bool(getattr(claim, "is_rework", False) if isinstance(claim, ExecutionClaim) else (claim.get("is_rework") or claim.get("reopened_from_actual_id") or claim.get("event_type") == "REWORK"))

    mismatch_cands: List[CandidateMatch] = []
    for act in schedule_activities:
        act_dict = _get_act_dict(act)
        eligibility = MatchingEligibilityService.get_activity_eligibility(
            act_dict,
            expected_project_id=claim_project_id,
            expected_schedule_id=claim_schedule_id,
            expected_stage_id=claim_stage_id,
            is_rework=is_rework,
        )
        if not eligibility.eligible:
            continue
        mismatch_cand = evaluate_hard_mismatch(claim, act)
        if mismatch_cand:
            mismatch_cands.append(mismatch_cand)

    if mismatch_cands:
        return rank_and_explain_candidates(mismatch_cands)

    if hybrid_cands:
        return rank_and_explain_candidates(hybrid_cands)

    return []

# ---------------------------------------------------------------------------
# Feature 30 -- WBS Granularity Bridge (M3 half). Decision logic lives in
# backend/shared/wbs_split.py; this section loads inputs and persists rows to
# claim_activity_splits. A claim has EITHER matched_activity_id OR split rows.
# ---------------------------------------------------------------------------

def _activity_dicts(schedule_activities: List[ScheduleActivity]) -> List[dict]:
    return [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in schedule_activities]


def _load_split_inputs(cur, schedule_id: str, member_ids: List[str]) -> Tuple[Dict[str, dict], List[dict]]:
    """Approved actuals and intra-group dependencies for the sibling activities."""
    cur.execute(
        """
        SELECT activity_id, actual_start, actual_finish, actual_pct_complete, actual_quantity
        FROM approved_actuals
        WHERE schedule_id = %s AND activity_id = ANY(%s)
        """,
        (schedule_id, member_ids),
    )
    actuals = {r["activity_id"]: dict(r) for r in cur.fetchall()}
    cur.execute(
        """
        SELECT predecessor_activity_id, successor_activity_id, relationship_type, lag_days
        FROM schedule_dependencies
        WHERE schedule_id = %s
          AND predecessor_activity_id = ANY(%s) AND successor_activity_id = ANY(%s)
        """,
        (schedule_id, member_ids, member_ids),
    )
    return actuals, [dict(r) for r in cur.fetchall()]


def decide_wbs_split(
    cur,
    claim: ExecutionClaim,
    candidates: List[CandidateMatch],
    schedule_activities: List[ScheduleActivity],
    is_ambiguous: bool,
) -> Optional[dict]:
    """
    Inspect the top candidate's WBS group and, for a broad claim, allocate it
    across the eligible siblings. Returns None when the normal single-activity
    match applies, otherwise {"group", "scope", "alloc"} (alloc may have
    status FAILED, in which case the caller keeps the normal match).
    """
    if not candidates or candidates[0].match_tier == HARD_MISMATCH:
        return None
    activities = _activity_dicts(schedule_activities)
    group = find_wbs_group(candidates[0].activity_id, activities)
    if not group:
        return None
    member_ids = [m["activity_id"] for m in group["members"]]
    ambiguous_in_group = bool(
        is_ambiguous and len(candidates) >= 2 and candidates[1].activity_id in member_ids
    )
    claim_dict = claim.model_dump()
    scope = classify_claim_scope(
        claim_dict,
        group,
        {"activity_id": candidates[0].activity_id, "match_tier": candidates[0].match_tier},
        ambiguous_in_group,
    )
    if scope["claim_scope"] != "BROAD_WBS":
        return {"group": group, "scope": scope, "alloc": None}
    actuals, deps = _load_split_inputs(cur, claim.schedule_id, member_ids)
    alloc = allocate_split(
        claim_dict,
        group["members"],
        actuals,
        deps,
        claim_date=claim.event_date,
        summary_activity_id=group["summary_activity_id"],
    )
    return {"group": group, "scope": scope, "alloc": alloc}


def replace_claim_splits(cur, event_id: str, schedule_id: str, rows: List[dict]) -> None:
    """Idempotently replace a claim's split rows (call inside the caller's transaction)."""
    cur.execute("DELETE FROM claim_activity_splits WHERE event_id = %s", (event_id,))
    for r in rows:
        cur.execute(
            """
            INSERT INTO claim_activity_splits (
                split_id, event_id, activity_id, split_basis, split_pct, schedule_id,
                wbs_code, planned_quantity, allocated_quantity, uom, rationale
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"split_{event_id}_{r['activity_id']}",
                event_id,
                r["activity_id"],
                r["split_basis"],
                r["split_pct"],
                schedule_id,
                r.get("wbs_code"),
                r.get("planned_quantity"),
                r.get("allocated_quantity"),
                r.get("uom"),
                r.get("rationale"),
            ),
        )


def read_claim_splits(cur, event_id: str) -> List[dict]:
    """Split rows for a claim, with the per-child contribution for its claim mode."""
    cur.execute(
        "SELECT claim_mode, claimed_pct, claimed_quantity FROM execution_events WHERE event_id = %s",
        (event_id,),
    )
    ev = cur.fetchone() or {}
    cur.execute(
        """
        SELECT split_id, event_id, schedule_id, activity_id, wbs_code, split_basis, split_pct,
               planned_quantity, allocated_quantity, uom, rationale, created_at
        FROM claim_activity_splits
        WHERE event_id = %s
        ORDER BY activity_id ASC
        """,
        (event_id,),
    )
    rows = []
    for r in cur.fetchall():
        row = dict(r)
        pct_mode = (ev.get("claim_mode") or "CUMULATIVE_PCT") == "CUMULATIVE_PCT"
        if pct_mode:
            row["allocated_pct"] = (
                round(float(ev["claimed_pct"]) * float(row["split_pct"]), 4)
                if ev.get("claimed_pct") is not None else None
            )
            row["allocated_quantity"] = None
        elif ev.get("claimed_quantity") is not None:
            row["allocated_quantity"] = round(float(ev["claimed_quantity"]) * float(row["split_pct"]), 4)
        rows.append(row)
    return rows


@router.post("/{event_id}/match")
def match_claim_endpoint(
    event_id: str,
    action: str = "MATCH_CLAIM",
    event_context: EventContext = Depends(gates.event_process),
):
    """
    POST /api/v1/claims/{event_id}/match
    Loads execution claim and schedule activities from DB, executes M3 matching cascade,
    persists candidate_matches and updates execution_events status/matched_activity_id,
    and returns matching summary JSON.
    """
    return run_claim_match(event_id, action, event_context)


def run_claim_match(
    event_id: str,
    action: str,
    event_context: EventContext,
    batch_context: Optional[BatchContext] = None,
):
    """The matching pipeline behind POST /claims/{id}/match, callable by the batch intake with an upload-batch context."""
    if get_connection is None:
        raise HTTPException(
            status_code=500, detail="Database connection module unavailable."
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Load execution claim
            cur.execute(
                "SELECT * FROM execution_events WHERE event_id = %s",
                (event_id,),
            )
            event_row = cur.fetchone()
            if not event_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution claim '{event_id}' not found.",
                )

            if event_row.get("clarification_status") == "PENDING":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot match claim while clarification is pending. Engineer must submit clarification first.",
                )

            claim_schedule_id = event_row.get("schedule_id")
            if not claim_schedule_id or not str(claim_schedule_id).strip():
                raise HTTPException(
                    status_code=400,
                    detail="INVALID_SCHEDULE_CONTEXT: Execution event has no explicit schedule_id",
                )

            claim = ExecutionClaim(**event_row)

            # 2. Load schedule activities with approved actuals to capture canonical execution state
            cur.execute(
                with_workflow_flags("""
                SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                       sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                       sa.asset_tag, sa.planned_start, sa.planned_finish,
                       sa.planned_quantity, sa.uom, sa.baseline_pct_complete,
                       sa.weight_factor, sa.quality_gate_required,
                       sa.contractor_id, sa.work_package_id,
                       sa.description, st.stage_name,
                       aa.actual_pct_complete, aa.actual_start, aa.actual_finish,
                       aa.is_reopened,
                       (
                           SELECT ee.reopen_status FROM execution_events ee
                           WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                           ORDER BY ee.event_date DESC LIMIT 1
                       ) AS reopen_status
                FROM schedule_activities sa
                LEFT JOIN approved_actuals aa
                       ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
                LEFT JOIN stages st ON st.stage_id = sa.stage_id
                WHERE sa.schedule_id = %s AND sa.project_id = %s
                ORDER BY sa.activity_id ASC
                """),
                (claim.schedule_id, str(event_context.project_id)),
            )
            act_rows = cur.fetchall()
            schedule_activities = [
                dict(r) if isinstance(r, dict) or hasattr(r, "keys") else (r.model_dump() if hasattr(r, "model_dump") else r)
                for r in act_rows
            ]

            # Stage-Aware scoping: if event has stage_id, prioritize or restrict to that stage
            event_stage_id = event_row.get("stage_id")
            if event_stage_id:
                stage_pool = [
                    a for a in schedule_activities
                    if a.get("stage_id") is not None and str(a.get("stage_id")) == str(event_stage_id)
                ]
                candidate_pool = stage_pool if stage_pool else schedule_activities
            else:
                candidate_pool = schedule_activities

            # Phase 6/7: Pre-filter candidate pool for eligibility (supporting authorized rework)
            is_rework = bool(event_row.get("reopened_from_actual_id") or event_row.get("event_type") == "REWORK")
            eligible_activities, audit_explanations = MatchingEligibilityService.filter_eligible_activities(
                candidate_pool,
                expected_project_id=event_row.get("project_id"),
                expected_schedule_id=claim.schedule_id,
                expected_stage_id=event_stage_id,
                is_rework=is_rework,
            )

            # 3. Run M3 matching cascade with optional M1 FAISS semantic retrieval.
            semantic_results = None

            if claim.raw_claim_text and schedule_index is not None:
                try:
                    if schedule_index.get_active_schedule_id() != claim.schedule_id:
                        try:
                            schedule_index.build_index(claim.schedule_id)
                        except Exception as build_err:
                            print(
                                f"[M3] FAISS auto-build index warning "
                                f"for {claim.schedule_id}: {build_err}"
                            )

                    semantic_results = schedule_index.search_schedule(
                        claim.schedule_id,
                        claim.raw_claim_text,
                    )

                except Exception as search_err:
                    print(f"[M3] FAISS search warning: {search_err}")
                    semantic_results = None

            # 4. Run M3 matching cascade strictly on eligible candidates.
            candidates = match_claim(
                claim,
                eligible_activities,
                semantic_results=semantic_results,
                batch_context=batch_context,
            )

            # 4. Determine matching status and matched_activity_id with ambiguity protection
            unmatched_reason = None
            is_ambiguous = False
            ambiguity_reason = None

            if (
                candidates
                and candidates[0].match_tier == HYBRID_FALLBACK
                and len(candidates) >= 2
            ):
                diff = (
                    candidates[0].composite_confidence
                    - candidates[1].composite_confidence
                )
                if diff < 0.05:
                    is_ambiguous = True
                    ambiguity_reason = (
                        f"Ambiguous match: confidence difference between rank-1 ({candidates[0].activity_id}) "
                        f"and rank-2 ({candidates[1].activity_id}) is {diff:.4f} < 0.05"
                    )

            if (
                candidates
                and candidates[0].match_tier
                in [EXACT_ID, EXACT_ASSET, HYBRID_FALLBACK]
                and candidates[0].composite_confidence > 0.40
                and not is_ambiguous
            ):
                status = "MATCHED"
                matched_activity_id = candidates[0].activity_id
                top_tier = candidates[0].match_tier
                top_confidence = candidates[0].composite_confidence
            else:
                status = "UNMATCHED"
                # A completed activity is NEVER eligible, so matched_activity_id is None when 0 eligible candidates
                matched_activity_id = candidates[0].activity_id if candidates else None
                top_tier = (
                    candidates[0].match_tier if candidates else HARD_MISMATCH
                )
                top_confidence = (
                    candidates[0].composite_confidence if candidates else 0.0
                )
                if is_ambiguous:
                    unmatched_reason = ambiguity_reason
                elif candidates and candidates[0].match_tier == HARD_MISMATCH:
                    unmatched_reason = (
                        candidates[0].disqualifying_signals
                        or "Hard metadata mismatch"
                    )
                elif candidates:
                    unmatched_reason = f"Low confidence match ({candidates[0].composite_confidence:.2f})"
                else:
                    # Deterministic explainability for empty candidate set (Phase 6)
                    reported_id = claim.reported_activity_id
                    if reported_id and reported_id in audit_explanations:
                        exp = audit_explanations[reported_id]
                        unmatched_reason = f"NO_ELIGIBLE_CANDIDATE: Reported activity '{reported_id}' is {exp.reason}"
                    elif event_stage_id and candidate_pool and not eligible_activities:
                        unmatched_reason = "NO_ELIGIBLE_CANDIDATE: All activities in stage are COMPLETED"
                    elif candidate_pool and not eligible_activities:
                        unmatched_reason = "NO_ELIGIBLE_CANDIDATE: All activities in schedule are COMPLETED"
                    else:
                        unmatched_reason = "NO_ELIGIBLE_CANDIDATE: No matching schedule activities found"

            # 5. Feature 30: broad claims are decomposed across WBS siblings instead of
            #    forcing one leaf. Normal match and split are mutually exclusive (XOR).
            split_info = decide_wbs_split(cur, claim, candidates, schedule_activities, is_ambiguous)
            alloc = split_info["alloc"] if split_info else None
            is_split = bool(alloc and alloc["status"] == "SUCCESS")
            split_rows: List[dict] = alloc["splits"] if is_split else []
            if is_split:
                status = "MATCHED"
                matched_activity_id = None
                unmatched_reason = None
                top_tier = candidates[0].match_tier
                top_confidence = candidates[0].composite_confidence

            # 6. Persist up to 3 ranked candidates and update execution_event safely
            cur.execute(
                "DELETE FROM candidate_matches WHERE event_id = %s",
                (event_id,),
            )

            top_3 = candidates[:3]
            for cand in top_3:
                cur.execute(
                    """
                    INSERT INTO candidate_matches (
                        candidate_id, event_id, schedule_id, activity_id,
                        rank_order, match_tier, composite_confidence,
                        semantic_score, fuzzy_score, location_score,
                        discipline_score, supporting_signals, disqualifying_signals
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        cand.candidate_id,
                        cand.event_id,
                        cand.schedule_id,
                        cand.activity_id,
                        cand.rank_order,
                        cand.match_tier,
                        cand.composite_confidence,
                        cand.semantic_score,
                        cand.fuzzy_score,
                        cand.location_score,
                        cand.discipline_score,
                        cand.supporting_signals,
                        cand.disqualifying_signals,
                    ),
                )

            replace_claim_splits(cur, event_id, claim.schedule_id, split_rows)

            cur.execute(
                """
                UPDATE execution_events
                SET status = %s, matched_activity_id = %s
                WHERE event_id = %s
                """,
                (status, matched_activity_id, event_id),
            )

            # V7 attribution: a single matched activity fixes the event's stage / contractor / work package
            # (derived from the schedule, never from claim text).
            if matched_activity_id and status == "MATCHED":
                matched_row = next((a for a in schedule_activities if a.get("activity_id") == matched_activity_id), None)
                if matched_row is not None:
                    cur.execute(
                        "UPDATE execution_events SET stage_id = %s, contractor_id = %s, work_package_id = %s WHERE event_id = %s",
                        (matched_row.get("stage_id"), matched_row.get("contractor_id"), matched_row.get("work_package_id"), event_id),
                    )

            # Feature 33: an activity_id resolved by schedule matching is SCHEDULE_AUTO_FILLED;
            # a split/unmatched claim has no single resolved activity, so drop a stale tag.
            if matched_activity_id and status == "MATCHED":
                cur.execute(
                    """
                    UPDATE execution_events
                    SET field_provenance = COALESCE(field_provenance, '{}'::jsonb)
                                           || '{"activity_id": "SCHEDULE_AUTO_FILLED"}'::jsonb
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )
            else:
                cur.execute(
                    """
                    UPDATE execution_events
                    SET field_provenance = COALESCE(field_provenance, '{}'::jsonb) - 'activity_id'
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )

            conn.commit()

            # 7. Audit log integration
            if write_audit_log is not None:
                try:
                    write_audit_log(
                        entity_type="execution_event",
                        entity_id=event_id,
                        action=action,
                        actor_id=str(event_context.project_context.user.id),
                        project_id=event_context.project_id,
                        schedule_id=claim.schedule_id,
                        role=event_context.role,
                        before_state={"status": event_row.get("status")},
                        after_state={
                            "status": status,
                            "matched_activity_id": matched_activity_id,
                            "split_activity_ids": [r["activity_id"] for r in split_rows],
                        },
                        payload={
                            "performed_by": "SYSTEM:M3 (deterministic matching cascade)",
                            "match_tier": top_tier,
                            "composite_confidence": top_confidence,
                            "unmatched_reason": unmatched_reason,
                            "claim_scope": split_info["scope"]["claim_scope"] if split_info else "SPECIFIC",
                        },
                    )
                except Exception:
                    logger.exception("match audit log failed for %s", event_id)

            res = {
                "event_id": event_id,
                "matched_activity_id": matched_activity_id,
                "match_tier": top_tier,
                "composite_confidence": top_confidence,
                "candidates": [c.model_dump() for c in top_3],
                "status": status,
                "claim_scope": "BROAD_WBS" if is_split else "SPECIFIC",
                "scope_reason": (
                    split_info["scope"]["scope_reason"] if split_info
                    else "Activity has no WBS group with 2+ members"
                ),
                "wbs_group": (
                    {"wbs_code": split_info["group"]["wbs_code"],
                     "members": [m["activity_id"] for m in split_info["group"]["members"]]}
                    if split_info else None
                ),
                "splits": split_rows,
                "allocation_status": alloc["status"] if alloc else "SKIPPED",
                "allocation_reason": alloc["reason"] if alloc else "Claim scope is not BROAD_WBS",
                "excluded_siblings": alloc["excluded"] if alloc else [],
            }
            if unmatched_reason:
                res["unmatched_reason"] = unmatched_reason

            return res


@router.post("/{event_id}/rematch")
def rematch_claim_endpoint(
    event_id: str,
    event_context: EventContext = Depends(gates.event_process),
):
    """
    POST /api/v1/claims/{event_id}/rematch
    Re-runs the M3 4-tier matching cascade for an existing execution claim.
    Updates candidate_matches and status/matched_activity_id accordingly.
    """
    return match_claim_endpoint(
        event_id, action="REMATCH_CLAIM", event_context=event_context
    )


@router.get("/{event_id}/splits")
def get_claim_splits_endpoint(
    event_id: str,
    _event_context: EventContext = Depends(gates.event_view),
):
    """
    GET /api/v1/claims/{event_id}/splits
    The claim's WBS split rows (empty list for a normal single-activity match).
    Read-only: does not recalculate allocations.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM execution_events WHERE event_id = %s", (event_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Execution claim '{event_id}' not found.")
            splits = read_claim_splits(cur, event_id)
    return {
        "event_id": event_id,
        "splits": splits,
        "split_count": len(splits),
        "total_split_pct": round(sum(float(s["split_pct"]) for s in splits), 4),
    }


class SplitUpdateRequest(BaseModel):
    activity_id: str
    split_pct: float  # FRACTION in [0, 1]; 0 drops the row


class PatchSplitsRequest(BaseModel):
    splits: List[SplitUpdateRequest]


@router.patch("/{event_id}/splits")
def patch_claim_splits_endpoint(
    event_id: str,
    payload: PatchSplitsRequest,
    event_context: EventContext = Depends(gates.event_review),
):
    """
    PATCH /api/v1/claims/{event_id}/splits  (Supervisor only)
    Edit split_pct fractions. Rows whose value actually changed become MANUAL.
    The result must sum to 1.0000 +/- 0.0001 (never auto-repaired); on any
    validation failure nothing is persisted. Re-run /check afterwards so per-child
    validation and priority reflect the edit.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT schedule_id FROM execution_events WHERE event_id = %s", (event_id,))
            ev = cur.fetchone()
            if not ev:
                raise HTTPException(status_code=404, detail=f"Execution claim '{event_id}' not found.")
            before = read_claim_splits(cur, event_id)
            try:
                edited = apply_manual_split_edit(
                    before, [{"activity_id": u.activity_id, "split_pct": u.split_pct} for u in payload.splits]
                )
            except SplitEditError as e:
                raise HTTPException(status_code=400, detail=str(e))
            replace_claim_splits(cur, event_id, ev["schedule_id"], edited)
            conn.commit()
            after = read_claim_splits(cur, event_id)

    if write_audit_log is not None:
        try:
            write_audit_log(
                entity_type="claim_splits",
                entity_id=event_id,
                action="SPLIT_EDIT",
                actor_id=str(event_context.project_context.user.id),
                project_id=event_context.project_id,
                schedule_id=ev["schedule_id"],
                role=event_context.role,
                before_state={r["activity_id"]: r["split_pct"] for r in before},
                after_state={r["activity_id"]: r["split_pct"] for r in after},
                payload={"bases": {r["activity_id"]: r["split_basis"] for r in after}},
            )
        except Exception:
            logger.exception("split edit audit log failed for %s", event_id)

    return {
        "event_id": event_id,
        "splits": after,
        "split_count": len(after),
        "total_split_pct": round(sum(float(s["split_pct"]) for s in after), 4),
        "message": f"Updated {len(after)} split rows",
    }
