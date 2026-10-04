"""Deterministic, local, SYNCHRONOUS extraction of claim candidates from reports: CSV, XLSX, plain text and text-layer PDF.

* No network and no LLM. Live-LLM extraction does not exist in this module (it stays off; a later phase may add it behind an explicit opt-in).
* Strict: a candidate is only eligible when it names an activity id, carries a quantity WITH a recognised unit and an explicit basis
  (cumulative or incremental) and/or a percentage, and has a date. Anything ambiguous is returned as `skipped` with a reason; nothing is guessed.
* Bounded: every loop checks one wall-clock budget; row, page and size caps apply. Hitting a cap FAILS the extraction instead of
  returning a partial result.
* Failure never produces candidates, and extraction itself never writes a claim: the caller decides what to file.
* Scanned documents, photographs and handwriting need OCR / vision and are a documented later-phase capability: they fail with SCANNED_NOT_SUPPORTED."""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Tuple

MAX_ROWS = 2000
MAX_PAGES = 60
DEFAULT_BUDGET_S = 15.0


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Candidate:
    source_ref: str
    activity_ref: str
    quantities: List[dict] = field(default_factory=list)       # {qty: Decimal, uom: str, basis: CUMULATIVE|INCREMENTAL, resource_hint: Optional[str]}
    claimed_pct: Optional[Decimal] = None
    event_date: Optional[date] = None
    text: str = ""
    problems: List[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.problems


@dataclass
class Extraction:
    method: str
    candidates: List[Candidate]
    page_count: Optional[int] = None
    warnings: List[str] = field(default_factory=list)


class Budget:
    def __init__(self, seconds: float):
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise ExtractionError("EXTRACTION_TIMEOUT", "Reading the document took too long; split it or send a smaller file")


# ------------------------------------------------------------------------------------------------ values
def _dec(v) -> Optional[Decimal]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().replace(",", "").replace(" ", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{v!r} is not a number")
    if not d.is_finite():
        raise ValueError(f"{v!r} is not a number")
    return d


def _pct(v) -> Optional[Decimal]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().rstrip("%").strip()
    d = _dec(s)
    if d is not None and isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and Decimal(0) < d <= 1 and "%" not in str(v):
        d = d * 100                       # Excel stores 45% as 0.45
    if d is not None and not (0 <= d <= 100):
        raise ValueError(f"{v!r} is not a percentage between 0 and 100")
    return d


_DATE_FORMS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")


def parse_date(v) -> Optional[date]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for f in _DATE_FORMS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ValueError(f"{v!r} is not a recognised date (use YYYY-MM-DD or DD-MM-YYYY)")


_DATE_IN_TEXT = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\b")


def _find_date(s: str) -> Optional[date]:
    for m in _DATE_IN_TEXT.finditer(s):
        try:
            return parse_date(m.group(1).replace("/", "-").replace(".", "-") if re.match(r"\d{1,2}[/.]", m.group(1)) else m.group(1))
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------------------------------------ tables (CSV / XLSX)
def _norm(h) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", str(h or "").lower()).strip()


_ACT = {"activity id", "activity", "activity code", "task id", "task", "id", "activity no"}
_DATE = {"date", "report date", "progress date", "as of", "as of date", "reporting date"}
_CUM = {"cumulative qty", "cumulative quantity", "qty to date", "quantity to date", "cum qty", "total qty", "cumulative"}
_INC = {"qty today", "quantity today", "today qty", "today", "daily qty", "incremental qty", "incremental quantity", "qty done", "quantity done today"}
_QTY = {"qty", "quantity"}
_BASIS = {"basis", "qty basis", "quantity basis", "type"}
_UNIT = {"unit", "uom", "unit of measure", "units"}
_RES = {"resource", "resource code", "measure", "item", "material"}
_PCT = {"% complete", "percent complete", "pct", "progress %", "physical %", "percent", "% progress", "complete %", "pct complete"}
_NOTE = {"remarks", "comment", "comments", "notes", "description", "work done", "narrative"}


def _colmap(headers: List[str]) -> Optional[Dict[str, int]]:
    m: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
        for key, names in (("act", _ACT), ("date", _DATE), ("cum", _CUM), ("inc", _INC), ("qty", _QTY), ("basis", _BASIS), ("unit", _UNIT), ("res", _RES), ("pct", _PCT), ("note", _NOTE)):
            if n in names and key not in m:
                m[key] = i
                break
    if "act" in m and ({"cum", "inc", "qty", "pct"} & set(m)):
        return m
    return None


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def _table(rows: List[list], unit_ok: Callable[[str], bool], default_date: Optional[date], budget: Budget, method: str) -> Extraction:
    hdr_i, cm = None, None
    for i, r in enumerate(rows[:10]):
        cm = _colmap(["" if c is None else str(c) for c in r])
        if cm:
            hdr_i = i
            break
    if cm is None:
        raise ExtractionError("NOT_A_PROGRESS_TABLE", "No activity-id column with a quantity or percentage column was found in the first rows")
    out: List[Candidate] = []
    body = rows[hdr_i + 1:]
    if len(body) > MAX_ROWS:
        raise ExtractionError("TOO_MANY_ROWS", f"At most {MAX_ROWS} rows can be read from one file")
    for off, r in enumerate(body):
        budget.check()
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        ref = f"row {hdr_i + off + 2}"
        act = str(_cell(r, cm.get("act")) or "").strip()
        cand = Candidate(source_ref=ref, activity_ref=act, text=" | ".join(str(c).strip() for c in r if c is not None and str(c).strip())[:500])
        try:
            if not act:
                cand.problems.append("ACTIVITY_MISSING")
            d = parse_date(_cell(r, cm.get("date"))) or default_date
            if d is None:
                cand.problems.append("DATE_MISSING")
            cand.event_date = d
            unit = str(_cell(r, cm.get("unit")) or "").strip()
            res = str(_cell(r, cm.get("res")) or "").strip() or None
            for key, basis in (("cum", "CUMULATIVE"), ("inc", "INCREMENTAL")):
                q = _dec(_cell(r, cm.get(key))) if key in cm else None
                if q is not None:
                    cand.quantities.append({"qty": q, "uom": unit, "basis": basis, "resource_hint": res})
            if "qty" in cm:
                q = _dec(_cell(r, cm["qty"]))
                if q is not None:
                    b = _norm(_cell(r, cm.get("basis")))
                    basis = "CUMULATIVE" if b.startswith("cum") else ("INCREMENTAL" if b.startswith(("inc", "daily", "today")) else None)
                    if basis is None:
                        cand.problems.append("BASIS_UNKNOWN")
                    else:
                        cand.quantities.append({"qty": q, "uom": unit, "basis": basis, "resource_hint": res})
            for q in cand.quantities:
                if q["qty"] < 0:
                    cand.problems.append("NEGATIVE_QUANTITY")
                if not q["uom"] or not unit_ok(q["uom"]):
                    cand.problems.append("UNKNOWN_UNIT")
            cand.claimed_pct = _pct(_cell(r, cm.get("pct")))
            if not cand.quantities and cand.claimed_pct is None and "BASIS_UNKNOWN" not in cand.problems:
                cand.problems.append("NO_PROGRESS_VALUE")
        except ValueError as e:
            cand.problems.append(f"BAD_VALUE: {e}")
        cand.problems = list(dict.fromkeys(cand.problems))
        out.append(cand)
    return Extraction(method=method, candidates=out)


def _csv_rows(content: bytes) -> List[list]:
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ExtractionError("UNREADABLE_TEXT", "The file is not readable text")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect=dialect)]


def _xlsx_rows(content: bytes, budget: Budget) -> List[list]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise ExtractionError("UNREADABLE_WORKBOOK", "The workbook could not be opened") from e
    for ws in wb.worksheets[:3]:
        rows: List[list] = []
        for r in ws.iter_rows(values_only=True):
            budget.check()
            rows.append(list(r))
            if len(rows) > MAX_ROWS + 20:
                raise ExtractionError("TOO_MANY_ROWS", f"At most {MAX_ROWS} rows can be read from one file")
        if any(_colmap(["" if c is None else str(c) for c in r]) for r in rows[:10]):
            return rows
    raise ExtractionError("NOT_A_PROGRESS_TABLE", "No sheet has an activity-id column with a quantity or percentage column")


# ------------------------------------------------------------------------------------------------ free text and PDF text
_ACT_RE = re.compile(r"\b([A-Z]{1,4}-?\d{2,5}(?:-\d{1,3})?)\b")
_NUM_UNIT = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z][A-Za-z0-9³²./-]{0,15})")
_PCT_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*%")
_CUM_RE = re.compile(r"\b(cumulative|cum\.?|total|to[- ]date|till date|so far|overall)\b", re.I)
_INC_RE = re.compile(r"\b(today|this shift|this day|daily|incremental|during the day)\b", re.I)
_FILLER = {"complete", "completed", "done", "progress", "of", "to", "in", "on", "at", "by", "and", "the", "for", "today", "total", "cumulative", "so", "till", "date", "percent"}


def _text_lines(lines: List[Tuple[str, str]], unit_ok: Callable[[str], bool], default_date: Optional[date], budget: Budget, method: str) -> Extraction:
    doc_date = None
    for _, ln in lines[:40]:
        doc_date = _find_date(ln)
        if doc_date:
            break
    out: List[Candidate] = []
    for ref, ln in lines:
        budget.check()
        s = ln.strip()
        m = _ACT_RE.search(s)
        if not m or len(s) < 6:
            continue
        rest = s[m.end():]
        cand = Candidate(source_ref=ref, activity_ref=m.group(1), text=s[:500])
        d = _find_date(rest) or doc_date or default_date
        cand.event_date = d
        if d is None:
            cand.problems.append("DATE_MISSING")
        pm = _PCT_RE.search(rest)
        rest_nopct = _PCT_RE.sub(" ", rest)
        for qm in _NUM_UNIT.finditer(rest_nopct):
            word = qm.group(2)
            if word.lower() in _FILLER or not unit_ok(word):
                continue
            try:
                q = _dec(qm.group(1))
            except ValueError:
                continue
            cum, inc = bool(_CUM_RE.search(rest)), bool(_INC_RE.search(rest))
            if cum == inc:
                cand.problems.append("BASIS_UNKNOWN")
                continue
            cand.quantities.append({"qty": q, "uom": word, "basis": "CUMULATIVE" if cum else "INCREMENTAL", "resource_hint": None})
        if pm:
            try:
                cand.claimed_pct = _pct(pm.group(1))
            except ValueError as e:
                cand.problems.append(f"BAD_VALUE: {e}")
        if not cand.quantities and cand.claimed_pct is None:
            if "BASIS_UNKNOWN" not in cand.problems:
                continue                                        # a line that mentions an id but reports nothing is not a claim
        cand.problems = list(dict.fromkeys(cand.problems))
        out.append(cand)
        if len(out) > MAX_ROWS:
            raise ExtractionError("TOO_MANY_ROWS", f"At most {MAX_ROWS} claims can be read from one file")
    return Extraction(method=method, candidates=out)


def _pdf_lines(content: bytes, budget: Budget) -> Tuple[List[Tuple[str, str]], int]:
    try:
        import fitz
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        raise ExtractionError("UNREADABLE_PDF", "The PDF could not be opened") from e
    try:
        if doc.is_encrypted:
            raise ExtractionError("PDF_ENCRYPTED", "Encrypted PDFs cannot be read")
        n = doc.page_count
        if n > MAX_PAGES:
            raise ExtractionError("PDF_TOO_LONG", f"At most {MAX_PAGES} pages can be read from one PDF")
        lines: List[Tuple[str, str]] = []
        for p in range(n):
            budget.check()
            for i, ln in enumerate(doc[p].get_text("text").splitlines(), 1):
                lines.append((f"page {p + 1} line {i}", ln))
        if sum(len(l) for _, l in lines) < 20:
            raise ExtractionError("SCANNED_NOT_SUPPORTED", "This PDF has no text layer (scanned or handwritten); OCR extraction is not available yet")
        return lines, n
    finally:
        doc.close()


def docx_text(content: bytes) -> str:
    """the text of a Word (DOCX) document: paragraphs and table cells, in order. No images, no macros, no external content is read."""
    import io
    import re
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 40 * 1024 * 1024:
                raise ExtractionError("DOCX_TOO_LARGE", "The document text is too large to read")
            raw = z.read("word/document.xml")
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError("UNREADABLE_DOCX", "The Word document could not be opened") from e
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ExtractionError("UNREADABLE_DOCX", "The Word document is not valid") from e
    lines: List[str] = []
    for para in root.iter(W + "p"):
        parts = []
        for node in para.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (W + "tab", W + "br"):
                parts.append(" ")
        line = re.sub(r"\s+", " ", "".join(parts)).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ entry point
def extract(content: bytes, family: str, ext: str, *, unit_ok: Callable[[str], bool], default_date: Optional[date] = None, budget_s: float = DEFAULT_BUDGET_S) -> Extraction:
    b = Budget(budget_s)
    if family == "image":
        raise ExtractionError("SCANNED_NOT_SUPPORTED", "Photographs are evidence only; reading values from images (OCR / vision) is not available yet")
    if family == "docx":
        lines = [(f"line {i}", ln) for i, ln in enumerate(docx_text(content).splitlines(), 1)]
        if sum(len(l) for _, l in lines) < 20:
            raise ExtractionError("EMPTY_DOCUMENT", "The Word document has no readable text")
        return _text_lines(lines, unit_ok, default_date, b, "DOCX_TEXT_RULES")
    if family == "xlsx":
        return _table(_xlsx_rows(content, b), unit_ok, default_date, b, "XLSX_TABLE")
    if family == "pdf":
        lines, n = _pdf_lines(content, b)
        ex = _text_lines(lines, unit_ok, default_date, b, "PDF_TEXT_RULES")
        ex.page_count = n
        return ex
    if family == "text":
        rows = _csv_rows(content) if ext == "csv" else None
        if rows is not None:
            try:
                return _table(rows, unit_ok, default_date, b, "CSV_TABLE")
            except ExtractionError as e:
                if e.code != "NOT_A_PROGRESS_TABLE":
                    raise
        text = content.decode("utf-8-sig", errors="replace")
        return _text_lines([(f"line {i}", l) for i, l in enumerate(text.splitlines(), 1)], unit_ok, default_date, b, "TEXT_RULES")
    raise ExtractionError("UNSUPPORTED_FILE_TYPE", "That file type cannot be extracted")
