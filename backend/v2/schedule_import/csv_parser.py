"""CSV schedule (+ optional resource assignment CSV) -> ParsedSchedule.

Activity file: one row per activity. Header names are matched case-insensitively with common synonyms. Required: an activity id,
a name, start and finish (or start + duration). WBS comes from `wbs_code` (dotted, e.g. 1.2.3 => nodes 1, 1.2, 1.2.3) with an
optional `wbs_name`, or from a `wbs_path` of names (`Pipeline > Stage 1 > Trenching`). Predecessors: `A100`, `A100FS+2`, `A100;A200SS`.
Resource file: activity_id, resource_id, [resource_name], [resource_class], baseline_qty, unit, [measures_progress], [progress_weight].
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from .models import (Issue, PActivity, PAssignment, PDependency, ParseError, ParsedSchedule, PProject, PResource, PWbs,
                     parse_date_text, working_days)

SYN: Dict[str, Tuple[str, ...]] = {
    "id": ("activity id", "activity_id", "activityid", "task id", "task_id", "id", "activity code", "task code"),
    "name": ("activity name", "activity_name", "name", "task name", "description of work"),
    "description": ("description", "details", "scope"),
    "wbs_code": ("wbs code", "wbs_code", "wbs", "wbs id"),
    "wbs_name": ("wbs name", "wbs_name"),
    "wbs_path": ("wbs path", "wbs_path", "wbs hierarchy"),
    "discipline": ("discipline", "trade", "work type"),
    "type": ("activity type", "activity_type", "type", "task type"),
    "location": ("location", "area", "chainage"),
    "asset_tag": ("asset tag", "asset_tag", "assettag", "equipment tag", "tag no", "tag number"),
    "duration": ("baseline duration", "baseline_duration", "duration", "original duration", "orig duration", "duration days"),
    "start": ("baseline start", "baseline_start", "start", "planned start", "start date", "target start"),
    "finish": ("baseline finish", "baseline_finish", "finish", "planned finish", "finish date", "end", "target finish"),
    "float": ("total float", "total_float", "float", "total slack"),
    "preds": ("predecessors", "predecessor", "predecessor activity id", "preds"),
}
RSYN: Dict[str, Tuple[str, ...]] = {
    "activity": ("activity id", "activity_id", "task id", "activity"),
    "resource": ("resource id", "resource_id", "resource code", "resource"),
    "rname": ("resource name", "resource_name", "name"),
    "rclass": ("resource class", "resource_class", "resource type", "class", "type"),
    "qty": ("baseline qty", "baseline_qty", "qty", "quantity", "budgeted units", "target qty"),
    "unit": ("unit of measure", "unit_of_measure", "uom", "unit", "units"),
    "measures": ("measures progress", "measures_progress", "physical", "drives progress"),
    "weight": ("progress weight", "progress_weight", "weight"),
}
TYPE_MAP = {"task": "TASK", "task dependent": "TASK", "activity": "TASK", "milestone": "MILESTONE", "start milestone": "MILESTONE",
            "finish milestone": "MILESTONE", "mile": "MILESTONE", "loe": "LOE", "level of effort": "LOE", "hammock": "LOE"}
CLASS_MAP = {"material": "MATERIAL", "mat": "MATERIAL", "labor": "LABOR", "labour": "LABOR", "manpower": "LABOR", "equipment": "EQUIPMENT",
             "plant": "EQUIPMENT", "machine": "EQUIPMENT", "nonlabor": "EQUIPMENT", "other": "OTHER"}
TRUE = {"y", "yes", "true", "1", "t"}
FALSE = {"n", "no", "false", "0", "f"}
_PRED = re.compile(r"^\s*(?P<id>.+?)\s*(?:(?P<type>FS|SS|FF|SF)\s*(?P<lag>[+-]\s*\d+(?:\.\d+)?)?\s*(?:d|days?)?)?\s*$", re.I)


def _read(content: bytes) -> List[Dict[str, str]]:
    if b"\x00" in content[:4096]:
        raise ParseError("The file looks binary, not CSV text.", "NOT_A_CSV")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:                                                    # pragma: no cover  (cp1252 decodes any byte except a few)
        raise ParseError("The file encoding is not UTF-8 or Windows-1252.", "BAD_ENCODING")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows or not rows[0]:
        raise ParseError("The CSV has no header row or no data rows.", "EMPTY_FILE")
    return rows


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("_", " ").strip().lower())


def _colmap(headers, syn) -> Dict[str, str]:
    by_norm = {_norm(h): h for h in headers if h}
    out = {}
    for key, names in syn.items():
        for n in names:
            if _norm(n) in by_norm:
                out[key] = by_norm[_norm(n)]
                break
    return out


def _num(v) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_csv(content: bytes, resources_content: Optional[bytes] = None, project_name: Optional[str] = None) -> ParsedSchedule:
    rows = _read(content)
    cm = _colmap(rows[0].keys(), SYN)
    missing = [k for k in ("id", "name") if k not in cm]
    if missing:
        raise ParseError(f"Required column(s) not found: {', '.join(missing)}. Found headers: {', '.join(h for h in rows[0].keys() if h)}",
                         "MISSING_COLUMNS")
    ps = ParsedSchedule(project=PProject(name=project_name, source_format="CSV"))
    wbs_seen: Dict[str, PWbs] = {}
    seq = 0

    def add_wbs(code: str, name: str, parent: Optional[str]):
        if code not in wbs_seen:
            wbs_seen[code] = PWbs(code=code, name=name, parent_code=parent, sequence=len(wbs_seen) + 1)
        elif name and wbs_seen[code].name == wbs_seen[code].code:
            wbs_seen[code].name = name

    pred_cells: List[Tuple[str, str]] = []
    for i, r in enumerate(rows, start=2):
        g = lambda k: (r.get(cm[k]) or "").strip() if k in cm else ""
        ext = g("id")
        if not ext and not g("name"):
            continue                                          # blank line
        seq += 1
        # ---- WBS
        wcode = None
        if g("wbs_path"):
            parts = [p.strip() for p in re.split(r"\s*[>/]\s*", g("wbs_path")) if p.strip()]
            parent = None
            for lvl, nm in enumerate(parts, start=1):
                code = "/".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_").upper() for p in parts[:lvl])[:200].replace("/", ".")
                add_wbs(code, nm, parent)
                parent = code
            wcode = parent
        elif g("wbs_code"):
            segs = g("wbs_code").split(".")
            parent = None
            for lvl in range(1, len(segs) + 1):
                code = ".".join(segs[:lvl])
                add_wbs(code, g("wbs_name") if lvl == len(segs) and g("wbs_name") else code, parent)
                parent = code
            wcode = parent
        # ---- dates / duration
        start, finish = parse_date_text(g("start")), parse_date_text(g("finish"))
        dur = _num(g("duration"))
        if g("start") and start is None:
            ps.issues.append(Issue("BAD_DATE", f"Row {i}: start date {g('start')!r} is not a recognised date", ext))
        if g("finish") and finish is None:
            ps.issues.append(Issue("BAD_DATE", f"Row {i}: finish date {g('finish')!r} is not a recognised date", ext))
        atype = TYPE_MAP.get(g("type").lower(), "TASK") if g("type") else "TASK"
        if g("type") and g("type").lower() not in TYPE_MAP:
            ps.issues.append(Issue("UNKNOWN_ACTIVITY_TYPE", f"Row {i}: activity type {g('type')!r} treated as TASK", ext, "WARNING"))
        ps.activities.append(PActivity(
            external_id=ext, name=g("name"), wbs_code=wcode, start=start, finish=finish, duration_days=dur,
            total_float_days=_num(g("float")), activity_type=atype, discipline_label=g("discipline") or None,
            description=g("description") or None, location=g("location") or None, asset_tag=g("asset_tag") or None, sequence=seq))
        if g("preds"):
            pred_cells.append((ext, g("preds")))
    ps.wbs = list(wbs_seen.values())
    for succ, cell in pred_cells:
        for tok in [t for t in re.split(r"[;,]", cell) if t.strip()]:
            m = _PRED.match(tok)
            if not m:
                ps.issues.append(Issue("BAD_PREDECESSOR", f"Cannot read predecessor {tok.strip()!r}", succ))
                continue
            lag = float(m.group("lag").replace(" ", "")) if m.group("lag") else 0.0
            ps.dependencies.append(PDependency(m.group("id").strip(), succ, (m.group("type") or "FS").upper(), lag))
    if resources_content is not None:
        _parse_resources(ps, resources_content)
    return ps


def _parse_resources(ps: ParsedSchedule, content: bytes) -> None:
    rows = _read(content)
    cm = _colmap(rows[0].keys(), RSYN)
    missing = [k for k in ("activity", "resource", "qty") if k not in cm]
    if missing:
        raise ParseError(f"Resource file: required column(s) not found: {', '.join(missing)}", "MISSING_COLUMNS")
    seen: Dict[str, PResource] = {}
    for i, r in enumerate(rows, start=2):
        g = lambda k: (r.get(cm[k]) or "").strip() if k in cm else ""
        if not g("activity") and not g("resource"):
            continue
        code = re.sub(r"[^A-Za-z0-9]+", "_", g("resource")).strip("_").upper()
        cls = CLASS_MAP.get(g("rclass").lower()) if g("rclass") else None
        if code not in seen:
            seen[code] = PResource(code=code, name=g("rname") or g("resource"), resource_class=cls, uom_label=g("unit") or None)
        meas = None
        if g("measures"):
            meas = True if g("measures").lower() in TRUE else False if g("measures").lower() in FALSE else None
        qty = _num(g("qty"))
        if g("qty") and qty is None:
            ps.issues.append(Issue("BAD_QUANTITY", f"Resource row {i}: quantity {g('qty')!r} is not a number", g("activity")))
        ps.assignments.append(PAssignment(g("activity"), code, qty, g("unit") or None, meas, _num(g("weight")) or 1.0))
    ps.resources = list(seen.values())
