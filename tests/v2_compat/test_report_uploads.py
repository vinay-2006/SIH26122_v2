"""Site Engineer 'Upload Progress Report': what is accepted, what is read, and what is refused (baseline schedules, legacy Office files, unsupported types)."""
import io
import shutil
import zipfile

import pytest

pytestmark = pytest.mark.db_write

REPORT = "Daily report\nA2000 pipe stringing 40 percent complete\nA2010 welding mainline 10 percent complete\n"


def docx_bytes(text: str) -> bytes:
    from xml.sax.saxutils import escape
    body = "".join(f"<w:p><w:r><w:t>{escape(l)}</w:t></w:r></w:p>" for l in text.splitlines())
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
    return b.getvalue()


def image_bytes(text: str, fmt: str = "PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1500, 220), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
    except Exception:
        f = ImageFont.load_default()
    d.text((20, 70), text, fill="black", font=f)
    b = io.BytesIO()
    im.save(b, fmt)
    return b.getvalue()


def upload(lg, who, name, body, mime="application/octet-stream"):
    return lg.post("/api/v1/claims/file", who, files={"file": (name, body, mime)}, data={"purpose": "SCANNED_DIARY"})


def test_text_csv_and_word_reports_are_read_into_claims(kit, lg):
    r = upload(lg, kit.world.se, "dpr.txt", REPORT.encode(), "text/plain")
    assert r.status_code == 200 and len(r.json()) >= 2, r.text
    csv = b"Activity ID,Activity Name,Discipline,Progress Pct\nA2000,Pipe stringing,Piping,40\nA2010,Welding mainline,Piping,10\n"
    assert upload(lg, kit.world.se, "progress.csv", csv, "text/csv").status_code == 200
    d = upload(lg, kit.world.se, "site-report.docx", docx_bytes(REPORT + "Weather clear, crew of 12 on site."))
    assert d.status_code == 200 and len(d.json()) >= 2, d.text
    assert all(c["input_channel"] == "FILE_UPLOAD" and c["status"] not in ("APPROVED", "EDITED") for c in d.json())
    assert kit.count("planner_decisions") == 0                                         # reading a report never approves anything


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR is not installed here")
def test_a_scanned_site_report_image_is_stored_as_evidence_and_read_by_ocr(kit, lg):
    for name, fmt, mime in (("scan.png", "PNG", "image/png"), ("scan.jpg", "JPEG", "image/jpeg"), ("scan.webp", "WEBP", "image/webp")):
        r = upload(lg, kit.world.se, name, image_bytes(f"A2010 Welding mainline 120 joints completed today {name}", fmt), mime)
        assert r.status_code == 200, (name, r.text)
        assert r.json() and all(c["input_channel"] == "SCANNED_OCR" for c in r.json()), name
    assert kit.count("source_documents") >= 3


def test_an_unreadable_image_is_refused_not_guessed(kit, lg):
    blank = image_bytes("", "PNG")
    r = upload(lg, kit.world.se, "blank.png", blank, "image/png")
    assert r.status_code in (422, 502) and kit.count("execution_events") == 0, r.text


@pytest.mark.parametrize("name,body,needle", [
    ("progress.xer", b"ERMHDR\t8.0\n", "baseline schedule"), ("p.xml", b"<Project/>", "baseline schedule"), ("plan.mpp", b"x", "baseline schedule"),
    ("legacy.xls", b"\xd0\xcf\x11\xe0" + b"0" * 40, "save it as XLSX"), ("legacy.doc", b"\xd0\xcf\x11\xe0" + b"0" * 40, "save it as DOCX"),
    ("tool.exe", b"MZ\x90", "Unsupported file type"), ("notes.html", b"<script>", "Unsupported file type")])
def test_schedule_files_legacy_office_files_and_other_types_are_refused_with_the_reason(kit, lg, name, body, needle):
    r = upload(lg, kit.world.se, name, body)
    assert r.status_code == 415 and needle in r.text, (name, r.status_code, r.text)
    assert kit.count("execution_events") == 0


def test_the_batch_reports_every_file_and_refuses_schedules_and_legacy_files_individually(kit, lg):
    files = [("files", ("a.txt", REPORT.encode(), "text/plain")), ("files", ("sched.xer", b"ERMHDR\t8.0\n", "application/octet-stream")),
             ("files", ("old.xls", b"\xd0\xcf\x11\xe0" + b"0" * 40, "application/vnd.ms-excel")), ("files", ("w.docx", docx_bytes(REPORT), "application/octet-stream"))]
    r = lg.post("/api/v1/claims/batch", kit.world.se, files=files)
    assert r.status_code == 200, r.text
    rep = r.json()
    by = {f["file_name"]: f for f in rep["files"]}
    assert by["a.txt"]["extraction_status"] == "EXTRACTED" and by["w.docx"]["extraction_status"] in ("EXTRACTED", "EMPTY")
    text = r.text
    assert "baseline schedule" in text and "save it as XLSX" in text                     # each refused file says why
    assert rep["status"] in ("COMPLETED", "PARTIAL")


def test_oversized_files_are_refused(kit, lg):
    r = upload(lg, kit.world.se, "big.txt", b"a" * (5 * 1024 * 1024 + 1), "text/plain")
    assert r.status_code in (413, 422), r.text


def test_the_legacy_schedule_export_route_is_closed(kit, lg):
    r = lg.post("/api/v1/claims/schedule-export", kit.world.se, files={"file": ("p.csv", b"Activity ID,Progress Pct\nA2000,40\n", "text/csv")})
    assert r.status_code == 403 and "SCHEDULE_FILES_NOT_ACCEPTED" in r.text


def test_without_an_ocr_engine_a_report_image_is_refused_with_the_reason_and_never_read_as_text(kit, lg, monkeypatch):
    """the serverless deployment has no Tesseract: the image is NOT represented as extracted, no claim is invented, and the answer says why"""
    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", None)                       # import pytesseract -> ImportError, as in the slim bundle
    r = upload(lg, kit.world.se, "scan.png", image_bytes("A2010 Welding mainline 120 joints completed today", "PNG"), "image/png")
    assert r.status_code in (422, 502), r.text
    assert "OCR" in r.text or "ocr" in r.text.lower(), r.text
    assert kit.count("execution_events") == 0


def test_an_image_is_still_preserved_as_evidence_when_no_ocr_engine_exists(kit, api, monkeypatch):
    """evidence storage and OCR are separate: the photograph is kept (hashed, immutable), and it is recorded as NOT extracted -- no text is attributed to it"""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "v2_api"))
    from apikit import jpeg, upload
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    d = upload(kit, api, "site.jpg", jpeg(), "EVIDENCE")
    assert d["kind"] == "EVIDENCE" and d["sha256"]
    assert d.get("extraction_status") in (None, "NOT_APPLICABLE") and not d.get("extracted_text")
    assert kit.count("source_documents", "kind = 'EVIDENCE'") == 1
