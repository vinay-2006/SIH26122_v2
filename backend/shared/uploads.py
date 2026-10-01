"""
Shared storage for uploaded evidence / report files (issue evidence, batch intake).

Files are stored under UPLOAD_DIR (same location the claim photo endpoint serves from) with a content-hash based name,
so the same bytes are stored once. The file name supplied by the client is never used as a path component.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Tuple

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (Path(__file__).resolve().parents[1] / "uploads"))

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(filename: str) -> str:
    base = os.path.basename(filename or "upload").strip() or "upload"
    return _SAFE.sub("_", base)[:120]


def save_upload(contents: bytes, filename: str) -> Tuple[str, str]:
    """Persist bytes; returns (stored_path, sha256). Raises ValueError for empty or oversized content."""
    if not contents:
        raise ValueError("empty file")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    digest = hashlib.sha256(contents).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{digest[:16]}_{safe_name(filename)}"
    if not path.exists():
        path.write_bytes(contents)
    return str(path), digest
