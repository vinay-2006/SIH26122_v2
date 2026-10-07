"""A site engineer's report/evidence route must never accept a schedule."""
import io

import pytest

from backend.v2.upload_guard import schedule_like_reason

from helpers import F


@pytest.mark.parametrize("name,body", [
    ("plan.xer", b"anything"), ("renamed_report.txt", (F / "nsp.xer").read_bytes()), ("plan.mpp", b"x"), ("plan.mpt", b"x"),
    ("schedule.xml", (F / "nsp_mspdi.xml").read_bytes()), ("report.csv", (F / "nsp.csv").read_bytes()),
    ("daily.csv", b"Activity ID,Activity Name,Baseline Start,Baseline Finish\nA1,x,01-01-2026,02-01-2026\n"),
    ("daily.txt", b"Task ID;Task Name;WBS;Predecessors;Total Float\nT1;a;1;;0\n"),
])
def test_schedule_files_are_rejected(name, body):
    assert schedule_like_reason(name, body)


def test_schedule_shaped_workbook_is_rejected_but_a_measurement_sheet_is_not():
    import openpyxl
    def book(rows):
        wb = openpyxl.Workbook(); ws = wb.active
        for r in rows: ws.append(r)
        b = io.BytesIO(); wb.save(b); return b.getvalue()
    sched = book([["Activity ID", "Activity Name", "Baseline Start", "Baseline Finish", "WBS"], ["A1", "x", "2026-01-01", "2026-01-02", "1"]])
    meas = book([["Date", "Chainage", "Pipe laid (m)", "Welds done", "Remarks"], ["2026-03-03", "KM 12+400", 180, 14, "ok"]])
    assert schedule_like_reason("s.xlsx", sched)
    assert schedule_like_reason("measurements.xlsx", meas) is None


@pytest.mark.parametrize("name,body", [
    ("daily_report.txt", b"Date 03/03/2026. 180 m of 24 inch pipe laid at KM 12+400. 14 welds completed. Rain stopped work after 3 pm."),
    ("quantities.csv", b"Date,Location,Item,Quantity,Unit\n2026-03-03,KM 12+400,Pipe laid,180,m\n2026-03-03,KM 12+400,Welds,14,nos\n"),
    ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100), ("report.pdf", b"%PDF-1.7 ..."),
    ("diary.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50),
])
def test_ordinary_reports_and_evidence_pass(name, body):
    assert schedule_like_reason(name, body) is None
