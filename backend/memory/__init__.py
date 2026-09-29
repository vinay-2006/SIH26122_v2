"""
Institutional Memory Retrieval Infrastructure for SETUAI V7 Phase 11.
Provides generic, domain-neutral retrieval, metadata filtering, semantic retrieval abstraction,
deterministic ranking, and fallback retrieval.
"""

from backend.memory.schemas import (
    MemoryFilter,
    MemoryMatchReason,
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    MemoryRetrievalResponse,
    ScoreBreakdown,
)
from backend.memory.interfaces import MemoryProvider, SemanticEncoder
from backend.memory.retrieval_service import (
    InstitutionalMemoryRetrievalService,
    search_by_activity,
    search_by_contractor,
    search_by_stage,
    search_institutional_memory,
    search_similar,
)

__all__ = [
    "MemoryRecord",
    "MemoryFilter",
    "MemoryQuery",
    "MemoryResult",
    "MemoryRetrievalResponse",
    "MemoryMatchReason",
    "ScoreBreakdown",
    "MemoryProvider",
    "SemanticEncoder",
    "InstitutionalMemoryRetrievalService",
    "search_institutional_memory",
    "search_by_activity",
    "search_by_stage",
    "search_by_contractor",
    "search_similar",
]
