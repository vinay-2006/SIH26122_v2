"""Evidence storage behind an interface. The database keeps an OPAQUE key (source_documents.storage_path) plus the backend name; this
module is the only code that knows where bytes live. `LocalEvidenceStore` is the development adapter; a hosted object-storage adapter
implements the same three methods later and is selected by `source_documents.storage_backend`. No cloud storage is used in this phase.

Safety: file names on disk are generated (a random id plus an extension taken from the VALIDATED content type, never from the client);
keys are validated against a strict pattern and the resolved path must stay inside the store root (no traversal, no symlinks); files are
written 0600 through a temp file and an atomic rename; nothing here ever returns or logs an absolute path."""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Protocol

EXTENSIONS = ("pdf", "png", "jpg", "txt", "csv", "xlsx")
_KEY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{2}/[0-9a-f]{32}\.(?:%s)$" % "|".join(EXTENSIONS))


class StorageError(RuntimeError):
    pass


class EvidenceStore(Protocol):
    backend: str

    def put(self, project_id, content: bytes, ext: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalEvidenceStore:
    backend = "LOCAL"

    def __init__(self, root):
        root = Path(root).expanduser()
        if not root.is_absolute():
            raise StorageError("the evidence directory must be an absolute path")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not _KEY.match(key):
            raise StorageError("invalid storage key")
        p = (self._root / key)
        if p.is_symlink() or any(q.is_symlink() for q in p.parents if self._root in q.parents):
            raise StorageError("invalid storage key")
        r = p.resolve()
        if self._root not in r.parents:
            raise StorageError("invalid storage key")
        return r

    def put(self, project_id, content: bytes, ext: str) -> str:
        if ext not in EXTENSIONS:
            raise StorageError("unsupported extension")
        name = uuid.uuid4().hex
        key = f"{uuid.UUID(str(project_id))}/{name[:2]}/{name}.{ext}"
        dest = self._path(key)
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return key

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as e:
            raise StorageError("stored file is missing") from e

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


_store: Optional[EvidenceStore] = None


def default_root() -> Path:
    env = os.environ.get("V2_EVIDENCE_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / ".local" / "v2_evidence"


def get_store() -> EvidenceStore:
    global _store
    if _store is None:
        _store = LocalEvidenceStore(default_root())
    return _store


def set_store(store: Optional[EvidenceStore]) -> None:
    """tests / future adapters"""
    global _store
    _store = store
