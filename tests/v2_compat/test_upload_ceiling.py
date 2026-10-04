"""Deployments with a request-size ceiling (Vercel refuses bodies above 4.5 MB): an oversized upload is refused as JSON, before any route runs, creating nothing."""
import io

import pytest

pytestmark = pytest.mark.db_write
CAP = 200_000


@pytest.fixture
def capped(monkeypatch):
    monkeypatch.setenv("V2_MAX_REQUEST_BYTES", str(CAP))


def big_csv(n=CAP * 2):
    return (b"activity,qty\n" + b"A2000,1\n" * (n // 8))


def tables(kit):
    return {t: kit.count(t) for t in ("execution_events", "source_documents")} if hasattr(kit, "count") else {}


def test_oversized_uploads_are_refused_with_the_apis_error_and_create_nothing(capped, kit, api, lg):
    before = tables(kit)
    r = lg.post("/api/v1/claims/file", kit.world.se, files={"file": ("report.csv", big_csv(), "text/csv")}, data={"purpose": "SCANNED_DIARY"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "REQUEST_TOO_LARGE", r.text
    r = lg.post("/api/v1/claims/batch", kit.world.se, files=[("files", ("a.csv", big_csv(), "text/csv"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "REQUEST_TOO_LARGE", r.text
    r = api.post(f"/api/v2/projects/{kit.project}/documents", kit.world.se, files={"file": ("a.csv", big_csv(), "text/csv")}, data={"kind": "DAILY_REPORT"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "REQUEST_TOO_LARGE", r.text
    assert tables(kit) == before, "an oversized upload must not leave a claim, document or batch behind"


def test_a_request_under_the_ceiling_still_works_and_the_ceiling_is_off_by_default(capped, kit, api, lg):
    r = lg.post("/api/v1/claims/file", kit.world.se, files={"file": ("report.csv", b"activity,qty\nA2000,1\n", "text/csv")}, data={"purpose": "SCANNED_DIARY"})
    assert r.status_code != 413, r.text


@pytest.fixture
def capped_with_cors(monkeypatch):
    monkeypatch.setenv("V2_MAX_REQUEST_BYTES", str(CAP))
    monkeypatch.setenv("V2_CORS_ORIGINS", "https://app.example")


def test_the_refusal_carries_cors_headers_so_the_browser_can_show_it(capped_with_cors, kit, api):
    r = api.post(f"/api/v2/projects/{kit.project}/documents", kit.world.se, files={"file": ("a.csv", big_csv(), "text/csv")}, data={"kind": "DAILY_REPORT"}, headers={"Origin": "https://app.example"})
    assert r.status_code == 413 and r.headers.get("access-control-allow-origin") == "https://app.example", dict(r.headers)
