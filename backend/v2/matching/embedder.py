"""Which engine turns text into the MiniLM vectors the semantic retrieval uses. The MODEL is always all-MiniLM-L6-v2; only how it is executed differs.

  V2_EMBEDDING_BACKEND=torch   (default) sentence-transformers on CPU + FAISS IndexFlatIP -- the original path, unchanged
  V2_EMBEDDING_BACKEND=onnx    the same network exported to ONNX, run by onnxruntime, with the same tokenizer, truncation (256), mean pooling and L2 normalisation;
                               retrieval is an exact NumPy inner product (what IndexFlatIP computes). Needs no torch / faiss / sentence-transformers.
                               V2_ONNX_MODEL_DIR (default: backend/v2/matching/onnx_model) must hold model.onnx and tokenizer.json (scripts/export_minilm_onnx.py writes them).

The choice is explicit and never silent: an unknown value, a missing dependency or a missing model file raises EmbeddingBackendError. It is NOT answered by
falling back to the other backend (the caller degrades to matching without the semantic signal, exactly as it already does when the model cannot load)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, List, Sequence

logger = logging.getLogger(__name__)
DEFAULT_ONNX_DIR = Path(__file__).resolve().parent / "onnx_model"       # where a deployment bundle carries model.onnx + tokenizer.json (model.onnx is not committed)
MAX_SEQ_LENGTH = 256                                    # sentence_bert_config.json of all-MiniLM-L6-v2
BACKENDS = ("torch", "onnx")


class EmbeddingBackendError(RuntimeError):
    """the selected embedding backend cannot run (reported, never replaced by another backend)"""


def selected() -> str:
    name = (os.environ.get("V2_EMBEDDING_BACKEND") or "torch").strip().lower()
    if name not in BACKENDS:
        raise EmbeddingBackendError(f"V2_EMBEDDING_BACKEND={name!r} is not one of {', '.join(BACKENDS)}")
    return name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_files(model_dir: Path) -> dict:
    """integrity: model.onnx and tokenizer.json must be byte-for-byte what MANIFEST.json (written by the export, committed with the code) says; fails closed.
    ~0.2 s for the 91 MB model, once per process."""
    mf = model_dir / "MANIFEST.json"
    if not mf.is_file():
        raise EmbeddingBackendError(f"ONNX backend selected but {mf} is missing: an unverified model is never loaded")
    manifest = json.loads(mf.read_text())
    for name, want in manifest["files"].items():
        got = _sha256(model_dir / name)
        if got != want["sha256"]:
            raise EmbeddingBackendError(f"integrity check failed for {name}: sha256 {got[:12]}… does not match the manifest ({want['sha256'][:12]}…)")
    return manifest


class OnnxEmbedder:
    """all-MiniLM-L6-v2 through onnxruntime: tokenize (truncate to 256, pad to the longest in the batch) -> network -> attention-masked mean -> L2 normalise"""

    def __init__(self, model_dir: str | os.PathLike):
        d = Path(model_dir)
        model, tok = d / "model.onnx", d / "tokenizer.json"
        for f in (model, tok):
            if not f.is_file():
                raise EmbeddingBackendError(f"ONNX backend selected but {f} is missing (run scripts/export_minilm_onnx.py, or set V2_ONNX_MODEL_DIR)")
        verify_files(d)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            raise EmbeddingBackendError(f"ONNX backend selected but a dependency is not installed: {e}") from e
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("V2_ONNX_THREADS", "2"))
        self._sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(str(tok))
        self._tok.enable_truncation(max_length=MAX_SEQ_LENGTH)
        self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
        self._lock = threading.Lock()

    def encode(self, texts: Sequence[str], batch_size: int = 64) -> Any:
        import numpy as np
        out: List[Any] = []
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))          # same trick as sentence-transformers: similar lengths share a batch
        for s in range(0, len(order), batch_size):
            idx = order[s:s + batch_size]
            enc = self._tok.encode_batch([texts[i] for i in idx])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            with self._lock:
                hidden = self._sess.run(None, {k: v for k, v in feed.items() if k in self._inputs})[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            out.append((idx, pooled.astype(np.float32)))
        res = np.zeros((len(texts), 384), dtype=np.float32)
        for idx, vecs in out:
            res[idx] = vecs
        return res


_onnx: OnnxEmbedder | None = None
_onnx_lock = threading.Lock()


def onnx_embedder() -> OnnxEmbedder:
    global _onnx
    with _onnx_lock:
        if _onnx is None:
            _onnx = OnnxEmbedder(os.environ.get("V2_ONNX_MODEL_DIR") or DEFAULT_ONNX_DIR)
        return _onnx


def encode(texts: Sequence[str], batch_size: int = 64):
    """-> float32 matrix (n, 384) of L2-normalised vectors, by the selected backend"""
    if selected() == "onnx":
        return onnx_embedder().encode(texts, batch_size)
    from backend.shared import schedule_index as si                 # torch is imported before faiss there (macOS)
    import numpy as np
    return si._get_model().encode(list(texts), batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False).astype(np.float32)


def top_k(matrix, query_vec, k: int):
    """exact inner-product top-k (what faiss.IndexFlatIP returns): -> [(row, score)] best first, ties by row order"""
    import numpy as np
    scores = matrix @ query_vec.reshape(-1)
    order = np.argsort(-scores, kind="stable")[:k]
    return [(int(i), float(scores[i])) for i in order]
