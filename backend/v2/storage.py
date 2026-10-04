"""Evidence storage behind an interface. The database keeps an OPAQUE key (source_documents.storage_path) plus the backend name; this
module is the only code that knows where bytes live. `LocalEvidenceStore` is the development adapter; `SupabaseEvidenceStore` keeps the bytes in a PRIVATE
Supabase Storage bucket, reached only by the backend with the service-role key (never sent to a browser, never used to mint a public or signed URL: downloads are streamed
through the API after the usual project-membership and role checks). The adapter for a stored file is chosen by `source_documents.storage_backend`.

Safety: file names on disk are generated (a random id plus an extension taken from the VALIDATED content type, never from the client);
keys are validated against a strict pattern and the resolved path must stay inside the store root (no traversal, no symlinks); files are
written 0600 through a temp file and an atomic rename; nothing here ever returns or logs an absolute path."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

log = logging.getLogger("anvyra.storage")

EXTENSIONS = ("pdf", "png", "jpg", "webp", "txt", "csv", "xlsx", "docx")
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


class SupabaseEvidenceStore:
    """Evidence in a private Supabase Storage bucket. Object keys have exactly the shape of the local keys (<project uuid>/<2 hex>/<32 hex>.<ext>), so what the database keeps
    is the same opaque key whichever backend holds the bytes. `opener` is injectable for tests."""
    backend = "SUPABASE"

    def __init__(self, base_url: str, service_key: str, bucket: str, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 30.0):
        if not (base_url or "").startswith("https://") and not (base_url or "").startswith("http://127.0.0.1"):
            raise StorageError("SUPABASE_URL must be an https URL")
        if not service_key:
            raise StorageError("the storage service key is not configured")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", bucket or ""):
            raise StorageError("invalid storage bucket name")
        self._base, self._key, self._bucket, self._open, self._timeout = base_url.rstrip("/"), service_key, bucket, opener, timeout

    def __repr__(self) -> str:                                              # never leaks the key
        return f"SupabaseEvidenceStore(bucket={self._bucket})"

    def _req(self, method: str, path: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None):
        h = {"apikey": self._key, "Authorization": f"Bearer {self._key}", **(headers or {})}
        return urllib.request.Request(f"{self._base}/storage/v1{path}", method=method, data=data, headers=h)

    def _call(self, req) -> bytes:
        try:
            with self._open(req, timeout=self._timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise StorageError(f"storage request failed (HTTP {e.code})") from None
        except urllib.error.URLError as e:
            raise StorageError(f"storage unreachable ({e.reason.__class__.__name__})") from None

    @staticmethod
    def _obj(key: str) -> str:
        if not isinstance(key, str) or not _KEY.match(key):
            raise StorageError("invalid storage key")
        return urllib.parse.quote(key, safe="/")

    def put(self, project_id, content: bytes, ext: str) -> str:
        if ext not in EXTENSIONS:
            raise StorageError("unsupported extension")
        name = uuid.uuid4().hex
        key = f"{uuid.UUID(str(project_id))}/{name[:2]}/{name}.{ext}"
        self._call(self._req("POST", f"/object/{self._bucket}/{self._obj(key)}", content, {"Content-Type": "application/octet-stream", "x-upsert": "false"}))
        return key

    def put_at(self, key: str, content: bytes) -> None:
        """store under a GIVEN key and never overwrite (x-upsert false): used only by the one-off migration of existing LOCAL documents, whose key is already in the database"""
        self._call(self._req("POST", f"/object/{self._bucket}/{self._obj(key)}", content, {"Content-Type": "application/octet-stream", "x-upsert": "false"}))

    def exists(self, key: str) -> bool:
        try:
            self._call(self._req("GET", f"/object/info/authenticated/{self._bucket}/{self._obj(key)}"))
            return True
        except StorageError:
            return False

    def read(self, key: str) -> bytes:
        try:
            return self._call(self._req("GET", f"/object/authenticated/{self._bucket}/{self._obj(key)}"))
        except StorageError as e:
            raise StorageError("stored file is missing or unreadable") from e

    def delete(self, key: str) -> None:
        try:
            self._call(self._req("DELETE", f"/object/{self._bucket}/{self._obj(key)}"))
        except StorageError as e:                                             # best effort: a failed clean-up never hides the original error
            log.warning("could not delete an orphaned object: %s", e)

    def verify_bucket(self) -> Dict[str, Any]:
        """deploy-time check (scripts/check_storage.py): the bucket exists and is PRIVATE. Raises StorageError otherwise."""
        info = json.loads(self._call(self._req("GET", f"/bucket/{self._bucket}")) or b"{}")
        if info.get("public"):
            raise StorageError("the evidence bucket is PUBLIC: make it private before use")
        return {"bucket": self._bucket, "public": False}


class ReadThroughLocalStore:
    """Documents recorded as LOCAL (their row is immutable) on a deployment that has no local disk: read the local file when it is there, otherwise the SAME key from the
    private bucket, where scripts/migrate_demo_documents.py put the hash-verified copy. Read-only: new files never go through here, and a file that is in neither place is
    reported as missing, never invented. The caller still checks the sha256 recorded in the row."""
    backend = "LOCAL"

    def __init__(self, local: Optional["LocalEvidenceStore"], bucket_store: "SupabaseEvidenceStore"):
        self._local, self._bucket = local, bucket_store              # local is None where there is no writable disk (a serverless function)

    def read(self, key: str) -> bytes:
        if self._local is not None:
            try:
                return self._local.read(key)
            except StorageError:
                pass
        return self._bucket.read(key)

    def put(self, *a, **k):
        raise StorageError("the read-through store is read-only")

    def delete(self, key: str) -> None:
        raise StorageError("the read-through store is read-only")


_store: Optional[EvidenceStore] = None
_local_for_reads: Optional[LocalEvidenceStore] = None


def default_root() -> Path:
    env = os.environ.get("V2_EVIDENCE_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / ".local" / "v2_evidence"


def _supabase_from_env() -> SupabaseEvidenceStore:
    return SupabaseEvidenceStore(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""), os.environ.get("V2_STORAGE_BUCKET", "evidence"))


def get_store() -> EvidenceStore:
    """the store NEW uploads go to: V2_STORAGE_BACKEND=supabase (private bucket) or, by default, the local directory"""
    global _store
    if _store is None:
        kind = os.environ.get("V2_STORAGE_BACKEND", "local").strip().lower()
        if kind == "supabase":
            _store = _supabase_from_env()
        elif kind == "local":
            _store = LocalEvidenceStore(default_root())
        else:
            raise StorageError("V2_STORAGE_BACKEND must be 'local' or 'supabase'")
    return _store


def store_for(backend: str) -> EvidenceStore:
    """the store that holds an EXISTING file (source_documents.storage_backend), whichever backend new uploads use"""
    global _local_for_reads
    cur = get_store()
    if backend == cur.backend:
        return cur
    if backend == "SUPABASE":
        return _supabase_from_env()
    if backend == "LOCAL":
        if cur.backend == "SUPABASE":                                       # a deployment whose new files live in the bucket: LOCAL-labelled documents are read through
            try:
                if _local_for_reads is None:
                    _local_for_reads = LocalEvidenceStore(default_root())
            except (OSError, StorageError):                                 # no usable local disk: the bucket is the only place to look
                return ReadThroughLocalStore(None, cur)
            return ReadThroughLocalStore(_local_for_reads, cur)
        if _local_for_reads is None:
            _local_for_reads = LocalEvidenceStore(default_root())
        return _local_for_reads
    raise StorageError("unknown storage backend")


def set_store(store: Optional[EvidenceStore]) -> None:
    """tests / adapters"""
    global _store, _local_for_reads
    _store = store
    _local_for_reads = None
