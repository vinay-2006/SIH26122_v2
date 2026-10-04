"""M1 semantic schedule retrieval: a single active, in-memory FAISS index.

Pipeline (per SIH26122_Full_Build_Context_And_Task_Division, "Member 1 —
Schedule Ingestion & Indexing" — unchanged from the earlier detailed
version, carried forward by the later delta document):

    PostgreSQL (schedule_activities, source of truth)
        -> searchable text per activity
        -> sentence-transformers embedding (all-MiniLM-L6-v2)
        -> FAISS IndexFlatIP, held in memory
        -> semantic candidate retrieval (schedule_id, activity_id, score)
        -> consumed by M3's matching cascade (not implemented here)

FAISS is an index, not a second database: it stores vectors plus a minimal
activity_id mapping, never the canonical activity fields. Every result this
module returns must be resolved back to `schedule_activities` (via
backend.shared.schedule_repository) for the actual record. This module
never applies matching/business rules (EXACT_ID, asset-tag, tiering, etc.)
— that is M3's job. It only answers "which activities in the active
schedule read as semantically similar to this query", nothing more.

Single active schedule — deliberate, per spec, not an oversight:
there is exactly one live FAISS index at a time, in memory only, for
whichever schedule was most recently indexed. Uploading a new schedule
replaces it entirely:

    build_index(A) -> active index represents A
    build_index(B) -> active index now represents B; A's activities are no
                       longer reachable through search_schedule(), though
                       A's rows remain untouched in PostgreSQL

This is intentional for a one-project prototype (see the project docs'
own roadmap note: a real multi-project version would key a dict of indexes
by schedule_id instead — not built here). There is therefore no on-disk
persistence for this index: process restart loses the active index the
same way it would in the original design, and the fix is the same one the
spec describes — call build_index() again for whichever schedule should be
active, straight from PostgreSQL.

Metric: cosine similarity, implemented as inner product (FAISS
IndexFlatIP) over L2-normalized embeddings — both index-time and query-time
vectors go through the same model with normalize_embeddings=True, so the
metric is consistent in both directions.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

# Imported here, before faiss, and NOT removed as "unused" -- on macOS,
# importing faiss before torch (which sentence-transformers loads lazily in
# _get_model() below) segfaults the whole process on the first real
# embedding call (confirmed by reproducing it directly: faiss-first crashes,
# torch-first doesn't). Loading bare torch here (no model weights, cheap)
# forces the correct init order regardless of which of this module's
# functions a caller happens to hit first.
#
# torch and faiss are OPTIONAL at import time: the slim serverless deployment (V2_EMBEDDING_BACKEND=onnx) does not ship them and never uses this module's own
# index. Where they are installed nothing changes. The functions of this module that need them raise a clear error instead of an ImportError at start-up.
try:
    import torch  # noqa: F401
    import faiss
except ImportError:                                                   # pragma: no cover - exercised by the slim-bundle test
    torch = None
    faiss = None
import numpy as np

from backend.shared.schedule_repository import list_schedule_activities
from backend.shared.schemas import ScheduleActivity

logger = logging.getLogger(__name__)

# Model and text construction are fixed by the project spec, not chosen
# here for convenience:
#   text = "{activity_id} {activity_name} {wbs_code} {discipline} {location} {asset_tag}"
#   embeddings = sentence-transformers("all-MiniLM-L6-v2")
#   index = FAISS IndexFlatIP
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_BATCH_SIZE = 64

# Not specified for M1 retrieval itself, but grounded in the PRD's own
# downstream convention ("Top 3 candidates shown" in the matching pipeline)
# rather than picked arbitrarily.
DEFAULT_TOP_K = 3

_model = None  # lazy singleton: SentenceTransformer load is several seconds, do it once per process


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        # Pinned to CPU: left to auto-detect, this loads onto Apple Silicon's
        # Metal (MPS) backend, which has crashed this process outright with
        # a driver-level assertion failure ("IOGPUMetalCommandBuffer
        # setCurrentCommandEncoder") during real embedding calls -- taking
        # down the whole backend (and with it, unrelated things like login,
        # which round-trips through this same process). all-MiniLM-L6-v2 is
        # a small model; CPU inference is fast enough here that there's no
        # real tradeoff for reliability.
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
    return _model


class ScheduleIndexError(Exception):
    """Base class for schedule-index errors."""


class NoActiveIndexError(ScheduleIndexError):
    """No schedule has been indexed yet in this process."""

    def __init__(self) -> None:
        super().__init__(
            "no active FAISS index in this process; call build_index(schedule_id) first"
        )


class ScheduleNotActiveError(ScheduleIndexError):
    """schedule_id exists in PostgreSQL but is not the currently active/indexed schedule.

    Mirrors the project spec's matching-layer contract: once a newer
    schedule replaces the active index, an older schedule_id has no valid
    index to search. Old events are never automatically rematched against
    a newer schedule.
    """

    def __init__(self, schedule_id: str, active_schedule_id: str) -> None:
        self.schedule_id = schedule_id
        self.active_schedule_id = active_schedule_id
        super().__init__(
            f"schedule_id {schedule_id!r} is not the active schedule "
            f"(active schedule is {active_schedule_id!r}); it has no searchable index"
        )


@dataclass
class SearchCandidate:
    """One semantic candidate. Resolve activity_id via PostgreSQL for full details."""

    schedule_id: str
    activity_id: str
    score: float


@dataclass
class IndexBuildResult:
    schedule_id: str
    activity_count: int


def build_searchable_text(activity: ScheduleActivity) -> str:
    """Deterministic searchable text for one activity.

    Field selection and order are fixed by the project spec: activity_id,
    activity_name, wbs_code, discipline, location, asset_tag. A field that is
    None (wbs_code or asset_tag may be None) is omitted rather than fabricated
    or rendered as the literal word "None".
    """
    parts = [
        activity.activity_id,
        activity.activity_name,
        activity.wbs_code,
        activity.discipline,
        activity.location,
        activity.asset_tag,
    ]
    return " ".join(part for part in parts if part)


# --- single active in-memory index -----------------------------------------
# Guarded by a lock so a build (write) and a search (read) from different
# requests can't interleave and observe a half-swapped state.
_state_lock = threading.Lock()
_active_schedule_id: Optional[str] = None
_active_index: Optional[faiss.Index] = None
_active_activity_ids: Optional[list[str]] = None


def get_active_schedule_id() -> Optional[str]:
    """The schedule_id currently represented by the active index, or None if none yet."""
    with _state_lock:
        return _active_schedule_id


def build_index(schedule_id: str) -> IndexBuildResult:
    """Build this schedule's FAISS index from PostgreSQL and make it the active index.

    Always reads schedule_activities fresh from PostgreSQL — PostgreSQL is
    the source of truth. The index is built fully in local variables first
    and only swapped into the active (module-level) state once it succeeds,
    so a failed build never leaves the active index half-replaced or torn
    down: whichever schedule was active before this call stays active until
    a build actually completes.

    Raises:
        ScheduleIndexError: the schedule has no activities in PostgreSQL
            (either schedule_id does not exist, or — not reachable via the
            current Phase 2 repository, which never persists a
            zero-activity schedule — it has none).
    """
    if faiss is None:
        raise ScheduleIndexError("faiss is not installed in this deployment; the legacy single-schedule index is unavailable")
    activities = list_schedule_activities(schedule_id)
    if not activities:
        raise ScheduleIndexError(
            f"cannot build an index for schedule_id {schedule_id!r}: "
            "no activities found in PostgreSQL"
        )

    texts = [build_searchable_text(activity) for activity in activities]
    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=EMBEDDING_BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    activity_ids = [activity.activity_id for activity in activities]

    global _active_schedule_id, _active_index, _active_activity_ids
    with _state_lock:
        _active_schedule_id = schedule_id
        _active_index = index
        _active_activity_ids = activity_ids

    logger.info(
        "active FAISS index replaced: schedule_id=%s (%d activities)",
        schedule_id, len(activities),
    )

    return IndexBuildResult(schedule_id=schedule_id, activity_count=len(activities))


def search_schedule(
    schedule_id: str, query: str, top_k: int = DEFAULT_TOP_K
) -> list[SearchCandidate]:
    """Semantic candidate retrieval against the active index, scoped to schedule_id.

    Returns up to top_k candidates as (schedule_id, activity_id, score),
    sorted by descending similarity. schedule_id must match whichever
    schedule is currently active — this is what makes cross-schedule
    contamination structurally impossible: there is only ever one index in
    memory, and a request naming any other schedule_id is rejected rather
    than silently searched against the wrong schedule's vectors.

    This is semantic retrieval only — no business-rule matching (exact-ID,
    asset-tag, tiering, etc.) is applied here; that belongs to M3.

    Raises:
        NoActiveIndexError: no schedule has been indexed yet in this process.
        ScheduleNotActiveError: schedule_id is not the currently active schedule.
        ValueError: top_k is not positive, or query is blank.
    """
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if not query or not query.strip():
        raise ValueError("query must not be blank")

    with _state_lock:
        active_schedule_id = _active_schedule_id
        index = _active_index
        activity_ids = _active_activity_ids

    if active_schedule_id is None or index is None or activity_ids is None:
        raise NoActiveIndexError()

    if schedule_id != active_schedule_id:
        raise ScheduleNotActiveError(schedule_id, active_schedule_id)

    if index.ntotal == 0:
        return []

    effective_k = min(top_k, index.ntotal)

    model = _get_model()
    query_vector = model.encode(
        [query],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32)

    scores, indices = index.search(query_vector, effective_k)

    candidates = [
        SearchCandidate(schedule_id=schedule_id, activity_id=activity_ids[idx], score=float(score))
        for score, idx in zip(scores[0], indices[0])
        if idx != -1
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def clear_active_index() -> None:
    """Drop the active index (test/process-reset utility only).

    Not part of the upload pipeline — a real process simply never calls
    this and keeps whichever schedule was indexed last.
    """
    global _active_schedule_id, _active_index, _active_activity_ids
    with _state_lock:
        _active_schedule_id = None
        _active_index = None
        _active_activity_ids = None
