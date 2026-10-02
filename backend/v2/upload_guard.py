"""Site-engineer upload guard. Reports and evidence are welcome; schedule files are not. Rejects (422) anything that is, or looks like,
a Primavera/MS Project/CSV schedule, so the report route can never become a back door into schedule import."""
from __future__ import annotations

import csv
import io
import re
from typing import Optional

SCHEDULE_EXT = (".xer", ".mpp", ".mpt", ".mpx", ".pmxml")
_SCHEDULE_HEADERS = {"activity id", "task id", "baseline start", "baseline finish", "planned start", "planned finish", "wbs", "wbs code",
                     "predecessors", "predecessor", "total float", "original duration", "baseline duration", "activity name", "task name"}
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("_", " ").strip().lower())


def _schedule_headers(headers) -> bool:
    return len({_norm(h) for h in headers} & _SCHEDULE_HEADERS) >= 3


def schedule_like_reason(filename: str, content: bytes) -> Optional[str]:
    """Return why the upload is a schedule file, or None if it looks like an ordinary report/evidence file."""
    name = (filename or "").lower()
    if name.endswith(SCHEDULE_EXT):
        return "Primavera / MS Project schedule files cannot be uploaded as reports"
    head = content[:8192]
    if head.startswith(OLE2_MAGIC) and name.endswith((".mpp", ".mpt")):
        return "MS Project schedule files cannot be uploaded as reports"
    if head.lstrip()[:6] == b"ERMHDR":
        return "Primavera P6 export content cannot be uploaded as a report"
    if b"schemas.microsoft.com/project" in head:
        return "Microsoft Project XML cannot be uploaded as a report"
    if name.endswith((".csv", ".tsv", ".txt")):
        try:
            text = content[:65536].decode("utf-8-sig", errors="replace")
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            first = next(csv.reader(io.StringIO(text), dialect=dialect), [])
            if _schedule_headers(first):
                return "This CSV looks like a project schedule (activity ids, baseline dates, WBS); schedules are imported by a Project Manager"
        except Exception:
            return None
    if name.endswith((".xlsx", ".xlsm")) and content.startswith(b"PK"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            for ws in wb.worksheets[:3]:
                first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
                if _schedule_headers([str(c) for c in first if c is not None]):
                    return "This workbook looks like a project schedule; schedules are imported by a Project Manager"
        except Exception:
            return None
    return None
