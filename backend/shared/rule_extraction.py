"""
Deterministic, dependency-free claim extraction used ONLY as an opt-in fallback
(EXTRACTION_FALLBACK=rules) when the LLM provider is unavailable -- quota
exhausted, network down, no key. PRD v6 Section 21: the application shall retain a
deterministic fallback/demo path when external services are unavailable.

It is intentionally conservative: it fills only what plain patterns can support and
leaves everything else null, so Feature 29's clarification gate (missing event_type /
discipline / progress) still asks the Site Engineer instead of guessing. Every claim
still goes through matching, deterministic checks and Supervisor review.
"""
from __future__ import annotations

import contextvars
import re
from datetime import date
from typing import List, Optional, Tuple

from backend.shared.schemas import ExtractedClaimFields

# Per-request switch for the rules fallback (never mutates process-wide environment, so concurrent requests are safe).
_FORCE_RULES: contextvars.ContextVar[bool] = contextvars.ContextVar("force_rule_extraction", default=False)
_RULES_USED: contextvars.ContextVar[bool] = contextvars.ContextVar("rule_extraction_used", default=False)

_DISCIPLINE_KEYWORDS = [
    ("HSE", ("hse", "safety", "toolbox", "induction", "barricad", "audit", "confined space")),
    ("INSTRUMENTATION", ("instrument", "transmitter", "junction box", "flowmeter", "calibration", "detector")),
    ("ELECTRICAL", ("cable", "electrical", "switchgear", "earth pit", "lighting", "tray installation", "mcc")),
    ("STATIC_ROTATING_EQUIPMENT", ("pump", "generator", "compressor", "vessel", "skid", "tank", "grouting", "baseplate")),
    ("PIPING", ("piping", "pipe", "weld", "spool", "header", "fabricat", "valve", "hydro", "tie-in", "flange")),
    ("CIVIL", ("civil", "excavat", "trench", "concrete", "foundation", "rebar", "backfill", "formwork", "blinding")),
]
_UOMS = r"(km|kilomet(?:er|re)s?|m3|cu\.?\s?m|m|meters?|metres?|t|tons?|tonnes?|joints?|welds?|piles?|spools?|units?|sets?|stations?|ea|nos|pits?|poles?|panels?|bins?|checks?|days?)"
_ID_RE = re.compile(r"\b([A-Z]{2,5}(?:-[A-Z0-9]{1,6}){2,4})\b")
_DELAY_KEYWORDS = [
    ("WEATHER", ("rain", "monsoon", "flood", "storm", "weather")),
    ("MATERIAL", ("material", "delivery", "drum", "shortage", "supply")),
    ("EQUIPMENT", ("breakdown", "equipment failure", "crane", "machine")),
    ("LABOUR", ("labour", "labor", "manpower", "workers absent")),
    ("ACCESS", ("access", "permit", "blocked road", "restricted")),
    ("REWORK", ("rework", "redo", "re-do", "reset")),
]


def _language(text: str) -> str:
    if re.search(r"[ఀ-౿]", text):
        return "Telugu"
    if re.search(r"[ऀ-ॿ]", text):
        return "Hindi"
    return "English"


def extract_with_rules(text: str) -> ExtractedClaimFields:
    _RULES_USED.set(True)  # observable by track_rules(): the rules extractor really ran (whatever the reason)
    low = text.lower()
    data: dict = {"language_detected": _language(text), "action": text.strip()[:120] or None}

    scoring_text = re.sub(r"\b(pump|tank|generator|control room|waste storage)\s+(station|area|bay|yard)\b", " ", low)
    best, best_hits = None, 0
    for disc, words in _DISCIPLINE_KEYWORDS:  # highest keyword count wins; ties keep list order
        hits = sum(1 for w in words if w in scoring_text)
        if hits > best_hits:
            best, best_hits = disc, hits
    if best:
        data["discipline"] = best

    if re.search(r"\b(finish|finished|complete[d]?|done|handed over)\b", low) and (
        re.search(r"100\s*%", low) or not re.search(r"\d", low)
    ):
        data["event_type"] = "ACTUAL_FINISH"
    elif re.search(r"\b(start(ed)?|commenc\w+|mobili[sz]ed)\b", low) and not re.search(r"\d\s*%|\d\s*(m|t)\b", low):
        data["event_type"] = "ACTUAL_START"
    elif re.search(r"\b(delay(ed)?|behind|waiting)\b", low):
        data["event_type"] = "DELAY"
    elif re.search(r"\b(blocked|blocker|stopped|halted)\b", low):
        data["event_type"] = "BLOCKER"
    elif re.search(r"\d", low):
        data["event_type"] = "PROGRESS_UPDATE"

    pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", low)
    qty = re.search(rf"(\d+(?:\.\d+)?)\s*{_UOMS}\b", low)
    if pct and (float(pct.group(1)) <= 100):
        data["claim_mode"] = "CUMULATIVE_PCT"
        data["claimed_pct"] = float(pct.group(1))
    elif qty:
        data["claim_mode"] = "INCREMENTAL_QUANTITY"
        data["claimed_quantity"] = float(qty.group(1))
        uom = re.sub(r"\s", "", qty.group(2)).rstrip("s")
        data["claimed_uom"] = {"cum": "m3", "cu.m": "m3", "meter": "m", "metre": "m", "ton": "t", "tonne": "t", "kilometer": "km", "kilometre": "km"}.get(uom, uom)
    elif data.get("event_type") == "ACTUAL_FINISH":
        data["claim_mode"] = "CUMULATIVE_PCT"
        data["claimed_pct"] = 100.0

    m = _ID_RE.search(text)
    if m:
        data["reported_activity_id"] = m.group(1)
    loc = re.search(r"\b(?:at|in|near)\s+((?:[A-Z][\w-]*\s?){1,4}\d*)", text)
    if loc:
        data["location"] = loc.group(1).strip()
    for reason, words in _DELAY_KEYWORDS:
        if any(w in low for w in words):
            data["delay_reason"] = reason
            break
    return ExtractedClaimFields(**data)


def fallback_enabled() -> bool:
    import os
    return _FORCE_RULES.get() or os.environ.get("EXTRACTION_FALLBACK", "").strip().lower() == "rules"


class force_rules:
    """Context manager: use the deterministic rules extractor in this request/task whatever the environment says."""

    def __enter__(self):
        self._token = _FORCE_RULES.set(True)
        return self

    def __exit__(self, *exc):
        _FORCE_RULES.reset(self._token)
        return False


class track_rules:
    """Context manager: after the block, `.used` says whether the deterministic rules extractor actually produced claims."""

    used = False

    def __enter__(self):
        self._token = _RULES_USED.set(False)
        return self

    def __exit__(self, *exc):
        self.used = _RULES_USED.get()
        _RULES_USED.reset(self._token)
        return False


# ---- multi-claim rules extraction (one claim per reported item instead of one per document) -------------------
_DATE_ISO = re.compile(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b")
_DATE_DMY = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b")
_PROGRESS_HINT = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|percent|per cent))|\b(complete[d]?|finish(?:ed)?|started|commenc\w+|in progress|handed over)\b"
    r"|\b\d+(?:\.\d+)?\s*" + _UOMS + r"\b",
    re.IGNORECASE,
)


def find_document_date(text: str) -> Optional[date]:
    """First calendar date in the text (ISO or DD/MM/YYYY), used as the report date for claims that carry none."""
    m = _DATE_ISO.search(text)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DATE_DMY.search(text)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def split_report_segments(text: str, limit: int = 80) -> List[str]:
    """
    Candidate report items: lines (or table rows) that state progress, a start/finish, or a quantity. A pipe/tab/comma
    delimited row is kept whole so its activity id, name and value stay together.
    """
    segments: List[str] = []
    for raw in re.split(r"[\r\n]+", text):
        line = raw.strip(" \t-*•")
        if len(line) < 8 or len(line) > 600:
            continue
        if _PROGRESS_HINT.search(line) or _ID_RE.search(line):
            segments.append(line)
        if len(segments) >= limit:
            break
    return segments


def extract_with_rules_segments(text: str) -> List[Tuple[str, ExtractedClaimFields]]:
    """One (segment, claim) per reported item; falls back to a single whole-text claim when no item can be isolated."""
    doc_date = find_document_date(text)
    out: List[Tuple[str, ExtractedClaimFields]] = []
    for seg in split_report_segments(text):
        claim = extract_with_rules(seg)
        if claim.claimed_pct is None and claim.claimed_quantity is None and claim.event_type not in ("ACTUAL_START", "ACTUAL_FINISH", "DELAY", "BLOCKER"):
            continue  # a heading or an id mentioned in passing is not a claim
        if claim.event_date is None and doc_date is not None:
            claim = claim.model_copy(update={"event_date": doc_date})
        out.append((seg, claim))
    if not out:
        whole = extract_with_rules(text)
        if doc_date is not None and whole.event_date is None:
            whole = whole.model_copy(update={"event_date": doc_date})
        out.append((text, whole))
    return out


def extract_with_rules_batch(text: str) -> List[ExtractedClaimFields]:
    return [c for _, c in extract_with_rules_segments(text)]
