"""Semantic retrieval for v2: the SAME model (all-MiniLM-L6-v2, CPU), the SAME searchable-text builder, the SAME metric (cosine via inner product on
normalised vectors) and the SAME top-k as backend/shared/schedule_index.py -- only the keying differs. The legacy index holds one schedule in a process;
here each (project, schedule version) has its own in-memory index, so one project's activities can never be returned for another project's claim.

Only ACTIVE versions are cached: an active version is immutable (database trigger), so a cached index can never go stale. Nothing is persisted."""
from __future__ import annotations

import logging
import os
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


def backend() -> str:
    from . import embedder
    return embedder.selected()


def warm_up() -> None:
    """load the embedding model once, in the background at start-up, so the first claim of the day is not the slow one (failures are logged loudly and matching
    degrades without semantics -- it never switches to the other backend)"""
    try:
        from . import embedder
        if embedder.selected() == "torch":
            _prefer_cached_model()
        embedder.encode(["warm up"])
    except Exception as e:                                                   # noqa: BLE001
        logger.error("embedding model warm-up failed (backend=%s); matching will run without the semantic signal: %s", os.environ.get("V2_EMBEDDING_BACKEND", "torch"), e)


def search(project_id, version_id, activities: List[Dict[str, Any]], query: str, top_k: Optional[int] = None):
    """-> list of legacy SearchCandidate (schedule_id = version id, activity_id = external id, score). Raises on any failure; the caller decides to degrade."""
    from . import embedder
    use_onnx = embedder.selected() == "onnx"
    if not use_onnx:
        _prefer_cached_model()
    from backend.shared import schedule_index as si                          # tolerant of missing torch/faiss; provides the shared text builder and the result type
    import numpy as np
    if not query or not query.strip() or not activities:
        return []
    if not use_onnx:
        import faiss
    key = (str(project_id), str(version_id))
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        texts = [si.build_searchable_text(SimpleNamespace(activity_id=a["activity_id"], activity_name=a["activity_name"], wbs_code=a["wbs_code"],
                                                          discipline=a["discipline"], location=a["location"], asset_tag=a["asset_tag"])) for a in activities]
        emb = embedder.encode(texts, si.EMBEDDING_BATCH_SIZE)
        if use_onnx:
            index = emb
        else:
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
    total = index.shape[0] if use_onnx else index.ntotal
    k = min(top_k or si.DEFAULT_TOP_K, total)
    q = embedder.encode([query])
    if use_onnx:
        pairs = embedder.top_k(index, q[0], k)
    else:
        scores, idx = index.search(q, k)
        pairs = [(int(i), float(s)) for s, i in zip(scores[0], idx[0]) if i != -1]
    out = [si.SearchCandidate(schedule_id=str(version_id), activity_id=ids[i], score=s) for i, s in pairs]
    out.sort(key=lambda c: c.score, reverse=True)
    return out
