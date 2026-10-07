"""
Additional, explainable matching signals for schedule matching (SETUAI V7 prototype intake).

The original cascade (EXACT_ID -> EXACT_ASSET -> HYBRID 0.50 semantic + 0.25 fuzzy + 0.15 location + 0.10 discipline ->
HARD_MISMATCH) stays exactly as it was. This module adds, on top of it, only signals that use fields the schedule really
carries (activity id / WBS code, stage name, planned dates, planned quantity + unit, description) plus the batch the
claim arrived in. Every signal is a pure function, returns a score or None ("not applicable": the claim or the activity
does not carry the field, so it neither helps nor hurts), and every adjustment is reported as a human-readable reason,
so a supervisor can see why a candidate ranks where it does.

    adjustment = stage name (+0.08 max) + planned-window date (+0.05 / -0.10) + quantity & unit (+0.05 / -0.05)
                 + WBS code in the report (+0.10) + batch stage prior (+0.07 max)

The adjustment never lifts a candidate that has a discipline or location conflict past the existing 0.40 cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

STAGE_NAME_MAX = 0.08
DATE_IN_WINDOW = 0.05
DATE_FAR_PENALTY = -0.10
QUANTITY_AGREE = 0.05
QUANTITY_IMPOSSIBLE = -0.05
WBS_IN_TEXT = 0.10
BATCH_STAGE_MAX = 0.07
WEAK_TEXT_MAX_PENALTY = -0.15   # location/discipline agreement alone must not confirm a match
WEAK_TEXT_BELOW = 0.45

# a progress report may legitimately precede the baseline start slightly (mobilisation) or follow its finish (slippage)
EARLY_GRACE_DAYS = 14
LATE_GRACE_DAYS = 45
FAR_EARLY_DAYS = 60
FAR_LATE_DAYS = 180

_STOP = {"the", "and", "of", "for", "at", "in", "on", "to", "a", "an", "works", "work", "area", "unit"}
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: Optional[str]) -> List[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def _as_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------------- location
def location_token_score(claim_loc: Optional[str], act_loc: Optional[str]) -> float:
    """
    Graded location agreement in [0, 1] from token overlap. Digit-bearing tokens (station 3, unit 5, chainage 212) are
    identifiers: if both locations carry them and they differ, the locations are different places (score 0), so
    "Pump Station 3" never matches "Pump Station 4".
    """
    a, b = set(_tokens(claim_loc)), set(_tokens(act_loc))
    if not a or not b:
        return 0.0
    da, db = {t for t in a if any(c.isdigit() for c in t)}, {t for t in b if any(c.isdigit() for c in t)}
    if da and db and not (da & db):
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    jaccard = len(inter) / len(a | b)
    containment = len(inter) / min(len(a), len(b))
    return round(min(1.0, 0.5 * jaccard + 0.5 * containment), 4)


# ------------------------------------------------------------------------------------------------ ids in text
def _id_pattern(token: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def find_activity_ids_in_text(text: Optional[str], activities: Iterable[Dict[str, Any]]) -> List[str]:
    """Activity ids that appear verbatim (whole token, case-insensitive) in the report text; longest ids first."""
    if not text:
        return []
    found = []
    for a in activities:
        aid = a.get("activity_id")
        if aid and len(str(aid)) >= 4 and _id_pattern(str(aid)).search(text):
            found.append(str(aid))
    return sorted(set(found), key=lambda s: (-len(s), s))


def wbs_in_text(text: Optional[str], wbs_code: Optional[str]) -> bool:
    """A WBS code (e.g. 4.05) is only trusted when written as WBS / W.B.S / 'item' context or as a table cell of its own."""
    if not text or not wbs_code:
        return False
    code = re.escape(str(wbs_code).strip())
    return bool(re.search(rf"(?:\bwbs\b[\s:#-]*|\bitem\b[\s:#-]*|\|\s*){code}(?![\d.])", text, re.IGNORECASE))


# ------------------------------------------------------------------------------------------------------ stage
def stage_name_score(text: Optional[str], stage_name: Optional[str]) -> Optional[float]:
    """Share of the stage name's distinctive words that the report text mentions (None when there is no stage name)."""
    st = set(_tokens(stage_name))
    if not st:
        return None
    present = set(_tokens(text))
    return round(len(st & present) / len(st), 4)


# ------------------------------------------------------------------------------------------------------- date
def date_signal(event_date: Any, planned_start: Any, planned_finish: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    (delta, reason): +DATE_IN_WINDOW when the report date falls in the activity's planned window (with grace),
    DATE_FAR_PENALTY when it is implausibly far outside it, 0 in between, None when a date is missing.
    """
    ev, ps, pf = _as_date(event_date), _as_date(planned_start), _as_date(planned_finish)
    if ev is None or ps is None or pf is None:
        return None, None
    early = (ps - ev).days      # > 0: report is before the planned start
    late = (ev - pf).days       # > 0: report is after the planned finish
    if early > FAR_EARLY_DAYS:
        return DATE_FAR_PENALTY, f"report date is {early} days before the activity's planned start"
    if late > FAR_LATE_DAYS:
        return DATE_FAR_PENALTY, f"report date is {late} days after the activity's planned finish"
    if early <= EARLY_GRACE_DAYS and late <= LATE_GRACE_DAYS:
        return DATE_IN_WINDOW, "report date falls within the planned window"
    return 0.0, None


# ------------------------------------------------------------------------------------------------- quantity
def _norm_uom(u: Optional[str]) -> str:
    u = re.sub(r"[^a-z0-9]", "", (u or "").lower())
    return {"meter": "m", "metre": "m", "meters": "m", "metres": "m", "cum": "m3", "tonnes": "t", "tonne": "t", "tons": "t",
            "ton": "t", "kilometer": "km", "kilometre": "km", "kilometers": "km", "kilometres": "km", "nos": "ea"}.get(u, u.rstrip("s"))


def quantity_signal(claimed_qty: Optional[float], claimed_uom: Optional[str], planned_qty: Optional[float],
                    planned_uom: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """+QUANTITY_AGREE when the unit matches and the claimed quantity fits the planned quantity; a penalty when it cannot."""
    if claimed_qty is None or not claimed_uom or not planned_uom:
        return None, None
    if _norm_uom(claimed_uom) != _norm_uom(planned_uom):
        return None, None  # different units say nothing about this activity either way
    if planned_qty and planned_qty > 0 and claimed_qty > planned_qty * 1.5:
        return QUANTITY_IMPOSSIBLE, f"claimed {claimed_qty:g} {claimed_uom} exceeds the planned {planned_qty:g}"
    return QUANTITY_AGREE, f"unit '{planned_uom}' matches the activity's planned unit"


# ------------------------------------------------------------------------------------------------ batch context
@dataclass
class BatchContext:
    """
    What the other claims of the same upload batch already established. Reports are written about one area at a time, so
    a vague line ("backfill 60 percent") in a file whose other lines clearly belong to one stage is more likely to belong
    to that stage too. Votes come only from claims matched with high confidence; they only ever add a small prior.
    """

    stage_votes: Dict[str, float] = field(default_factory=dict)
    activity_votes: Dict[str, float] = field(default_factory=dict)

    def vote(self, stage_id: Optional[str], activity_id: Optional[str], weight: float = 1.0) -> None:
        if stage_id:
            self.stage_votes[str(stage_id)] = self.stage_votes.get(str(stage_id), 0.0) + weight
        if activity_id:
            self.activity_votes[activity_id] = self.activity_votes.get(activity_id, 0.0) + weight

    def stage_prior(self, stage_id: Optional[str]) -> float:
        total = sum(self.stage_votes.values())
        if not total or not stage_id:
            return 0.0
        return self.stage_votes.get(str(stage_id), 0.0) / total

    def __bool__(self) -> bool:
        return bool(self.stage_votes)


# ------------------------------------------------------------------------------------- distinctive-word coverage
def _stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def _stems(text: Optional[str]) -> List[str]:
    return [_stem(t) for t in _tokens(text)]


def build_idf(texts: Iterable[str]) -> Dict[str, float]:
    """Inverse document frequency of each word over the activity texts of one schedule (common words weigh little)."""
    import math

    docs = [set(_stems(t)) for t in texts]
    n = max(len(docs), 1)
    df: Dict[str, int] = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    return {w: math.log(1.0 + n / c) for w, c in df.items()}


def distinctive_coverage(claim_text: Optional[str], activity_text: Optional[str], idf: Dict[str, float]) -> float:
    """
    Share of the activity's idf-weighted words that the claim text contains, in [0, 1]. "installation" or "and" shared by
    half the schedule contributes almost nothing; "ladders" or "radiography" shared by one activity contributes a lot.
    """
    act = set(_stems(activity_text))
    if not act:
        return 0.0
    have = set(_stems(claim_text))
    total = sum(idf.get(w, 1.0) for w in act)
    return round(sum(idf.get(w, 1.0) for w in act & have) / total, 4) if total else 0.0


MIN_POOL_FOR_IDF = 8          # fewer activities than this is no corpus: leave the legacy text score untouched
GENERIC_OVERLAP_FLOOR = 0.20  # a text score is capped at FLOOR + (1 - FLOOR) * distinctive coverage


def capped_text_score(fuzzy: float, coverage: float) -> float:
    return round(min(fuzzy, GENERIC_OVERLAP_FLOOR + (1.0 - GENERIC_OVERLAP_FLOOR) * coverage), 6)


# ------------------------------------------------------------------------------------------------- adjustment
def extension_adjustment(
    *,
    claim_text: Optional[str],
    claim_date: Any,
    claim_qty: Optional[float],
    claim_uom: Optional[str],
    activity: Dict[str, Any],
    stage_name: Optional[str],
    batch: Optional[BatchContext] = None,
    text_score: Optional[float] = None,
) -> Tuple[float, List[str], List[str]]:
    """Total additive adjustment plus the supporting / disqualifying reasons that explain it."""
    delta = 0.0
    supporting: List[str] = []
    disqualifying: List[str] = []

    # Everything in one unit shares a location and often a discipline, so those agreements alone say little. When the
    # schedule data is rich enough to judge (dates / stage / description present) and the text barely resembles the
    # activity, confidence is reduced in proportion, and the reason is shown.
    rich = bool(activity.get("planned_start") or stage_name or activity.get("description"))
    if rich and text_score is not None and text_score < WEAK_TEXT_BELOW:
        d = round(WEAK_TEXT_MAX_PENALTY * (WEAK_TEXT_BELOW - text_score) / WEAK_TEXT_BELOW, 4)
        if d < -0.005:
            delta += d
            disqualifying.append(f"weak text evidence (text similarity {text_score:.2f}) ({d:+.2f})")

    s = stage_name_score(claim_text, stage_name)
    if s is not None and s >= 0.5:
        d = round(STAGE_NAME_MAX * s, 4)
        delta += d
        supporting.append(f"stage name mentioned ({stage_name}) (+{d:.2f})")

    d, why = date_signal(claim_date, activity.get("planned_start"), activity.get("planned_finish"))
    if d is not None and d != 0.0:
        delta += d
        (supporting if d > 0 else disqualifying).append(f"{why} ({d:+.2f})")

    d, why = quantity_signal(claim_qty, claim_uom, activity.get("planned_quantity"), activity.get("uom"))
    if d is not None:
        delta += d
        (supporting if d > 0 else disqualifying).append(f"{why} ({d:+.2f})")

    if wbs_in_text(claim_text, activity.get("wbs_code")):
        delta += WBS_IN_TEXT
        supporting.append(f"WBS code {activity.get('wbs_code')} cited in the report (+{WBS_IN_TEXT:.2f})")

    if batch:
        prior = batch.stage_prior(str(activity.get("stage_id")) if activity.get("stage_id") else None)
        if prior > 0.0:
            d = round(BATCH_STAGE_MAX * prior, 4)
            delta += d
            supporting.append(f"other claims in this upload point to the same stage ({prior:.0%}) (+{d:.2f})")

    return round(delta, 4), supporting, disqualifying


def claim_fingerprint(*, project_id: Any, schedule_id: str, activity_id: str, event_date: Any, claim_mode: Optional[str],
                      claimed_pct: Optional[float], claimed_quantity: Optional[float], claimed_uom: Optional[str],
                      event_type: Optional[str]) -> str:
    """
    Identity of a claim independent of which file reported it: the same activity, date, kind of claim and value is the same
    claim however many reports repeat it. A different value for the same activity is a different (conflicting) claim and
    is left for the conflict checks to flag.
    """
    import hashlib

    value = f"pct:{round(float(claimed_pct), 2)}" if claimed_pct is not None else (
        f"qty:{round(float(claimed_quantity), 3)}:{_norm_uom(claimed_uom)}" if claimed_quantity is not None else "none")
    raw = "|".join([str(project_id), schedule_id, activity_id, str(_as_date(event_date)), str(claim_mode or ""), value, str(event_type or "")])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
