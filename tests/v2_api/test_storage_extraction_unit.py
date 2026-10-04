"""DB-free: the evidence store, the file-type validator and the extractor."""
import io
import os
import stat
import time
import uuid
import zipfile
from datetime import date
from decimal import Decimal as D

import pytest

from backend.v2 import extraction as ex, filetypes, storage

PID = uuid.uuid4()
UNITS = {"joints", "km", "m3", "t", "tonnes", "m"}
ok = lambda u: u.lower() in UNITS


def raises(code):
    class R:
        def __enter__(s): return s
        def __exit__(s, et, ev, tb):
            assert et is not None, f"expected {code}"
            assert getattr(ev, "code", None) == code, f"expected {code}, got {ev!r}"
            return True
    return R()


# ------------------------------------------------------------------ storage
def test_store_writes_generated_names_with_private_permissions(tmp_path):
    s = storage.LocalEvidenceStore(tmp_path / "ev")
    k = s.put(PID, b"hello", "txt")
    assert k.startswith(f"{PID}/") and k.endswith(".txt") and "hello" not in k
    assert s.read(k) == b"hello"
    p = next((tmp_path / "ev").rglob("*.txt"))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "ev").stat().st_mode) == 0o700
    assert not list((tmp_path / "ev").rglob(".part-*"))
    assert s.put(PID, b"hello", "txt") != k                                   # names are never derived from content or client input
    s.delete(k); s.delete(k)
    with pytest.raises(storage.StorageError):
        s.read(k)


@pytest.mark.parametrize("key", ["../../etc/passwd", "/etc/passwd", f"{PID}/../../x.txt", f"{PID}/ab/{'a' * 32}.exe", f"{PID}/ab/{'a' * 32}.txt/..", "", "a/b/c",
                                 f"{PID}\\ab\\{'a' * 32}.txt", f"{PID}/ab/{'a' * 32}.txt\x00.png", None])
def test_store_refuses_hostile_keys(tmp_path, key):
    s = storage.LocalEvidenceStore(tmp_path / "ev")
    with pytest.raises(storage.StorageError):
        s.read(key)
    with pytest.raises(storage.StorageError):
        s.delete(key)


def test_store_refuses_symlink_escape_and_relative_roots(tmp_path):
    s = storage.LocalEvidenceStore(tmp_path / "ev")
    k = s.put(PID, b"x", "txt")
    secret = tmp_path / "secret.txt"; secret.write_text("top secret")
    target = tmp_path / "ev" / k
    target.unlink(); os.symlink(secret, target)
    with pytest.raises(storage.StorageError):
        s.read(k)
    with pytest.raises(storage.StorageError):
        storage.LocalEvidenceStore("relative/dir")
    with pytest.raises(storage.StorageError):
        s.put(PID, b"x", "exe")


# ------------------------------------------------------------------ file types
def xlsx_bytes(rows):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows: ws.append(r)
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def pdf_bytes(lines):
    import fitz
    d = fitz.open(); pg = d.new_page()
    for i, l in enumerate(lines): pg.insert_text((40, 60 + 16 * i), l, fontsize=10)
    return d.tobytes()


def png_bytes():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (4, 4)).save(b, "PNG"); return b.getvalue()


def test_content_decides_the_type_and_must_match_the_extension():
    assert filetypes.detect("r.pdf", pdf_bytes(["hello"]), "DAILY_REPORT").ext == "pdf"
    assert filetypes.detect("r.XLSX", xlsx_bytes([["a"]]), "EVIDENCE").mime.endswith("spreadsheetml.sheet")
    assert filetypes.detect("p.png", png_bytes(), "PHOTO").ext == "png"
    assert filetypes.detect("r.csv", b"a,b\n1,2\n", "EVIDENCE").mime == "text/csv"
    assert filetypes.detect("r.txt", "température 3 °C".encode("cp1252"), "SITE_REPORT").ext == "txt"
    for name, body, code in [("x.exe", b"MZ\x90", "UNSUPPORTED_FILE_TYPE"), ("x.pdf", png_bytes(), "FILE_CONTENT_MISMATCH"), ("x.png", pdf_bytes(["a"]), "FILE_CONTENT_MISMATCH"),
                             ("x.txt", b"\x00\x01\x02binary", "UNSUPPORTED_FILE_TYPE"), ("x.xlsx", b"PK\x03\x04junk", "CORRUPT_FILE"), ("noext", b"hello", "UNSUPPORTED_FILE_TYPE"),
                             ("x.html", b"<script>", "UNSUPPORTED_FILE_TYPE"), ("x.svg", b"<svg/>", "UNSUPPORTED_FILE_TYPE"), ("x.jpg", b"\xff\xd8\xff" + b"0" * 40, "CORRUPT_FILE")]:
        with raises(code):
            filetypes.detect(name, body, "EVIDENCE")


def test_kind_must_suit_the_type():
    with raises("KIND_TYPE_MISMATCH"):
        filetypes.detect("p.pdf", pdf_bytes(["a"]), "PHOTO")
    with raises("KIND_TYPE_MISMATCH"):
        filetypes.detect("p.png", png_bytes(), "DAILY_REPORT")


def test_size_limits_per_type(monkeypatch):
    monkeypatch.setitem(filetypes.LIMITS, "text", 100)
    with raises("TOO_LARGE"):
        filetypes.detect("big.txt", b"a" * 101, "EVIDENCE")
    filetypes.detect("ok.txt", b"a" * 100, "EVIDENCE")


def test_active_content_and_archive_bombs_are_refused():
    with raises("ACTIVE_CONTENT_REFUSED"):
        filetypes.detect("m.pdf", b"%PDF-1.7\n1 0 obj << /S /JavaScript /JS (app.alert(1)) >> endobj", "EVIDENCE")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>"); z.writestr("xl/workbook.xml", "<x/>"); z.writestr("xl/vbaProject.bin", b"x")
    with raises("ACTIVE_CONTENT_REFUSED"):
        filetypes.detect("m.xlsx", b.getvalue(), "EVIDENCE")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>"); z.writestr("xl/workbook.xml", "<x/>"); z.writestr("xl/big.xml", b"0" * (130 * 1024 * 1024))
    with raises("FILE_TOO_COMPLEX"):
        filetypes.detect("bomb.xlsx", b.getvalue(), "EVIDENCE")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("a.txt", "x")
    with raises("UNSUPPORTED_FILE_TYPE"):
        filetypes.detect("notexcel.xlsx", b.getvalue(), "EVIDENCE")


def test_display_names_are_sanitised():
    assert filetypes.safe_display_name("../../etc/passwd") == "passwd"
    assert filetypes.safe_display_name("C:\\Users\\x\\report.csv") == "report.csv"
    assert filetypes.safe_display_name("a\x00b\r\n.txt") == "ab.txt"
    assert filetypes.safe_display_name(".hidden") == "hidden" and filetypes.safe_display_name("") == "upload"
    assert len(filetypes.safe_display_name("x" * 500 + ".txt")) == 120


# ------------------------------------------------------------------ extraction
TEXT = b"""Site daily report 30-09-2026
A2010: welded 1,200 joints cumulative
A1010 - cleared 2 km today; 75% complete
A3010 progress noted, 1500 joints
Nothing to see here.
A9999 reported 3 parsec cumulative
"""


def test_text_extraction_is_strict_about_basis_unit_and_date():
    r = ex.extract(TEXT, "text", "txt", unit_ok=ok)
    by = {c.activity_ref: c for c in r.candidates}
    a = by["A2010"]
    assert a.eligible and a.quantities == [{"qty": D("1200"), "uom": "joints", "basis": "CUMULATIVE", "resource_hint": None}] and a.event_date == date(2026, 9, 30)
    b = by["A1010"]
    assert b.eligible and b.quantities[0]["basis"] == "INCREMENTAL" and b.claimed_pct == D("75")
    assert not by["A3010"].eligible and by["A3010"].problems == ["BASIS_UNKNOWN"]             # ambiguous basis is never guessed
    assert "A9999" not in by or not by["A9999"].eligible
    assert r.method == "TEXT_RULES"


def test_missing_date_makes_a_candidate_ineligible_unless_a_default_is_given():
    t = b"A2010: 100 joints cumulative"
    c = ex.extract(t, "text", "txt", unit_ok=ok).candidates[0]
    assert c.problems == ["DATE_MISSING"]
    c = ex.extract(t, "text", "txt", unit_ok=ok, default_date=date(2026, 9, 1)).candidates[0]
    assert c.eligible and c.event_date == date(2026, 9, 1)


def test_csv_extraction_headers_synonyms_and_row_validation():
    csv_ = (b"Activity ID;Date;Cumulative Qty;Unit;% Complete;Remarks\n"
            b"A2010;2026-09-30;1,200;joints;;spread 2\n"
            b"A1010;30-09-2026;;;60%;\n"
            b"A3010;2026-09-30;abc;joints;;\n"
            b"A2000;2026-09-30;5;parsec;;\n"
            b";2026-09-30;5;km;;\n"
            b"A4000;2026-09-30;;;;\n"
            b"\n")
    r = ex.extract(csv_, "text", "csv", unit_ok=ok)
    got = {c.source_ref: c for c in r.candidates}
    assert r.method == "CSV_TABLE" and len(r.candidates) == 6
    assert got["row 2"].eligible and got["row 2"].quantities[0]["qty"] == D("1200")
    assert got["row 3"].eligible and got["row 3"].claimed_pct == D("60") and got["row 3"].quantities == []
    assert got["row 4"].problems[0].startswith("BAD_VALUE")
    assert got["row 5"].problems == ["UNKNOWN_UNIT"] and "ACTIVITY_MISSING" in got["row 6"].problems and got["row 7"].problems == ["NO_PROGRESS_VALUE"]


def test_generic_quantity_column_needs_an_explicit_basis():
    c = b"Activity,Date,Quantity,UoM,Basis\nA2010,2026-09-30,10,joints,\nA2020,2026-09-30,10,joints,incremental\n"
    r = ex.extract(c, "text", "csv", unit_ok=ok)
    assert r.candidates[0].problems == ["BASIS_UNKNOWN"] and r.candidates[1].eligible and r.candidates[1].quantities[0]["basis"] == "INCREMENTAL"


def test_xlsx_and_pdf_extraction():
    x = xlsx_bytes([["Daily progress"], [None], ["Activity ID", "Date", "Qty Today", "Unit", "Resource"], ["A2010", date(2026, 9, 30), 120, "joints", "WELD_JOINTS"], ["A1010", "2026-09-30", 0.45 * 0, "km", None]])
    r = ex.extract(x, "xlsx", "xlsx", unit_ok=ok)
    assert r.method == "XLSX_TABLE" and r.candidates[0].eligible and r.candidates[0].quantities[0] == {"qty": D("120"), "uom": "joints", "basis": "INCREMENTAL", "resource_hint": "WELD_JOINTS"}
    pdf = pdf_bytes(["Daily report 2026-09-30", "A2010 welded 600 joints cumulative", "A1010 80% complete"])
    r = ex.extract(pdf, "pdf", "pdf", unit_ok=ok)
    assert r.method == "PDF_TEXT_RULES" and r.page_count == 1 and [c.activity_ref for c in r.candidates] == ["A2010", "A1010"] and all(c.eligible for c in r.candidates)
    assert r.candidates[1].claimed_pct == D("80")


def test_failures_return_no_candidates_and_a_reason():
    import fitz
    d = fitz.open(); pg = d.new_page(); pg.draw_rect(fitz.Rect(10, 10, 100, 100)); scanned = d.tobytes()
    for content, fam, ext, code in [(scanned, "pdf", "pdf", "SCANNED_NOT_SUPPORTED"), (png_bytes(), "image", "png", "SCANNED_NOT_SUPPORTED"), (b"just,some,words\n1,2,3\n", "text", "csv", None),
                                    (xlsx_bytes([["x", "y"], [1, 2]]), "xlsx", "xlsx", "NOT_A_PROGRESS_TABLE"), (b"%PDF-1.4 garbage", "pdf", "pdf", "UNREADABLE_PDF")]:
        if code is None:
            assert ex.extract(content, fam, ext, unit_ok=ok).candidates == []
            continue
        with pytest.raises(ex.ExtractionError) as e:
            ex.extract(content, fam, ext, unit_ok=ok)
        assert e.value.code == code


def test_caps_fail_instead_of_returning_partial_results(monkeypatch):
    monkeypatch.setattr(ex, "MAX_ROWS", 5)
    rows = b"Activity ID,Date,Cumulative Qty,Unit\n" + b"".join(b"A20%02d,2026-09-30,1,joints\n" % i for i in range(10))
    with pytest.raises(ex.ExtractionError) as e:
        ex.extract(rows, "text", "csv", unit_ok=ok)
    assert e.value.code == "TOO_MANY_ROWS"
    monkeypatch.setattr(ex, "MAX_PAGES", 1)
    import fitz
    d = fitz.open(); d.new_page().insert_text((40, 60), "A2010 welded 5 joints cumulative 2026-09-30"); d.new_page().insert_text((40, 60), "second page text here")
    with pytest.raises(ex.ExtractionError) as e:
        ex.extract(d.tobytes(), "pdf", "pdf", unit_ok=ok)
    assert e.value.code == "PDF_TOO_LONG"


def test_time_budget_is_enforced():
    rows = b"Activity ID,Date,Cumulative Qty,Unit\n" + b"".join(b"A20%02d,2026-09-30,1,joints\n" % (i % 90) for i in range(1500))
    with pytest.raises(ex.ExtractionError) as e:
        ex.extract(rows, "text", "csv", unit_ok=lambda u: (time.sleep(0.001), True)[1], budget_s=0.2)
    assert e.value.code == "EXTRACTION_TIMEOUT"


def test_extraction_is_offline_and_never_touches_an_llm():
    import inspect
    src = inspect.getsource(ex)
    for forbidden in ("llm_client", "openai", "requests", "httpx", "urllib", "socket", "anthropic", "groq"):
        assert forbidden not in src.lower().replace("no llm", ""), forbidden


# ------------------------------------------------------------------ progress-report formats: DOCX and WebP, legacy Office files
def docx_bytes(lines, extra=None):
    from xml.sax.saxutils import escape
    body = "".join(f"<w:p><w:r><w:t>{escape(l)}</w:t></w:r></w:p>" for l in lines)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>")
        for n, c in (extra or {}).items():
            z.writestr(n, c)
    return b.getvalue()


def webp_bytes():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (8, 8)).save(b, "WEBP"); return b.getvalue()


def test_docx_and_webp_are_detected_by_content_and_must_match_their_extension():
    d = filetypes.detect("report.docx", docx_bytes(["A2000 pipe stringing 40 percent complete"]), "DAILY_REPORT")
    assert (d.ext, d.family) == ("docx", "docx") and d.mime.endswith("wordprocessingml.document")
    w = filetypes.detect("scan.webp", webp_bytes(), "EVIDENCE")
    assert (w.ext, w.family, w.mime) == ("webp", "image", "image/webp")
    with raises("FILE_CONTENT_MISMATCH"):
        filetypes.detect("report.xlsx", docx_bytes(["x"]), "EVIDENCE")           # a Word file renamed .xlsx
    with raises("KIND_TYPE_MISMATCH"):
        filetypes.detect("scan.webp", webp_bytes(), "DAILY_REPORT")             # scans and photographs are evidence, not report kinds
    with raises("CORRUPT_FILE"):
        filetypes.detect("bad.docx", b"PK\x03\x04junk", "EVIDENCE")
    with raises("ACTIVE_CONTENT_REFUSED"):
        filetypes.detect("macro.docx", docx_bytes(["x"], {"word/vbaProject.bin": b"x"}), "EVIDENCE")


def test_legacy_office_files_are_refused_with_the_modern_format_named():
    for name, word in (("old.xls", "XLSX"), ("old.doc", "DOCX")):
        with pytest.raises(Exception) as e:
            filetypes.detect(name, b"\xd0\xcf\x11\xe0" + b"0" * 50, "EVIDENCE")
        assert getattr(e.value, "code", None) == "UNSUPPORTED_FILE_TYPE" and word in str(e.value.message)


def test_docx_text_is_read_in_order_and_feeds_the_report_extractor():
    lines = ["Daily progress report - 12 Aug 2026", "A2010 welding mainline: 120 joints completed", "Weather clear"]
    assert ex.docx_text(docx_bytes(lines)).splitlines() == lines
    out = ex.extract(docx_bytes(lines), "docx", "docx", unit_ok=ok)
    assert out.method == "DOCX_TEXT_RULES"
    with raises("EMPTY_DOCUMENT"):
        ex.extract(docx_bytes(["hi"]), "docx", "docx", unit_ok=ok)
    with raises("UNREADABLE_DOCX"):
        ex.docx_text(b"not a zip")


def test_the_evidence_store_keeps_the_new_extensions():
    assert "webp" in storage.EXTENSIONS and "docx" in storage.EXTENSIONS
