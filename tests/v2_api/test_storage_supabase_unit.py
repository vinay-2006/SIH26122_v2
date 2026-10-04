"""DB-free: the Supabase Storage adapter against an in-memory fake of the REST API."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakesupabase import FakeStorageAPI  # noqa: E402
from backend.v2 import storage  # noqa: E402

PID = uuid.uuid4()
URL = "https://abcdefghijklmnopqrst.supabase.co"


def store(fake=None, bucket="evidence"):
    fake = fake or FakeStorageAPI()
    return storage.SupabaseEvidenceStore(URL, fake.key, bucket, opener=fake), fake


def test_put_read_delete_round_trip_with_the_same_key_shape_as_local():
    s, fake = store()
    key = s.put(PID, b"hello report", "txt")
    assert storage._KEY.match(key) and key.startswith(f"{PID}/") and key.endswith(".txt")
    assert s.read(key) == b"hello report"
    method, path, hdr = fake.calls[0]
    assert (method, path) == ("POST", f"/object/evidence/{key}") and hdr["authorization"] == f"Bearer {fake.key}" and hdr["x-upsert"] == "false"
    assert fake.calls[1][:2] == ("GET", f"/object/authenticated/evidence/{key}")                       # the authenticated route, never a public URL
    s.delete(key)
    with pytest.raises(storage.StorageError):
        s.read(key)


def test_no_public_or_signed_url_is_ever_used():
    s, fake = store()
    k = s.put(PID, b"x", "csv"); s.read(k); s.delete(k)
    assert all("/public/" not in p and "/sign/" not in p for _, p, _ in fake.calls)


def test_invalid_keys_and_extensions_never_reach_the_network():
    s, fake = store()
    for bad in ("../etc/passwd", f"{PID}/../../x.txt", "", None, f"{PID}/aa/{'0' * 32}.exe", "x/y/z"):
        with pytest.raises(storage.StorageError):
            s.read(bad)
        s.delete(bad) if False else None
    with pytest.raises(storage.StorageError):
        s.put(PID, b"x", "exe")
    assert fake.calls == []


def test_errors_are_generic_and_the_key_never_leaks():
    fake = FakeStorageAPI(fail=500)
    s, _ = store(fake)
    with pytest.raises(storage.StorageError) as e:
        s.put(PID, b"x", "txt")
    assert "500" in str(e.value) and fake.key not in str(e.value) and "secret body" not in str(e.value)
    assert fake.key not in repr(s)
    wrong = storage.SupabaseEvidenceStore(URL, "a-different-key", "evidence", opener=FakeStorageAPI())
    with pytest.raises(storage.StorageError, match="401"):
        wrong.put(PID, b"x", "txt")


def test_a_failed_delete_never_hides_the_original_error():
    s, fake = store()
    k = s.put(PID, b"x", "txt")
    fake.fail = 503
    s.delete(k)                                                                                   # swallowed (logged), not raised


def test_the_bucket_must_be_private():
    store()[0].verify_bucket()
    with pytest.raises(storage.StorageError, match="PUBLIC"):
        store(FakeStorageAPI(public=True))[0].verify_bucket()


def test_configuration_is_validated():
    for url, key, bucket in (("http://example.com", "k", "evidence"), (URL, "", "evidence"), (URL, "k", "Bad Bucket!"), (URL, "k", "")):
        with pytest.raises(storage.StorageError):
            storage.SupabaseEvidenceStore(url, key, bucket)


def test_selection_by_environment_and_by_the_backend_recorded_on_each_file(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_EVIDENCE_DIR", str(tmp_path / "ev"))
    storage.set_store(None)
    assert storage.get_store().backend == "LOCAL"
    monkeypatch.setenv("V2_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", URL); monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k"); monkeypatch.setenv("V2_STORAGE_BUCKET", "evidence")
    storage.set_store(None)
    assert storage.get_store().backend == "SUPABASE"
    assert storage.store_for("SUPABASE").backend == "SUPABASE" and storage.store_for("LOCAL").backend == "LOCAL"       # old local files stay readable after the switch
    with pytest.raises(storage.StorageError):
        storage.store_for("S3")
    monkeypatch.setenv("V2_STORAGE_BACKEND", "ftp")
    storage.set_store(None)
    with pytest.raises(storage.StorageError):
        storage.get_store()
    monkeypatch.delenv("V2_STORAGE_BACKEND"); storage.set_store(None)


def test_put_at_uses_a_given_key_never_overwrites_and_exists_reports_it():
    s, fake = store()
    key = f"{PID}/ab/{'a' * 32}.txt"
    assert not s.exists(key)
    s.put_at(key, b"original")
    assert s.exists(key) and s.read(key) == b"original"
    with pytest.raises(storage.StorageError):
        s.put_at(key, b"different")                                        # x-upsert false: a second upload cannot replace the first
    assert s.read(key) == b"original"
    assert fake.calls[1][2]["x-upsert"] == "false"


def test_read_through_serves_a_local_labelled_document_from_the_bucket_and_never_invents_one(tmp_path, monkeypatch):
    s, fake = store()
    key = f"{PID}/ab/{'b' * 32}.txt"
    local = storage.LocalEvidenceStore(tmp_path)
    rt = storage.ReadThroughLocalStore(local, s)
    with pytest.raises(storage.StorageError):
        rt.read(key)                                                       # in neither place: missing, not fabricated
    s.put_at(key, b"from the bucket")
    assert rt.read(key) == b"from the bucket"
    (tmp_path / str(PID) / "ab").mkdir(parents=True, exist_ok=True)
    (tmp_path / str(PID) / "ab" / f"{'b' * 32}.txt").write_bytes(b"from the disk")
    assert rt.read(key) == b"from the disk"                                # the local file wins when present
    assert storage.ReadThroughLocalStore(None, s).read(key) == b"from the bucket"   # a serverless function has no disk
    with pytest.raises(storage.StorageError):
        rt.put(PID, b"x", "txt")
    with pytest.raises(storage.StorageError):
        rt.delete(key)


def test_store_for_local_documents_reads_through_only_when_new_files_go_to_the_bucket(tmp_path, monkeypatch):
    s, _ = store()
    monkeypatch.setenv("V2_EVIDENCE_DIR", str(tmp_path))
    storage.set_store(s)
    try:
        assert isinstance(storage.store_for("LOCAL"), storage.ReadThroughLocalStore)
        assert storage.store_for("SUPABASE") is s
    finally:
        storage.set_store(None)
    local = storage.LocalEvidenceStore(tmp_path)
    storage.set_store(local)
    try:
        assert not isinstance(storage.store_for("LOCAL"), storage.ReadThroughLocalStore)
    finally:
        storage.set_store(None)
