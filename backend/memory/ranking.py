"""
Deterministic explainable ranking engine for Institutional Memory Retrieval in SETUAI V7 Phase 11.
Provides transparent scoring, metadata bonus accumulation, recency decay, and deterministic tie-breaking.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

from backend.memory.interfaces import SemanticEncoder
from backend.memory.schemas import (
    MemoryFilter,
    MemoryMatchReason,
    MemoryRecord,
    MemoryResult,
    ScoreBreakdown,
)

# ============================================================================
# CONFIGURABLE RANKING COEFFICIENTS
# ============================================================================
# Weights sum to a transparent, explainable scale where base relevance and metadata
# signals combine predictably.
WEIGHT_SEMANTIC: float = 0.50
WEIGHT_LEXICAL_FALLBACK: float = 0.50

BONUS_ACTIVITY_MATCH: float = 0.25
BONUS_STAGE_MATCH: float = 0.15
BONUS_CONTRACTOR_MATCH: float = 0.10
BONUS_INCIDENT_TYPE_MATCH: float = 0.10
BONUS_RECENCY_MAX: float = 0.05
RECENCY_HALF_LIFE_DAYS: float = 180.0

# Stopwords for deterministic token normalization in lexical fallback
COMMON_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "of", "from", "as", "is", "was", "are", "were", "it", "this", "that"
}


def tokenize(text: str) -> List[str]:
    """Extract lowercase alphanumeric tokens, filtering single chars and stopwords."""
    if not text:
        return []
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
    return [t for t in raw_tokens if t not in COMMON_STOPWORDS]


def compute_lexical_similarity(query_tokens: List[str], text: str) -> float:
    """
    Deterministic lexical token overlap score in range [0.0, 1.0].
    Combines Jaccard token overlap with token containment ratio.
    """
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokenize(text))
    if not doc_tokens:
        return 0.0

    q_set = set(query_tokens)
    intersection = q_set.intersection(doc_tokens)
    if not intersection:
        return 0.0

    jaccard = len(intersection) / len(q_set.union(doc_tokens))
    containment = len(intersection) / len(q_set)
    return round(min(1.0, 0.4 * jaccard + 0.6 * containment), 4)


def compute_recency_bonus(created_at: Optional[datetime], now: Optional[datetime] = None) -> float:
    """
    Computes an exponential-decay recency bonus in range [0.0, BONUS_RECENCY_MAX].
    Records created today receive full BONUS_RECENCY_MAX.
    """
    if created_at is None:
        return 0.0

    if now is None:
        now = datetime.now(timezone.utc) if created_at.tzinfo else datetime.utcnow()

    delta = now - created_at
    days_old = max(0.0, delta.total_seconds() / 86400.0)

    # Exponential decay: e^(-ln(2) * days / half_life)
    decay = math.exp(-0.693147 * (days_old / RECENCY_HALF_LIFE_DAYS))
    return round(BONUS_RECENCY_MAX * decay, 4)


def rank_candidates(
    candidates: List[MemoryRecord],
    query_text: str,
    filters: Optional[MemoryFilter] = None,
    encoder: Optional[SemanticEncoder] = None,
    top_k: int = 10,
) -> Tuple[List[MemoryResult], str]:
    """
    Deterministically scores and ranks candidate memory records.

    Pipeline:
    1. If encoder is available and query_text is non-empty:
       Encodes query and computes vector cosine similarity.
       Mode = 'metadata+semantic'
    2. Else if query_text is non-empty:
       Computes deterministic lexical token similarity fallback.
       Mode = 'metadata+lexical_fallback'
    3. Else (pure metadata filtering without query text):
       Mode = 'metadata_only'
    4. Evaluates metadata match bonuses (activity, stage, contractor, incident_type, recency).
    5. Assembles explainable ScoreBreakdown and match_reasons.
    6. Sorts by total_score DESC, with deterministic tie-breaking on memory_id ASC.
    7. Returns top_k items along with the retrieval_mode string.
    """
    if not candidates:
        mode = "metadata+semantic" if (encoder and encoder.is_available() and query_text.strip()) else (
            "metadata+lexical_fallback" if query_text.strip() else "metadata_only"
        )
        return [], mode

    query_clean = query_text.strip()
    query_tokens = tokenize(query_clean) if query_clean else []

    # Determine mode and compute query embedding if possible
    query_embedding: Optional[List[float]] = None
    retrieval_mode = "metadata_only"

    if query_clean:
        if encoder and encoder.is_available():
            try:
                query_embedding = encoder.encode_text(query_clean)
                if query_embedding:
                    retrieval_mode = "metadata+semantic"
            except Exception:
                query_embedding = None

        if not query_embedding:
            retrieval_mode = "metadata+lexical_fallback"

    results: List[MemoryResult] = []

    for rec in candidates:
        semantic_score = 0.0
        lexical_score = 0.0
        reasons: List[str] = []

        # 1. Similarity Scoring
        combined_text = f"{rec.title} {rec.summary or ''} {rec.content}"

        if query_embedding and rec.embedding_reference and encoder:
            sim = encoder.compute_similarity(query_embedding, rec.embedding_reference)
            semantic_score = round(max(0.0, min(1.0, sim)), 4)
            if semantic_score > 0.15:
                reasons.append(MemoryMatchReason.SEMANTIC_SIMILARITY)
        elif query_tokens:
            lex = compute_lexical_similarity(query_tokens, combined_text)
            lexical_score = lex
            if lexical_score > 0.05:
                reasons.append(MemoryMatchReason.LEXICAL_TOKEN_MATCH)

        # 2. Metadata Match Bonuses
        act_bonus = 0.0
        if filters and filters.activity_id and rec.activity_id == filters.activity_id:
            act_bonus = BONUS_ACTIVITY_MATCH
            reasons.append(MemoryMatchReason.EXACT_ACTIVITY)

        stage_bonus = 0.0
        if filters and filters.stage_id and rec.stage_id == filters.stage_id:
            stage_bonus = BONUS_STAGE_MATCH
            reasons.append(MemoryMatchReason.EXACT_STAGE)

        contractor_bonus = 0.0
        if filters and filters.contractor_id and rec.contractor_id == filters.contractor_id:
            contractor_bonus = BONUS_CONTRACTOR_MATCH
            reasons.append(MemoryMatchReason.EXACT_CONTRACTOR)

        type_bonus = 0.0
        if filters and filters.incident_type and rec.incident_type:
            if rec.incident_type.upper() == filters.incident_type.upper():
                type_bonus = BONUS_INCIDENT_TYPE_MATCH
                reasons.append(MemoryMatchReason.EXACT_INCIDENT_TYPE)

        # 3. Recency Bonus
        recency_bonus = compute_recency_bonus(rec.created_at)
        if recency_bonus > 0.01:
            reasons.append(MemoryMatchReason.RECENCY_BONUS)

        # 4. Total Score Calculation
        if retrieval_mode == "metadata+semantic":
            base_score = WEIGHT_SEMANTIC * semantic_score
        elif retrieval_mode == "metadata+lexical_fallback":
            base_score = WEIGHT_LEXICAL_FALLBACK * lexical_score
        else:
            base_score = 0.0

        total_score = round(
            base_score
            + act_bonus
            + stage_bonus
            + contractor_bonus
            + type_bonus
            + recency_bonus,
            4,
        )

        breakdown = ScoreBreakdown(
            semantic_score=semantic_score,
            lexical_score=lexical_score,
            activity_bonus=act_bonus,
            stage_bonus=stage_bonus,
            contractor_bonus=contractor_bonus,
            recency_bonus=recency_bonus,
            total_score=total_score,
        )

        results.append(
            MemoryResult(
                record=rec,
                score=total_score,
                match_reasons=reasons,
                score_breakdown=breakdown,
            )
        )

    # 5. Deterministic Ordering: total_score DESC, tie-break memory_id ASC
    results.sort(key=lambda r: (-r.score, r.record.memory_id))

    return results[:top_k], retrieval_mode
