"""Semantic retrieval for v2: the SAME model (all-MiniLM-L6-v2, CPU), the SAME searchable-text builder, the SAME metric (cosine via inner product on
normalised vectors) and the SAME top-k as backend/shared/schedule_index.py -- only the keying differs. The legacy index holds one schedule in a process;
here each (project, schedule version) has its own in-memory index, so one project's activities can never be returned for another project's claim.

Only ACTIVE versions are cached: an active version is immutable (database trigger), so a cached index can never go stale. Nothing is persisted."""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_MAX_INDEXES = 8
_lock = threading.Lock()
_cache: "OrderedDict[Tuple[str, str], Tuple[Any, List[str]]]" = OrderedDict()


def _prefer_cached_model() -> None:
    """The embedding model is small and normally already in the local Hugging Face cache. When it is, never go to the network for it: the library would otherwise
    check the hub on every load, which stalls (and warns) on machines that are offline. If the model is NOT cached, the default behaviour (download) is untouched."""
    import os
    from pathlib import Path
    home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    if (home / "hub" / "models--sentence-transformers--all-MiniLM-L6-v2").exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _legacy():
    # torch must be imported before faiss (macOS segfault documented in the legacy module); importing schedule_index first guarantees the order.
    _prefer_cached_model()
    from backend.shared import schedule_index as si
    return si


def warm_up() -> None:
    """load the embedding model once, in the background at start-up, so the first claim of the day is not the slow one (failures are ignored: matching degrades without semantics)"""
    try:
        si = _legacy()
        si._get_model().encode(["warm up"], normalize_embeddings=True, show_progress_bar=False)
    except Exception as e:                                                   # noqa: BLE001
        logger.warning("embedding model warm-up skipped: %s", e)


def search(project_id, version_id, activities: List[Dict[str, Any]], query: str, top_k: Optional[int] = None):
    """-> list of legacy SearchCandidate (schedule_id = version id, activity_id = external id, score). Raises on any failure; the caller decides to degrade."""
    si = _legacy()
    import numpy as np
    import faiss
    if not query or not query.strip() or not activities:
        return []
    key = (str(project_id), str(version_id))
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        texts = [si.build_searchable_text(SimpleNamespace(activity_id=a["activity_id"], activity_name=a["activity_name"], wbs_code=a["wbs_code"],
                                                          discipline=a["discipline"], location=a["location"], asset_tag=a["asset_tag"])) for a in activities]
        emb = si._get_model().encode(texts, batch_size=si.EMBEDDING_BATCH_SIZE, normalize_embeddings=True, convert_to_numpy=True,
                                     show_progress_bar=False).astype(np.float32)
        index = faiss.IndexFlatIP(emb.shape[1])
        index.add(emb)
        hit = (index, [a["activity_id"] for a in activities])
        with _lock:
            _cache[key] = hit
            while len(_cache) > _MAX_INDEXES:
                _cache.popitem(last=False)
    index, ids = hit
    if len(ids) != len(activities):                       # defensive: an index must describe exactly the activities it is asked about
        raise RuntimeError("semantic index does not match the schedule version")
    k = min(top_k or si.DEFAULT_TOP_K, index.ntotal)
    q = si._get_model().encode([query], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
    scores, idx = index.search(q, k)
    out = [si.SearchCandidate(schedule_id=str(version_id), activity_id=ids[i], score=float(s)) for s, i in zip(scores[0], idx[0]) if i != -1]
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def clear() -> None:
    with _lock:
        _cache.clear()
