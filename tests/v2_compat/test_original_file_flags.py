"""A document whose original file was never kept says so (the UI shows 'original file not retained'); a stored one says it is there; the content route never pretends."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2_api"))
from apikit import upload as upload_doc  # noqa: E402
from v2api import upload as upload_schedule  # noqa: E402

pytestmark = pytest.mark.db_write


def test_schedule_files_are_flagged_as_not_retained_and_their_content_is_unavailable(kit, api):
    r = upload_schedule(api, kit.world.pm, kit.project, "csv", "nsp")                 # the kit's schedule came from this file: the answer names the existing import
    iid = r.json()["import_id"] if r.status_code == 201 else r.json()["error"]["details"]["import_id"]
    imp = api.get(f"/api/v2/projects/{kit.project}/schedule-imports/{iid}", kit.world.pm).json()
    assert imp["original_file_available"] is False
    docs = api.get(f"/api/v2/projects/{kit.project}/documents?kind=SCHEDULE_FILE", kit.world.pm).json()["items"]
    assert docs and all(d["file_available"] is False for d in docs)
    c = api.get(f"/api/v2/projects/{kit.project}/documents/{docs[0]['document_id']}/content", kit.world.pm)
    assert c.status_code == 404 and c.json()["error"]["code"] == "CONTENT_UNAVAILABLE"


def test_a_stored_report_is_flagged_available(kit, api):
    d = upload_doc(kit, api, "report.txt", b"A2010 progress", "DAILY_REPORT")
    assert api.get(f"/api/v2/projects/{kit.project}/documents/{d['document_id']}", kit.world.se).json()["file_available"] is True
