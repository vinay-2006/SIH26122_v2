"""Uploads and downloads through the API with the private-bucket store: same behaviour, same access rules, same audit trail, no key in any response."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apikit import upload, url  # noqa: E402
from fakesupabase import FakeStorageAPI  # noqa: E402
from v2api import connect  # noqa: E402

from backend.v2 import storage  # noqa: E402

URL = "https://abcdefghijklmnopqrst.supabase.co"


def use_bucket(fake):
    storage.set_store(storage.SupabaseEvidenceStore(URL, fake.key, "evidence", opener=fake))


def test_uploads_go_to_the_private_bucket_and_download_through_the_api_with_the_usual_checks(kit, api, evidence_dir):
    fake = FakeStorageAPI()
    use_bucket(fake)
    body = b"A2010 progress report"
    d = upload(kit, api, "report.txt", body, "DAILY_REPORT")
    assert len(fake.objects) == 1 and next(iter(fake.objects.values())) == body
    with connect() as c:
        r = c.execute("select storage_backend, storage_path from source_documents where kind = 'DAILY_REPORT'").fetchone()
    assert r["storage_backend"] == "SUPABASE" and f"evidence/{r['storage_path']}" in fake.objects
    assert not list(evidence_dir.rglob("*.txt"))                                                  # nothing was written to local disk
    got = api.get(url(kit, f"/documents/{d['document_id']}/content"), kit.world.se)
    assert got.status_code == 200 and got.content == body
    assert fake.key not in got.text and fake.key not in str(d) and "storage_path" not in str(d)
    assert api.get(url(kit, f"/documents/{d['document_id']}/content"), kit.world.outsider).status_code == 403          # project-level access is unchanged
    assert api.get(url(kit, f"/documents/{d['document_id']}/content"), kit.world.pm).status_code in (403, 404)         # the Project Manager never reads report content
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'DOCUMENT_UPLOADED'").fetchone()["n"] == 1


def test_a_failed_upload_leaves_no_orphan_in_the_bucket(kit, api):
    fake = FakeStorageAPI()
    use_bucket(fake)
    upload(kit, api, "once.txt", b"same bytes", "DAILY_REPORT")
    n = len(fake.objects)
    upload(kit, api, "again.txt", b"same bytes", "DAILY_REPORT", expect=409)                       # duplicate content is refused after the bytes were put
    assert len(fake.objects) == n


def test_a_storage_outage_is_a_clean_error(kit, api):
    use_bucket(FakeStorageAPI(fail=503))
    r = api.post(url(kit, "/documents"), kit.world.se, files={"file": ("x.txt", b"hello world", "text/plain")}, data={"kind": "DAILY_REPORT"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "STORAGE_UNAVAILABLE" and "503" not in r.text


def test_files_stored_locally_before_the_switch_stay_readable(kit, api, evidence_dir):
    old = upload(kit, api, "old.txt", b"stored before the switch", "DAILY_REPORT")
    use_bucket(FakeStorageAPI())                                                                    # new uploads now go to the bucket
    assert api.get(url(kit, f"/documents/{old['document_id']}/content"), kit.world.se).content == b"stored before the switch"
