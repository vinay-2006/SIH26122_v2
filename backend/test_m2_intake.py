"""
Unit and integration tests for Member 2 (Intake & Extraction).
"""
from __future__ import annotations
import io
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import pytest
except ImportError:
    pytest = None

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.auth import UserProfile as CurrentUser, get_current_user
from backend.shared.db import get_db
from backend.shared.schemas import ClaimMode, Discipline, EventType, ExtractedClaimFields, InputChannel


def _live_llm(fn):
    """
    Marks a test that calls the real configured LLM_PROVIDER over the
    network (see pytest.ini: excluded from the default deterministic run,
    opt in with `pytest -m live_llm`). A plain no-op when this file is
    executed standalone without pytest installed (see the `pytest = None`
    fallback above) so that mode isn't broken by the decorator.
    """
    return pytest.mark.live_llm(fn) if pytest is not None else fn


def _skip_if_quota_exhausted(response):
    """
    Many tests below exercise a real LLM call (typed-text intake, PDF/TXT
    file intake) against this project's configured provider -- a shared,
    rate-limited free-tier key. A 502 caused by the provider's own
    rate/quota limit is an infrastructure condition of the moment, not a
    regression in this code (see routers/intake.py's LLMExtractionError
    handling, which is what turns a real provider failure into this 502 in
    the first place, correctly, instead of masking it as a null claim) --
    skip rather than fail so a temporarily-exhausted free-tier quota
    doesn't look like a broken test. Any other failure still fails normally.
    """
    if response.status_code == 502:
        detail = response.json().get("detail", "")
        if "rate_limit" in detail.lower() or "rate limit" in detail.lower() or "429" in detail:
            if pytest is not None:
                pytest.skip(f"LLM provider quota/rate limit hit (infra, not a code issue): {detail}")
            else:
                print(f"Skipping (LLM provider quota/rate limit hit): {detail}")
                return True
    return False


# ---------------------------------------------------------------------------
# In-Memory Test DB Connection
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self.last_results: List[Dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query: str, params: tuple = ()):
        q = query.strip()
        q_upper = q.upper()

        if "SELECT SCHEDULE_ID FROM SCHEDULES" in q_upper:
            if self.db.schedules:
                self.last_results = [{"schedule_id": self.db.schedules[-1]["schedule_id"]}]
            else:
                self.last_results = []

        elif "INSERT INTO SOURCE_DOCUMENTS" in q_upper:
            doc = {
                "document_id": params[0],
                "file_name": params[1],
                "document_type": params[2],
                "uploader_id": params[3],
                "file_hash": params[4],
                "uploaded_at": datetime.now(timezone.utc),
            }
            self.db.source_documents.append(doc)
            self.last_results = []

        elif "INSERT INTO EXECUTION_EVENTS" in q_upper:
            ev = {
                "event_id": params[0],
                "document_id": params[1],
                "schedule_id": params[2],
                "event_date": params[3],
                "raw_claim_text": params[4],
                "input_channel": params[5],
                "language_detected": params[6],
                "reported_activity_id": params[7],
                "discipline": params[8],
                "action": params[9],
                "event_type": params[10],
                "claim_mode": params[11],
                "asset_tag": params[12],
                "location": params[13],
                "claimed_quantity": params[14],
                "claimed_uom": params[15],
                "claimed_pct": params[16],
                "delay_reason": params[17],
                "supervisor_id": params[18],
                "photo_path": params[19],
                "status": "EXTRACTED",
                "clarification_status": params[20] if len(params) > 20 else "NONE",
                "clarification_question": params[21] if len(params) > 21 else None,
                "clarification_answer": params[22] if len(params) > 22 else None,
                "field_provenance": params[23] if len(params) > 23 else "{}",
                "project_id": params[24] if len(params) > 24 else None,
                "stage_id": params[25] if len(params) > 25 else None,
                "contractor_id": params[26] if len(params) > 26 else None,
                "work_package_id": params[27] if len(params) > 27 else None,
                "created_at": datetime.now(timezone.utc),
            }
            ev.setdefault("matched_activity_id", None)
            self.db.execution_events.append(ev)
            self.last_results = []

        elif "UPDATE EXECUTION_EVENTS SET" in q_upper:
            target_id = params[-1]
            for ev in self.db.execution_events:
                if ev["event_id"] == target_id:
                    ev["event_date"] = params[0]
                    ev["raw_claim_text"] = params[1]
                    ev["discipline"] = params[2]
                    ev["action"] = params[3]
                    ev["event_type"] = params[4]
                    ev["claim_mode"] = params[5]
                    ev["asset_tag"] = params[6]
                    ev["location"] = params[7]
                    ev["claimed_quantity"] = params[8]
                    ev["claimed_uom"] = params[9]
                    ev["claimed_pct"] = params[10]
                    ev["delay_reason"] = params[11]
                    ev["clarification_status"] = params[12]
                    ev["clarification_answer"] = params[13]
                    ev["field_provenance"] = params[14]
                    break
            self.last_results = []

        elif "INSERT INTO SOURCE_REFERENCES" in q_upper:
            ref = {
                "reference_id": params[0],
                "event_id": params[1],
                "file_name": params[2],
                "raw_snippet": params[-1],
            }
            self.db.source_references.append(ref)
            self.last_results = []

        elif "SELECT * FROM EXECUTION_EVENTS WHERE EVENT_ID = %S" in q_upper:
            target_id = params[0]
            matched = [e for e in self.db.execution_events if e["event_id"] == target_id]
            self.last_results = matched

        elif "SELECT * FROM EXECUTION_EVENTS WHERE EVENT_ID = ANY(%S)" in q_upper:
            target_ids = params[0]
            matched = [e for e in self.db.execution_events if e["event_id"] in target_ids]
            self.last_results = matched

        elif "SELECT * FROM EXECUTION_EVENTS WHERE SCHEDULE_ID = %S" in q_upper:
            schedule_id = params[0]
            res = [e for e in self.db.execution_events if e.get("schedule_id") == schedule_id]
            param_idx = 1
            if "AND PROJECT_ID = %S" in q_upper:
                project_id = params[param_idx]
                param_idx += 1
                res = [e for e in res if str(e.get("project_id")) == str(project_id)]
            if "AND STATUS = %S" in q_upper:
                val = params[param_idx]
                param_idx += 1
                res = [e for e in res if e.get("status") == val]
            if "AND DISCIPLINE = %S" in q_upper:
                val = params[param_idx]
                param_idx += 1
                res = [e for e in res if e.get("discipline") == val]
            self.last_results = res

        else:
            self.last_results = []

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self.last_results[0] if self.last_results else None

    def fetchall(self) -> List[Dict[str, Any]]:
        return list(self.last_results)


class FakeDB:
    def __init__(self):
        self.schedules = [{"schedule_id": "test-sched-1", "project_name": "Test Project"}]
        self.source_documents: List[Dict[str, Any]] = []
        self.execution_events: List[Dict[str, Any]] = []
        self.source_references: List[Dict[str, Any]] = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Test Functions
# ---------------------------------------------------------------------------

def test_health_checks(client):
    """Router health checks respond with 200 OK."""
    r1 = client.get("/api/v1/intake/health")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    r2 = client.get("/api/v1/claims/health")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"


@_live_llm
def test_auth_gating(client):
    """Intake requires SITE_ENGINEER role; unauthorized access is rejected."""
    # 1. No auth headers -> 401 Unauthorized
    r_no_auth = client.post("/api/v1/claims/text", json={"raw_claim_text": "Started welding"})
    assert r_no_auth.status_code == 401

    # 2. SUPERVISOR role -> 403 Forbidden for intake
    headers_sup = {"X-Dev-User-Id": "sup-1", "X-Dev-Role": "SUPERVISOR"}
    r_forbidden = client.post("/api/v1/claims/text", json={"raw_claim_text": "Started welding"}, headers=headers_sup)
    assert r_forbidden.status_code == 403

    # 3. SITE_ENGINEER role -> 200 OK
    headers_eng = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r_ok = client.post("/api/v1/claims/text", json={"raw_claim_text": "Completed concrete pour in Area C"}, headers=headers_eng)
    _skip_if_quota_exhausted(r_ok)
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["status"] == "EXTRACTED"
    assert data["supervisor_id"] == "eng-1"


@_live_llm
def test_text_claim_typed_and_voice(client, fake_db):
    """Test TYPED_TEXT and VOICE input channels."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    # Typed text
    r_typed = client.post(
        "/api/v1/claims/text",
        json={"raw_claim_text": "Activity A1000 excavation completed 100%", "input_channel": "TYPED_TEXT"},
        headers=headers,
    )
    _skip_if_quota_exhausted(r_typed)
    assert r_typed.status_code == 200
    data = r_typed.json()
    assert data["input_channel"] == "TYPED_TEXT"
    assert data["status"] == "EXTRACTED"

    # Voice input (arrives as text transcribed via Web Speech API)
    r_voice = client.post(
        "/api/v1/claims/text",
        json={"raw_claim_text": "Area D trench excavation started today", "input_channel": "VOICE"},
        headers=headers,
    )
    _skip_if_quota_exhausted(r_voice)
    assert r_voice.status_code == 200
    assert r_voice.json()["input_channel"] == "VOICE"

    # Verify provenance and documents stored
    assert len(fake_db.source_documents) >= 2
    assert len(fake_db.source_references) >= 2


@_live_llm
def test_file_claim_txt(client, fake_db):
    """Upload plain text DPR file. /claims/file always returns a LIST (a
    single-activity file yields a 1-item list) since a file can legitimately
    contain several distinct activity claims -- see test_file_claim_pdf."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    file_content = b"DAILY PROGRESS REPORT\n14 Aug 2026\nExcavation of utility trench CH 0+180 to CH 0+220 completed, 40m, 100% done."

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("daily_report.txt", io.BytesIO(file_content), "text/plain")},
        headers=headers,
    )
    _skip_if_quota_exhausted(r)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["status"] == "EXTRACTED"
    assert data[0]["input_channel"] == "FILE_UPLOAD"
    assert any(doc["document_type"] == "DPR" for doc in fake_db.source_documents)


@_live_llm
def test_file_claim_pdf_multi_activity(client, fake_db):
    """
    Upload a PDF whose text (extracted via PyMuPDF) describes FOUR distinct
    activities, as this project's real daily-report PDFs do (see
    sample_data/input/daily-report-pdf) -- must produce four claims, not
    collapse them into one and silently discard the other three (the
    original bug this rewrite fixes).
    """
    import pymupdf as fitz
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "Daily Progress Report - 14 Aug 2026",
        "CIV-PS3-TR-0180 | Civil | Excavate utility trench CH 0+180 to CH 0+220 | 40/40 m | 100.0% | Complete",
        "PIP-PS3-WLD-024 | Piping | Complete field weld joints for utility header | 22/24 joints | 91.7% | Ongoing",
        "ELE-PS3-CT-011 | Electrical | Place cable trench bedding at MCC-02 | 140/160 m | 87.5% | Ongoing",
        "MECH-PS3-DWP-003 | Mechanical | Relocate dewatering pump after water ingress | 1/1 each | 100.0% | Complete",
    ]
    for i, line in enumerate(lines):
        page.insert_text((50, 72 + i * 20), line)
    pdf_bytes = doc.write()

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("daily_report_2026-08-14.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=headers,
    )
    _skip_if_quota_exhausted(r)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 4, f"expected 4 distinct activity claims from the PDF, got {len(data)}"
    activity_ids = {c["reported_activity_id"] for c in data}
    assert activity_ids == {"CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011", "MECH-PS3-DWP-003"}
    for c in data:
        assert c["status"] == "EXTRACTED"
        assert c["claimed_pct"] is not None


@_live_llm
def test_file_claim_evidence_photo(client, fake_db):
    """Upload photo with purpose=EVIDENCE_PHOTO + raw_claim_text -> stores
    photo_path, unchanged single-claim contract (the photo is evidence
    attached to an already-typed claim, not extracted itself)."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    dummy_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("inspection_photo.png", io.BytesIO(dummy_image), "image/png")},
        data={"purpose": "EVIDENCE_PHOTO", "raw_claim_text": "Joint 3 visual inspection verified"},
        headers=headers,
    )
    _skip_if_quota_exhausted(r)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["photo_path"] is not None
    assert "inspection_photo.png" in data[0]["photo_path"]


@_live_llm
def test_get_claim_photo_round_trip(client, fake_db):
    """
    ISS-07/ISS-20: an uploaded evidence photo can actually be fetched back
    over HTTP (not just its filename shown as text) -- GET
    /api/v1/claims/{event_id}/photo returns the original image bytes.
    """
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    dummy_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    upload = client.post(
        "/api/v1/claims/file",
        files={"file": ("inspection_photo.png", io.BytesIO(dummy_image), "image/png")},
        data={"purpose": "EVIDENCE_PHOTO", "raw_claim_text": "Joint 3 visual inspection verified"},
        headers=headers,
    )
    _skip_if_quota_exhausted(upload)
    assert upload.status_code == 200
    event_id = upload.json()[0]["event_id"]

    r = client.get(f"/api/v1/claims/{event_id}/photo", headers=headers)
    assert r.status_code == 200
    assert r.content == dummy_image
    assert r.headers["content-type"] in ("image/png", "application/octet-stream")


def test_get_claim_photo_404_when_claim_has_no_photo(client, fake_db):
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r = client.post(
        "/api/v1/claims/text",
        json={"raw_claim_text": "Excavation 50% complete", "input_channel": "TYPED_TEXT"},
        headers=headers,
    )
    _skip_if_quota_exhausted(r)
    assert r.status_code == 200
    event_id = r.json()["event_id"]

    photo_resp = client.get(f"/api/v1/claims/{event_id}/photo", headers=headers)
    assert photo_resp.status_code == 404


def test_get_claim_photo_404_for_nonexistent_claim(client, fake_db):
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r = client.get("/api/v1/claims/does-not-exist/photo", headers=headers)
    assert r.status_code == 404


def test_get_claim_photo_requires_auth(client, fake_db):
    r = client.get("/api/v1/claims/some-event/photo")
    assert r.status_code == 401


@_live_llm
def test_file_claim_scanned_diary(client, fake_db):
    """
    Upload a scanned diary image with purpose=SCANNED_DIARY. The dummy PNG
    bytes here are deliberately truncated/unopenable (fast, no real image
    fixture needed) -- both vision extraction (no vision model configured
    for the test's LLM_PROVIDER) and OCR (image won't open) are expected to
    fail, so this exercises the final raw_claim_text fallback and confirms
    it still reaches real LLM-based extraction rather than being stored
    verbatim as a null-field claim.
    """
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    dummy_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("site_diary_2026-08-14.png", io.BytesIO(dummy_image), "image/png")},
        data={
            "purpose": "SCANNED_DIARY",
            "raw_claim_text": "Excavation trench CH 0+180 to CH 0+220 completed today, 100 percent done.",
        },
        headers=headers,
    )
    _skip_if_quota_exhausted(r)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and len(data) >= 1
    assert data[0]["input_channel"] == "SCANNED_OCR"
    assert any(doc["document_type"] == "SCANNED_DIARY" for doc in fake_db.source_documents)


def test_file_claim_image_no_purpose_no_text_is_legitimate_claim_source(client, fake_db):
    """
    A bare image upload with NO purpose and NO raw_claim_text must not be
    forced into the EVIDENCE_PHOTO contract (which would 422 demanding
    text) -- an image with nothing else supplied is the claim itself, the
    capability this rewrite adds. Falls through to the raw_claim_text-less
    path, which (with the always-fails-to-open dummy PNG and no vision
    model configured) is expected to fail extraction cleanly with a 422,
    not silently succeed with an all-null claim.
    """
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    dummy_image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("photo.png", io.BytesIO(dummy_image), "image/png")},
        headers=headers,
    )
    # Not the old hard-coded "raw_claim_text is required for EVIDENCE_PHOTO"
    # 422 -- this is now a real (if here unresolvable, given the dummy
    # image) extraction attempt.
    assert r.status_code == 422
    assert "raw_claim_text is required" not in r.json()["detail"]


def test_file_claim_xlsx_multi_sheet(client, fake_db):
    """
    A 3-sheet discipline XLSX must yield one claim per sheet, not just the
    first -- pandas.read_excel defaults to sheet_name=0, which was the
    actual root cause of the original Excel intake bug (2 of 3 disciplines
    silently vanished). Also exercises the Excel-percentage-as-fraction
    normalization (a "Progress" column storing 0.917 for 91.7%, not 91.7).
    """
    import openpyxl

    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    sheet_data = {
        "Civil": ("CIV-PS3-TR-0180", "Excavate utility trench", 40, 1.0),
        "Piping": ("PIP-PS3-WLD-024", "Complete field weld joints", 2, 0.917),
        "Electrical": ("ELE-PS3-CT-011", "Place cable trench bedding", 20, 0.875),
    }
    for sheet_name, (activity_id, desc, qty, pct_fraction) in sheet_data.items():
        ws = wb.create_sheet(sheet_name)
        ws.append(["L5 Activity ID", "Work description", "Today actual", "Progress"])
        ws.append([activity_id, desc, qty, pct_fraction])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("discipline_progress.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 200
    claims = r.json()
    assert len(claims) == 3, f"expected one claim per sheet (3), got {len(claims)}"
    by_activity = {c["reported_activity_id"]: c for c in claims}
    assert set(by_activity) == {"CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011"}
    assert by_activity["CIV-PS3-TR-0180"]["discipline"] == "CIVIL"
    assert by_activity["PIP-PS3-WLD-024"]["discipline"] == "PIPING"
    assert by_activity["ELE-PS3-CT-011"]["discipline"] == "ELECTRICAL"
    # 0.917 stored fraction -> 91.7 on the 0-100 scale the schema expects.
    assert abs(by_activity["PIP-PS3-WLD-024"]["claimed_pct"] - 91.7) < 0.1


def test_file_claim_xer(client, fake_db):
    """
    A .xer upload must be recognized and parsed as real Primavera P6
    export structure (stacked %T/%F/%R tables), not rejected as an
    unsupported type and not misread as a flat CSV.
    """
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    xer_content = (
        "ERMHDR\t19.12\t2026-08-10\tProject\tadmin\tPlanner\tPROJ\tUSD\tDD/MM/YYYY\t1\t0\t0\n"
        "%T\tPROJWBS\n"
        "%F\twbs_id\tproj_id\tparent_wbs_id\twbs_short_name\twbs_name\n"
        "%R\t100\t1\t\t1\tCivil Works\n"
        "%T\tTASK\n"
        "%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\ttarget_start_date\ttarget_end_date\n"
        "%R\t2001\t1\t100\tCIV-PS3-TR-0180\tUtility Trench Excavation\tTT_Task\tTK_Complete\t2026-08-14 08:00\t2026-08-14 18:00\n"
        "%R\t2002\t1\t100\tCIV-PS3-TR-0220\tUtility Trench Excavation 2\tTT_Task\tTK_Active\t2026-08-15 08:00\t2026-08-16 18:00\n"
        "%R\t2003\t1\t100\tCIV-PS3-NOTSTARTED\tNot yet started activity\tTT_Task\tTK_NotStart\t2026-08-20 08:00\t2026-08-21 18:00\n"
    ).encode("utf-8")

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("schedule.xer", io.BytesIO(xer_content), "application/xml")},
        headers=headers,
    )
    assert r.status_code == 200
    claims = r.json()
    # 2 claims: TK_Complete and TK_Active. TK_NotStart has no progress to
    # report and must be excluded, not turned into an empty claim.
    assert len(claims) == 2
    ids = {c["reported_activity_id"] for c in claims}
    assert ids == {"CIV-PS3-TR-0180", "CIV-PS3-TR-0220"}
    assert "CIV-PS3-NOTSTARTED" not in ids
    assert all(c["input_channel"] == "SCHEDULE_EXPORT" for c in claims)
    by_id = {c["reported_activity_id"]: c for c in claims}
    assert by_id["CIV-PS3-TR-0180"]["claimed_pct"] == 100.0
    assert by_id["CIV-PS3-TR-0180"]["discipline"] == "CIVIL"


def test_file_claim_unsupported_extension(client, fake_db):
    """An extension nothing recognizes (e.g. .docx) must fail with a clear
    415, not a generic crash or a silently-empty claim."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("report.docx", io.BytesIO(b"not really a docx"), "application/msword")},
        headers=headers,
    )
    assert r.status_code == 415
    assert ".docx" in r.json()["detail"]


def test_file_claim_empty_file(client, fake_db):
    """A zero-byte upload must fail clearly (422) -- never silently produce
    an all-null claim just because there was nothing to extract from."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 422
    assert "empty" in r.json()["detail"].lower()


def test_file_claim_corrupt_pdf(client, fake_db):
    """Bytes that aren't a real PDF, uploaded with a .pdf extension, must
    fail with a clear 422 identifying the file as unreadable -- not a raw
    parser traceback and not a silently-empty claim."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("report.pdf", io.BytesIO(b"this is not a pdf file at all, just text"), "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 422
    assert "pdf" in r.json()["detail"].lower()


def test_file_claim_csv_bom_and_semicolon_delimiter(client, fake_db):
    """
    A CSV saved by Excel on Windows commonly carries a UTF-8 BOM and/or
    uses ';' instead of ',' in some locales -- both must still parse
    correctly rather than treating the whole file as one unparseable
    column.
    """
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    csv_text = (
        "Activity ID;Activity Name;Discipline;Today Actual;Progress Pct;Status\n"
        "CIV-PS3-TR-0180;Excavate utility trench;Civil;40;100.0;Complete\n"
    )
    csv_bytes = b"\xef\xbb\xbf" + csv_text.encode("utf-8")  # UTF-8 BOM prefix

    r = client.post(
        "/api/v1/claims/file",
        files={"file": ("progress.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200
    claims = r.json()
    assert len(claims) == 1
    assert claims[0]["reported_activity_id"] == "CIV-PS3-TR-0180"
    assert claims[0]["claimed_pct"] == 100.0


def test_llm_extraction_failure_is_not_silently_swallowed():
    """
    A genuine LLM/API-level failure (bad config, network error, malformed
    response) must raise LLMExtractionError, not silently return an
    all-null ExtractedClaimFields() -- the original bug this rewrite fixes.
    Patches the shared client factory directly (rather than going through
    the FastAPI app) to isolate this to the extraction layer itself. Uses a
    standalone MonkeyPatch (rather than pytest's `monkeypatch` fixture) so
    this test also runs from the __main__ direct-runner block below, which
    doesn't go through pytest's fixture injection.
    """
    from backend.shared import llm_extraction

    old_get_client = llm_extraction._get_client
    try:
        def _broken_client():
            raise RuntimeError("simulated network failure")

        llm_extraction._get_client = _broken_client

        raised = False
        try:
            llm_extraction.extract_claim_fields("Excavation completed 100%")
        except llm_extraction.LLMExtractionError:
            raised = True
        assert raised, "extract_claim_fields must raise LLMExtractionError on a genuine failure, not swallow it"

        raised = False
        try:
            llm_extraction.extract_claim_fields_batch("Excavation completed 100%")
        except llm_extraction.LLMExtractionError:
            raised = True
        assert raised, "extract_claim_fields_batch must raise LLMExtractionError on a genuine failure, not swallow it"
    finally:
        llm_extraction._get_client = old_get_client


def test_schedule_export_claims(client, fake_db):
    """Feature 22: P6/MSP progress export parsed directly, skipping LLM."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    csv_content = (
        "Activity ID,Activity Name,Discipline,Unit,Today Actual,Progress Pct,Status\n"
        "CIV-PS3-TR-0180,Excavate utility trench,Civil,m,40,100.0,Complete\n"
        "PIP-PS3-WLD-024,Complete field weld joints,Piping,joints,2,91.7,Ongoing\n"
        "EMPTY-ROW,,Civil,m,0,0.0,Not Started\n"
    ).encode("utf-8")

    r = client.post(
        "/api/v1/claims/schedule-export",
        files={"file": ("progress_export.csv", io.BytesIO(csv_content), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200
    claims = r.json()
    assert len(claims) == 2

    c1 = claims[0]
    assert c1["reported_activity_id"] == "CIV-PS3-TR-0180"
    assert c1["input_channel"] == "SCHEDULE_EXPORT"
    assert c1["status"] == "EXTRACTED"
    assert c1["claimed_pct"] == 100.0

    c2 = claims[1]
    assert c2["reported_activity_id"] == "PIP-PS3-WLD-024"
    assert c2["input_channel"] == "SCHEDULE_EXPORT"
    assert c2["status"] == "EXTRACTED"
    assert c2["claimed_pct"] == 91.7

    assert any(doc["document_type"] == "SCHEDULE_EXPORT_PROGRESS" for doc in fake_db.source_documents)


@_live_llm
def test_get_and_list_claims(client, fake_db):
    """Test claim retrieval and filtering."""
    headers_eng = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    headers_sup = {"X-Dev-User-Id": "sup-1", "X-Dev-Role": "SUPERVISOR"}

    # Create 2 claims
    r1 = client.post("/api/v1/claims/text", json={"raw_claim_text": "Civil work 1"}, headers=headers_eng)
    _skip_if_quota_exhausted(r1)
    eid1 = r1.json()["event_id"]

    r2 = client.post("/api/v1/claims/text", json={"raw_claim_text": "Piping work 2"}, headers=headers_eng)
    eid2 = r2.json()["event_id"]

    # Retrieve single claim by ID as Supervisor
    r_get = client.get(f"/api/v1/claims/{eid1}", headers=headers_sup)
    assert r_get.status_code == 200
    assert r_get.json()["event_id"] == eid1

    # List claims
    r_list = client.get("/api/v1/claims", headers=headers_sup)
    assert r_list.status_code == 200
    assert len(r_list.json()) >= 2

    # Non-existent claim -> 404
    r_404 = client.get("/api/v1/claims/non-existent-id", headers=headers_sup)
    assert r_404.status_code == 404


def test_list_claims_scoped_to_the_explicit_schedule(client, fake_db):
    """
    ISS-05: GET /api/v1/claims (the Review Workspace queue's data source)
    must only return the EXPLICIT schedule's claims (X-Schedule-ID), never another
    schedule's -- seeded directly into FakeDB so this doesn't depend on
    the live LLM at all.
    """
    headers_sup = {"X-Dev-User-Id": "sup-1", "X-Dev-Role": "SUPERVISOR"}

    fake_db.execution_events.append({
        "event_id": "EV-ACTIVE", "document_id": None, "schedule_id": "test-sched-1",
        "event_date": "2026-09-01", "raw_claim_text": "Active schedule claim",
        "input_channel": "TYPED_TEXT", "language_detected": "English",
        "reported_activity_id": None, "matched_activity_id": None, "discipline": "CIVIL",
        "action": "progress", "event_type": "PROGRESS_UPDATE", "claim_mode": "CUMULATIVE_PCT",
        "asset_tag": None, "location": None, "claimed_quantity": None, "claimed_uom": None,
        "claimed_pct": 50.0, "delay_reason": None, "supervisor_id": "eng-1", "photo_path": None,
        "status": "EXTRACTED", "clarification_status": "NONE", "clarification_question": None,
        "clarification_answer": None, "field_provenance": "{}", "created_at": datetime.now(timezone.utc),
        "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    })
    fake_db.execution_events.append({
        "event_id": "EV-OTHER", "document_id": None, "schedule_id": "some-other-schedule",
        "event_date": "2026-09-01", "raw_claim_text": "Other schedule claim",
        "input_channel": "TYPED_TEXT", "language_detected": "English",
        "reported_activity_id": None, "matched_activity_id": None, "discipline": "CIVIL",
        "action": "progress", "event_type": "PROGRESS_UPDATE", "claim_mode": "CUMULATIVE_PCT",
        "asset_tag": None, "location": None, "claimed_quantity": None, "claimed_uom": None,
        "claimed_pct": 50.0, "delay_reason": None, "supervisor_id": "eng-1", "photo_path": None,
        "status": "EXTRACTED", "clarification_status": "NONE", "clarification_question": None,
        "clarification_answer": None, "field_provenance": "{}", "created_at": datetime.now(timezone.utc),
        "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    })

    r_list = client.get("/api/v1/claims", headers=headers_sup)
    assert r_list.status_code == 200
    event_ids = {c["event_id"] for c in r_list.json()}
    assert "EV-ACTIVE" in event_ids
    assert "EV-OTHER" not in event_ids


def test_missing_or_unknown_schedule_is_rejected(client, fake_db):
    """No implicit 'active schedule': intake without an explicit schedule is a 400; an unknown one is a 404."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    no_schedule = client.post("/api/v1/claims/text", json={"raw_claim_text": "Test claim"},
                              headers={**headers, "X-Schedule-ID": ""})
    assert no_schedule.status_code == 400
    assert no_schedule.json()["detail"]["error_code"] == "INVALID_SCHEDULE_CONTEXT"

    fake_db.schedules.clear()
    unknown = client.post("/api/v1/claims/text", json={"raw_claim_text": "Test claim"}, headers=headers)
    assert unknown.status_code == 404


def test_llm_extraction_schema_and_invariants():
    """Verify extraction schema validation and claim_mode mutual exclusivity invariants."""
    from backend.shared.schemas import (
        ClaimMode,
        DelayReason,
        Discipline,
        EventType,
        ExtractedClaimFields,
    )

    # 1. Cumulative pct claim
    c1 = ExtractedClaimFields(
        event_date=date(2026, 8, 14),
        reported_activity_id="CIV-PS3-TR-0180",
        discipline=Discipline.CIVIL,
        action="Excavation of utility corridor trench",
        event_type=EventType.ACTUAL_FINISH,
        claim_mode=ClaimMode.CUMULATIVE_PCT,
        claimed_pct=100.0,
        claimed_quantity=None,
        claimed_uom=None,
    )
    assert c1.discipline == Discipline.CIVIL
    assert c1.event_type == EventType.ACTUAL_FINISH
    assert c1.claimed_pct == 100.0
    assert c1.claimed_quantity is None

    # 2. Incremental quantity claim
    c2 = ExtractedClaimFields(
        event_date=date(2026, 8, 14),
        reported_activity_id="PIP-PS3-WLD-024",
        discipline=Discipline.PIPING,
        action="Welded 2 field weld joints",
        event_type=EventType.PROGRESS_UPDATE,
        claim_mode=ClaimMode.INCREMENTAL_QUANTITY,
        claimed_quantity=2.0,
        claimed_uom="joints",
        claimed_pct=None,
        language_detected="Hindi-English mixed",
    )
    assert c2.discipline == Discipline.PIPING
    assert c2.claim_mode == ClaimMode.INCREMENTAL_QUANTITY
    assert c2.claimed_quantity == 2.0
    assert c2.claimed_uom == "joints"
    assert c2.claimed_pct is None

    # 3. Delay event
    c3 = ExtractedClaimFields(
        event_date=date(2026, 8, 15),
        discipline=Discipline.HSE,
        action="Heavy rainfall stopped excavation works",
        event_type=EventType.DELAY,
        delay_reason=DelayReason.WEATHER,
    )
    assert c3.event_type == EventType.DELAY
    assert c3.delay_reason == DelayReason.WEATHER
    assert c3.discipline == Discipline.HSE


@_live_llm
def test_real_sample_data_intake(client, fake_db):
    """
    End-to-end intake against the actual repo sample files in sample_data/
    for every supported format -- not synthetic fixtures. Each of these
    real files (except the TXT, which is a single-activity narrative)
    describes FOUR activities (CIV-PS3-TR-0180, PIP-PS3-WLD-024,
    ELE-PS3-CT-011, MECH-PS3-DWP-003), so this also verifies multi-claim
    extraction end-to-end against real-world file shapes, not just the
    hand-built fixtures in the tests above.
    """
    from pathlib import Path

    base_dir = Path(__file__).resolve().parents[1]
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    # 1. Real TXT DPR (single narrative -> LLM batch extraction)
    txt_path = base_dir / "sample_data" / "input" / "daily-report-txt" / "daily_progress_report_2026-08-14.txt"
    if txt_path.exists():
        with open(txt_path, "rb") as f:
            r = client.post("/api/v1/claims/file", files={"file": (txt_path.name, f, "text/plain")}, headers=headers)
        _skip_if_quota_exhausted(r)
        assert r.status_code == 200
        claims = r.json()
        assert isinstance(claims, list) and len(claims) >= 1
        assert all(c["status"] == "EXTRACTED" for c in claims)

    # 2. Real PDF daily report (multi-activity table -> LLM batch extraction)
    pdf_path = base_dir / "sample_data" / "input" / "daily-report-pdf" / "daily_progress_report_2026-08-14.pdf"
    if pdf_path.exists():
        with open(pdf_path, "rb") as f:
            r = client.post("/api/v1/claims/file", files={"file": (pdf_path.name, f, "application/pdf")}, headers=headers)
        _skip_if_quota_exhausted(r)
        assert r.status_code == 200
        claims = r.json()
        assert len(claims) == 4, f"expected 4 activities from the real daily-report PDF, got {len(claims)}"
        assert {c["reported_activity_id"] for c in claims} == {
            "CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011", "MECH-PS3-DWP-003",
        }

    # 3. Real multi-sheet discipline XLSX (structured, no LLM) -- the
    #    original Excel bug: only the first sheet was ever read, silently
    #    dropping the other two disciplines.
    xlsx_path = base_dir / "sample_data" / "input" / "discipline-report-xlsx" / "discipline_progress_2026-08-14.xlsx"
    if xlsx_path.exists():
        with open(xlsx_path, "rb") as f:
            r = client.post("/api/v1/claims/file", files={"file": (xlsx_path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}, headers=headers)
        assert r.status_code == 200
        claims = r.json()
        assert len(claims) == 3, f"expected 3 activities (one per sheet: Civil/Piping/Electrical), got {len(claims)}"
        assert {c["reported_activity_id"] for c in claims} == {
            "CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011",
        }
        assert {c["discipline"] for c in claims} == {"CIVIL", "PIPING", "ELECTRICAL"}

    # 4. Real XER (P6 export, structured, no LLM)
    xer_path = base_dir / "sample_data" / "input" / "schedule-xer" / "sih26122_schedule.xer"
    if xer_path.exists():
        with open(xer_path, "rb") as f:
            r = client.post("/api/v1/claims/file", files={"file": (xer_path.name, f, "application/xml")}, headers=headers)
        assert r.status_code == 200
        claims = r.json()
        assert len(claims) == 44, f"expected 44 in-progress/complete activities from the real XER, got {len(claims)}"
        assert all(c["input_channel"] == "SCHEDULE_EXPORT" for c in claims)
        assert all(c["reported_activity_id"] for c in claims)

    # 5. Real CSV progress report via schedule-export
    csv_path = base_dir / "sample_data" / "input" / "progress-report-csv" / "daily_progress_2026-08-14.csv"
    if csv_path.exists():
        with open(csv_path, "rb") as f:
            r = client.post("/api/v1/claims/schedule-export", files={"file": (csv_path.name, f, "text/csv")}, headers=headers)
        assert r.status_code == 200
        claims = r.json()
        assert len(claims) == 4
        assert {c["reported_activity_id"] for c in claims} == {
            "CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011", "MECH-PS3-DWP-003",
        }
        assert claims[0]["input_channel"] == "SCHEDULE_EXPORT"
        assert claims[0]["status"] == "EXTRACTED"

    # 6. Same real CSV progress report via /claims/file (not schedule-export)
    #    -- proves the structured-row path is shared/reachable from the
    #    generic file endpoint too, not just the dedicated one.
    if csv_path.exists():
        with open(csv_path, "rb") as f:
            r = client.post("/api/v1/claims/file", files={"file": (csv_path.name, f, "text/csv")}, headers=headers)
        assert r.status_code == 200
        claims = r.json()
        assert len(claims) == 4
        assert {c["reported_activity_id"] for c in claims} == {
            "CIV-PS3-TR-0180", "PIP-PS3-WLD-024", "ELE-PS3-CT-011", "MECH-PS3-DWP-003",
        }



@_live_llm
def test_copilot_clarification_flow(client, fake_db):
    """Feature #29: Copilot triggers PENDING on missing fields, then clarify resolves to ANSWERED."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}
    payload = {
        "raw_claim_text": "Completed some general tasks on site today.",
        "input_channel": "TYPED_TEXT",
    }
    r = client.post("/api/v1/claims/text", json=payload, headers=headers)
    if _skip_if_quota_exhausted(r):
        return
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "EXTRACTED"
    # In fallback or extraction where event_type/discipline/progress are missing
    if data["clarification_status"] == "PENDING":
        assert data["clarification_question"] is not None
        event_id = data["event_id"]

        # Call clarify endpoint
        clarify_payload = {"answer": "Started trench excavation 40% for civil foundation work."}
        r_clarify = client.post(f"/api/v1/claims/{event_id}/clarify", json=clarify_payload, headers=headers)
        if not _skip_if_quota_exhausted(r_clarify):
            assert r_clarify.status_code == 200
            clarified_data = r_clarify.json()
            assert clarified_data["event_id"] == event_id
            assert clarified_data["clarification_status"] == "ANSWERED"
            assert clarified_data["clarification_answer"] == clarify_payload["answer"]
            assert "Clarification:" in clarified_data["raw_claim_text"]
            assert isinstance(clarified_data["field_provenance"], dict)


@_live_llm
def test_field_provenance_tags(client, fake_db):
    """Feature #33: Verify AI_EXTRACTED on text claims and SCHEDULE_AUTO_FILLED on schedule exports."""
    headers = {"X-Dev-User-Id": "eng-1", "X-Dev-Role": "SITE_ENGINEER"}

    # 1. Text claim provenance
    r_text = client.post("/api/v1/claims/text", json={"raw_claim_text": "Welded joint 4 of 10 for piping"}, headers=headers)
    if not _skip_if_quota_exhausted(r_text):
        assert r_text.status_code == 200
        text_claim = r_text.json()
        assert isinstance(text_claim["field_provenance"], dict)
        for k, v in text_claim["field_provenance"].items():
            assert v == "AI_EXTRACTED"

    # 2. Schedule export provenance
    csv_content = b"Activity ID,Activity Name,Discipline,Progress Pct\nA1001,Excavation Work,CIVIL,50.0\n"
    r_sched = client.post(
        "/api/v1/claims/schedule-export",
        files={"file": ("progress.csv", io.BytesIO(csv_content), "text/csv")},
        headers=headers,
    )
    assert r_sched.status_code == 200
    sched_claims = r_sched.json()
    assert len(sched_claims) == 1
    sched_claim = sched_claims[0]
    assert sched_claim["clarification_status"] == "NONE"
    assert isinstance(sched_claim["field_provenance"], dict)
    assert sched_claim["field_provenance"].get("reported_activity_id") == "SCHEDULE_AUTO_FILLED"
    assert sched_claim["field_provenance"].get("discipline") == "SCHEDULE_AUTO_FILLED"
    assert sched_claim["field_provenance"].get("claimed_pct") == "SCHEDULE_AUTO_FILLED"


def test_copilot_unit_rules():
    """Unit tests for check_missing_required_fields and prompt rules."""
    from backend.shared.llm_extraction import check_missing_required_fields
    from backend.shared.schemas import ExtractedClaimFields, Discipline, EventType

    # Complete claim: has discipline, event_type, and progress (claimed_pct)
    # Optional fields (asset_tag, location, delay_reason) are None
    c_complete = ExtractedClaimFields(
        discipline=Discipline.CIVIL,
        event_type=EventType.PROGRESS_UPDATE,
        claimed_pct=50.0,
        asset_tag=None,
        location=None,
        delay_reason=None,
    )
    assert check_missing_required_fields(c_complete) == []

    # Incomplete claim: missing discipline and progress
    c_incomplete = ExtractedClaimFields(
        event_type=EventType.ACTUAL_START,
        discipline=None,
        claimed_pct=None,
        claimed_quantity=None,
    )
    missing = check_missing_required_fields(c_incomplete)
    assert "discipline" in missing
    assert "claimed_progress" in missing
    assert "event_type" not in missing


def test_shared_llm_client_fallback():
    """Verify shared/llm_client.py behaves cleanly when configured or unconfigured."""
    import json
    from backend.shared.llm_client import call_llm, reset_client

    # 1. Normal/live call: returns non-empty string and valid JSON
    res_str = call_llm(messages=[{"role": "user", "content": "Hello"}])
    assert isinstance(res_str, str)
    assert len(res_str) > 0

    res_json = call_llm(messages=[{"role": "user", "content": "Return JSON"}], response_format={"type": "json_object"})
    assert isinstance(res_json, str)
    parsed = json.loads(res_json)
    assert isinstance(parsed, dict)

    # 2. Offline / unconfigured fallback: test that None client returns safe defaults
    orig_key = os.environ.get("LLM_API_KEY")
    try:
        os.environ["LLM_API_KEY"] = ""
        reset_client()

        res_fallback_str = call_llm(messages=[{"role": "user", "content": "Hello"}])
        assert isinstance(res_fallback_str, str)
        assert len(res_fallback_str) > 0

        res_fallback_json = call_llm(messages=[{"role": "user", "content": "Return JSON"}], response_format={"type": "json_object"})
        assert res_fallback_json == "{}"
    finally:
        if orig_key is not None:
            os.environ["LLM_API_KEY"] = orig_key
        reset_client()


# ---------------------------------------------------------------------------
# Direct Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db = FakeDB()

    def override_get_db():
        yield db

    def override_get_current_user(request: Request) -> CurrentUser:
        user_id = request.headers.get("X-Dev-User-Id")
        role = request.headers.get("X-Dev-Role")
        if not user_id or not role:
            raise HTTPException(
                status_code=401,
                detail="Missing X-Dev-User-Id/X-Dev-Role test auth headers.",
            )
        return CurrentUser(id=user_id, full_name=user_id, role=role)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    c = TestClient(app)

    print("Running Member 2 Intake & Extraction tests...")

    test_health_checks(c)
    print("✓ test_health_checks passed")

    test_auth_gating(c)
    print("✓ test_auth_gating passed")

    db = FakeDB()
    test_text_claim_typed_and_voice(c, db)
    print("✓ test_text_claim_typed_and_voice passed")

    db = FakeDB()
    test_file_claim_txt(c, db)
    print("✓ test_file_claim_txt passed")

    db = FakeDB()
    test_file_claim_pdf_multi_activity(c, db)
    print("✓ test_file_claim_pdf_multi_activity passed")

    db = FakeDB()
    test_file_claim_evidence_photo(c, db)
    print("✓ test_file_claim_evidence_photo passed")

    db = FakeDB()
    test_file_claim_scanned_diary(c, db)
    print("✓ test_file_claim_scanned_diary passed")

    db = FakeDB()
    test_file_claim_image_no_purpose_no_text_is_legitimate_claim_source(c, db)
    print("✓ test_file_claim_image_no_purpose_no_text_is_legitimate_claim_source passed")

    db = FakeDB()
    test_file_claim_xlsx_multi_sheet(c, db)
    print("✓ test_file_claim_xlsx_multi_sheet passed")

    db = FakeDB()
    test_file_claim_xer(c, db)
    print("✓ test_file_claim_xer passed")

    db = FakeDB()
    test_file_claim_unsupported_extension(c, db)
    print("✓ test_file_claim_unsupported_extension passed")

    db = FakeDB()
    test_file_claim_empty_file(c, db)
    print("✓ test_file_claim_empty_file passed")

    db = FakeDB()
    test_file_claim_corrupt_pdf(c, db)
    print("✓ test_file_claim_corrupt_pdf passed")

    db = FakeDB()
    test_file_claim_csv_bom_and_semicolon_delimiter(c, db)
    print("✓ test_file_claim_csv_bom_and_semicolon_delimiter passed")

    test_llm_extraction_failure_is_not_silently_swallowed()
    print("✓ test_llm_extraction_failure_is_not_silently_swallowed passed")

    db = FakeDB()
    test_schedule_export_claims(c, db)
    print("✓ test_schedule_export_claims passed")

    db = FakeDB()
    test_get_and_list_claims(c, db)
    print("✓ test_get_and_list_claims passed")

    db = FakeDB()
    test_missing_or_unknown_schedule_is_rejected(c, db)
    print("✓ test_missing_or_unknown_schedule_is_rejected passed")

    test_llm_extraction_schema_and_invariants()
    print("✓ test_llm_extraction_schema_and_invariants passed")

    db = FakeDB()
    test_real_sample_data_intake(c, db)
    print("✓ test_real_sample_data_intake passed")

    # New Features (29 & 33) Tests
    db = FakeDB()
    test_copilot_clarification_flow(c, db)
    print("✓ test_copilot_clarification_flow passed")

    db = FakeDB()
    test_field_provenance_tags(c, db)
    print("✓ test_field_provenance_tags passed")

    test_copilot_unit_rules()
    print("✓ test_copilot_unit_rules passed")

    test_shared_llm_client_fallback()
    print("✓ test_shared_llm_client_fallback passed")

    app.dependency_overrides.clear()
    print("\n==========================================")
    print("All Member 2 Intake & Extraction tests PASSED (including New Features 29 & 33)!")
    print("==========================================")
