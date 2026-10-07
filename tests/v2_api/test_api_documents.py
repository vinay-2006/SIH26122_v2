"""Uploads, downloads and extraction over HTTP."""
import hashlib
import io
import os
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from apikit import activity_pct, decide, file_claim, jpeg, summary, upload, url
from test_storage_extraction_unit import pdf_bytes, png_bytes, xlsx_bytes
from v2api import connect

REPORT = b"Site report 2026-09-30\nA2010: welded 600 joints cumulative\nA1010 - 40% complete\nA3010 progress noted, 1500 joints\n"


def stored_files(evidence_dir):
    return [p for p in Path(evidence_dir).rglob("*") if p.is_file()]


def test_upload_stores_a_generated_name_and_returns_no_path(kit, api, evidence_dir):
    body = b"A2010 progress"
    d = upload(kit, api, "../../etc/Daily Report?.txt", body, "DAILY_REPORT")
    assert d["file_name"] == "Daily Report?.txt" and d["sha256"] == hashlib.sha256(body).hexdigest() and d["size_bytes"] == len(body) and d["mime_type"] == "text/plain"
    files = stored_files(evidence_dir)
    assert len(files) == 1 and files[0].suffix == ".txt" and "Daily" not in files[0].name and files[0].read_bytes() == body
    assert str(evidence_dir) not in str(d) and "storage" not in str(d).lower()
    meta = api.get(url(kit, f"/documents/{d['document_id']}"), kit.world.se).text
    assert str(evidence_dir) not in meta and "storage_path" not in meta and "/ev" not in meta
    with connect() as c:
        r = c.execute("select storage_path, storage_backend, uploaded_by from source_documents where kind = 'DAILY_REPORT'").fetchone()
    assert r["storage_backend"] == "LOCAL" and r["storage_path"].startswith(kit.project.__str__() + "/") and "etc" not in r["storage_path"] and r["uploaded_by"] == kit.world.se.id


def test_hostile_or_wrong_files_are_refused_and_leave_nothing_behind(kit, api, evidence_dir):
    cases = [("a.exe", b"MZ", "UNSUPPORTED_FILE_TYPE", 415), ("a.pdf", png_bytes(), "FILE_CONTENT_MISMATCH", 422), ("a.txt", b"", "EMPTY_FILE", 422),
             ("a.jpg", b"\xff\xd8\xff" + b"1" * 50, "CORRUPT_FILE", 422), ("a.pdf", b"%PDF-1.4 /JavaScript (x)", "ACTIVE_CONTENT_REFUSED", 415),
             ("a.xer", b"ERMHDR\t8.0", "SCHEDULE_FILE_NOT_ALLOWED", 422), ("a.pdf", pdf_bytes(["x"]), "KIND_TYPE_MISMATCH", 422)]
    for name, body, code, status in cases:
        kind = "PHOTO" if code == "KIND_TYPE_MISMATCH" else ("EVIDENCE" if name == "a.jpg" else "DAILY_REPORT")
        r = api.post(url(kit, "/documents"), kit.world.se, files={"file": (name, body, "x/y")}, data={"kind": kind})
        assert r.status_code == status and r.json()["error"]["code"] == code, (name, r.text)
    assert api.post(url(kit, "/documents"), kit.world.se, files={"file": ("a.txt", b"hello world", "text/plain")}, data={"kind": "SCHEDULE_FILE"}).json()["error"]["code"] == "BAD_KIND"
    assert kit.count("source_documents", "kind <> 'SCHEDULE_FILE'") == 0 and stored_files(evidence_dir) == []


def test_duplicate_upload_is_refused_and_the_second_copy_is_not_kept(kit, api, evidence_dir):
    d = upload(kit, api, "a.txt", b"same bytes here")
    r = api.post(url(kit, "/documents"), kit.world.se, files={"file": ("renamed.txt", b"same bytes here", "text/plain")}, data={"kind": "DAILY_REPORT"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_UPLOAD" and r.json()["error"]["details"]["document_id"] == d["document_id"]
    assert len(stored_files(evidence_dir)) == 1
    other = api.post(url(kit, "/documents"), kit.world.se2, files={"file": ("a.txt", b"same bytes here", "text/plain")}, data={"kind": "DAILY_REPORT"})
    assert other.status_code == 409 and other.json()["error"]["details"] is None                       # another engineer's document id is not disclosed


def test_oversize_uploads_are_refused(kit, api, monkeypatch):
    from backend.v2 import filetypes
    monkeypatch.setitem(filetypes.LIMITS, "text", 1000)
    r = api.post(url(kit, "/documents"), kit.world.se, files={"file": ("big.txt", b"a" * 1001, "text/plain")}, data={"kind": "EVIDENCE"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "TOO_LARGE"


def test_who_may_upload_what(kit, api):
    assert api.post(url(kit, "/documents"), kit.world.pm, files={"file": ("a.txt", b"hello there", "text/plain")}, data={"kind": "EVIDENCE"}).status_code == 403
    r = api.post(url(kit, "/documents"), kit.world.sup, files={"file": ("a.txt", b"supervisor note", "text/plain")}, data={"kind": "DAILY_REPORT"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"
    assert api.post(url(kit, "/documents"), kit.world.sup, files={"file": ("a.txt", b"supervisor note", "text/plain")}, data={"kind": "EVIDENCE"}).status_code == 201
    assert api.post(url(kit, "/documents"), kit.world.world.outsider if False else kit.world.outsider, files={"file": ("a.txt", b"hello there", "text/plain")}, data={"kind": "EVIDENCE"}).status_code == 403


def test_photo_metadata_is_read_when_present(kit, api):
    from PIL import Image
    b = io.BytesIO()
    im = Image.new("RGB", (8, 8)); ex = im.getexif(); ex[271] = "TestCam"
    ex.get_ifd(0x8769)[36867] = "2026:09:30 10:15:00"
    ex.get_ifd(0x8825).update({1: "N", 2: (26.0, 30.0, 0.0), 3: "E", 4: (91.0, 45.0, 0.0)})
    im.save(b, "JPEG", exif=ex)
    d = upload(kit, api, "site.jpg", b.getvalue(), "PHOTO")
    assert d["captured_at"].startswith("2026-09-30T10:15") and abs(d["gps_lat"] - 26.5) < 1e-6 and abs(d["gps_lon"] - 91.75) < 1e-6
    plain = upload(kit, api, "plain.jpg", jpeg(), "PHOTO")
    assert "gps_lat" not in plain


def test_download_access_matrix(kit, api):
    mine = upload(kit, api, "mine.txt", b"my daily report A2010")
    theirs = upload(kit, api, "theirs.txt", b"their daily report", who=kit.world.se2)
    claimed = upload(kit, api, "claimed.txt", b"evidence for a claim", "EVIDENCE")
    file_claim(kit, api, "A2010", 100, evidence_document_ids=[claimed["document_id"]])
    issue_doc = upload(kit, api, "leak.txt", b"photo of the leak", "ISSUE_REPORT")
    assert api.post(url(kit, "/issues"), kit.world.se, json={"title": "Pipe leak", "category_code": "EQUIPMENT_SHORTAGE", "activity_uid": str(kit.uid("A1010")), "evidence_document_ids": [issue_doc["document_id"]]}).status_code == 201

    def code(doc, user, tail=""):
        return api.get(url(kit, f"/documents/{doc['document_id']}{tail}"), user).status_code
    r = api.get(url(kit, f"/documents/{mine['document_id']}/content"), kit.world.se)
    assert r.status_code == 200 and r.content == b"my daily report A2010" and r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff"
    assert code(theirs, kit.world.se, "/content") == 404 and code(theirs, kit.world.se) == 404                      # another engineer's file looks absent
    assert code(mine, kit.world.sup, "/content") == 200 and code(theirs, kit.world.sup) == 200
    assert code(mine, kit.world.pm, "/content") == 404 and code(claimed, kit.world.pm) == 404 and code(claimed, kit.world.pm, "/content") == 404   # claim content is not the PM's
    assert code(issue_doc, kit.world.pm, "/content") == 200                                                          # issue evidence is
    assert code(mine, kit.world.outsider, "/content") == 403
    lst = lambda u: {x["document_id"] for x in api.get(url(kit, "/documents"), u).json()["items"]}
    assert lst(kit.world.se) == {mine["document_id"], claimed["document_id"], issue_doc["document_id"]}
    assert lst(kit.world.se2) == {theirs["document_id"]} and lst(kit.world.sup) >= {mine["document_id"], theirs["document_id"]}
    pm_docs = api.get(url(kit, "/documents"), kit.world.pm).json()["items"]
    assert issue_doc["document_id"] in {x["document_id"] for x in pm_docs} and {x["kind"] for x in pm_docs if x["document_id"] != issue_doc["document_id"]} <= {"SCHEDULE_FILE"}
    assert {mine["document_id"], theirs["document_id"], claimed["document_id"]}.isdisjoint({x["document_id"] for x in pm_docs})


def test_a_tampered_stored_file_is_detected(kit, api, evidence_dir):
    d = upload(kit, api, "a.txt", b"original content")
    stored_files(evidence_dir)[0].write_bytes(b"tampered content")
    r = api.get(url(kit, f"/documents/{d['document_id']}/content"), kit.world.se)
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTEGRITY_FAILURE"


def test_a_missing_stored_file_is_reported_without_a_path(kit, api, evidence_dir):
    d = upload(kit, api, "a.txt", b"original content")
    stored_files(evidence_dir)[0].unlink()
    r = api.get(url(kit, f"/documents/{d['document_id']}/content"), kit.world.se)
    assert r.status_code == 404 and r.json()["error"]["code"] == "CONTENT_UNAVAILABLE" and str(evidence_dir) not in r.text


def test_uploaded_documents_are_immutable_in_the_database(kit, api):
    import psycopg
    d = upload(kit, api, "a.txt", b"original content")
    with connect() as c:
        c.execute("select set_config('app.system','on',false)")
        for sql in ("update source_documents set sha256 = repeat('a', 64)", "update source_documents set file_name = 'other.txt'", "update source_documents set storage_path = 'x'", "update source_documents set kind = 'EVIDENCE'"):
            try:
                c.execute(sql)
                raise AssertionError(f"{sql} should have been refused")
            except psycopg.errors.CheckViolation:
                pass
        c.execute("update source_documents set extraction_status = 'EXTRACTED', claims_extracted = 0")           # extraction outcome stays writable


# ------------------------------------------------------------------ extraction -> claims
def test_extraction_files_only_clean_rows_as_pending_claims(kit, api):
    d = upload(kit, api, "report.txt", REPORT)
    pre = api.post(url(kit, f"/documents/{d['document_id']}/extract"), kit.world.se, json={"create_claims": False})
    assert pre.status_code == 200 and pre.json()["preview_only"] and kit.count("execution_events") == 0
    assert [c["outcome"] for c in pre.json()["claims"]] == ["ELIGIBLE", "ELIGIBLE"]
    r = api.post(url(kit, f"/documents/{d['document_id']}/extract"), kit.world.se, json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "EXTRACTED" and out["method"] == "TEXT_RULES" and out["counts"] == {"created": 2, "existing": 0, "skipped": 1}
    assert out["skipped"][0]["activity_ref"] == "A3010" and out["skipped"][0]["reasons"] == ["BASIS_UNKNOWN"]
    assert kit.count("execution_events", "status = 'MATCHED'") == 2 and summary(kit, api)["physical_pct"] == 0          # proposals only
    c = api.get(url(kit, f"/claims/{out['claims'][0]['claim_id']}"), kit.world.se).json()
    assert c["input_channel"] == "TXT" and c["document_id"] == d["document_id"] and c["field_provenance"]["extraction"] == "TEXT_RULES" and [e["document_id"] for e in c["evidence"]] == [d["document_id"]]
    assert c["quantities"][0]["reported_qty"] == 600
    again = api.post(url(kit, f"/documents/{d['document_id']}/extract"), kit.world.se, json={}).json()
    assert again["counts"] == {"created": 0, "existing": 2, "skipped": 1} and kit.count("execution_events") == 2          # re-running never doubles claims
    assert api.get(url(kit, f"/documents/{d['document_id']}"), kit.world.se).json()["extraction_status"] == "EXTRACTED"
    for cl in out["claims"]:
        decide(kit, api, cl["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS") if cl["source_ref"] == "line 3" else decide(kit, api, cl["claim_id"])
    assert activity_pct(kit, api, "A2010") == D("30") and activity_pct(kit, api, "A1010") == D("40")


def test_csv_xlsx_and_pdf_documents_extract(kit, api):
    csv_ = b"Activity ID,Date,Cumulative Qty,Unit\nA2010,2026-09-30,400,joints\nA2000,2026-09-30,2,furlongs\nA9999,2026-09-30,1,joints\n"
    r = api.post(url(kit, f"/documents/{upload(kit, api, 'p.csv', csv_)['document_id']}/extract"), kit.world.se, json={}).json()
    assert r["method"] == "CSV_TABLE" and r["counts"] == {"created": 1, "existing": 0, "skipped": 2}
    assert {tuple(s["reasons"]) for s in r["skipped"]} == {("UNKNOWN_UNIT",), ("ACTIVITY_NOT_FOUND",)}
    x = xlsx_bytes([["Activity ID", "Date", "Qty Today", "Unit"], ["A2010", date.today(), 50, "joints"]])
    rx = api.post(url(kit, f"/documents/{upload(kit, api, 'p.xlsx', x)['document_id']}/extract"), kit.world.se, json={}).json()
    assert rx["method"] == "XLSX_TABLE" and rx["counts"]["created"] == 1
    p = pdf_bytes([f"Report {date.today()}", "A2010 welded 700 joints cumulative"])
    rp = api.post(url(kit, f"/documents/{upload(kit, api, 'p.pdf', p)['document_id']}/extract"), kit.world.se, json={}).json()
    assert rp["method"] == "PDF_TEXT_RULES" and rp["page_count"] == 1 and rp["counts"]["created"] == 1


def test_failed_extraction_creates_no_claim_and_says_why(kit, api):
    import fitz
    d = fitz.open(); d.new_page().draw_rect(fitz.Rect(10, 10, 90, 90))
    doc = upload(kit, api, "scan.pdf", d.tobytes())
    r = api.post(url(kit, f"/documents/{doc['document_id']}/extract"), kit.world.se, json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXTRACTION_FAILED" and r.json()["error"]["details"] == {"reason": "SCANNED_NOT_SUPPORTED", "claims_created": 0}
    assert kit.count("execution_events") == 0 and kit.ledger() == ([], [])
    got = api.get(url(kit, f"/documents/{doc['document_id']}"), kit.world.se).json()
    assert got["extraction_status"] == "FAILED" and "SCANNED_NOT_SUPPORTED" in got["extraction_error"] and got["claims_extracted"] == 0
    assert kit.count("audit_logs", "action = 'EXTRACTION_FAILED'") == 1
    photo = upload(kit, api, "p.jpg", jpeg(), "PHOTO")
    assert api.post(url(kit, f"/documents/{photo['document_id']}/extract"), kit.world.se, json={}).json()["error"]["code"] == "NOT_EXTRACTABLE"
    nothing = upload(kit, api, "n.txt", b"Weather was fine. Nothing else to report today.")
    rn = api.post(url(kit, f"/documents/{nothing['document_id']}/extract"), kit.world.se, json={}).json()
    assert rn["status"] == "NO_CLAIMS" and rn["claims"] == [] and kit.count("execution_events") == 0


def test_only_the_uploader_extracts_and_a_schedule_is_needed(kit, api):
    d = upload(kit, api, "report.txt", REPORT)
    assert api.post(url(kit, f"/documents/{d['document_id']}/extract"), kit.world.se2, json={}).status_code == 404
    for u in (kit.world.sup, kit.world.pm):
        assert api.post(url(kit, f"/documents/{d['document_id']}/extract"), u, json={}).status_code == 403
    assert kit.count("execution_events") == 0


def test_live_llm_is_never_used_even_if_enabled_in_the_environment(kit, api, monkeypatch):
    monkeypatch.setenv("SETUAI_ALLOW_LIVE_LLM", "1"); monkeypatch.setenv("LLM_API_KEY", "dummy")
    import socket
    def boom(*a, **k): raise AssertionError("network used during extraction")
    monkeypatch.setattr(socket.socket, "connect", boom)
    d = upload(kit, api, "report.txt", REPORT)
    # the TestClient talks to the app in-process; the database connection pool is already open, so only NEW sockets would trip the guard
    assert api.post(url(kit, f"/documents/{d['document_id']}/extract"), kit.world.se, json={}).status_code == 200
