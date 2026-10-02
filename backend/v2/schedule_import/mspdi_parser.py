"""Microsoft Project XML (MSPDI) -> ParsedSchedule.

Summary tasks become WBS nodes (outline hierarchy); non-summary tasks become activities attached to their nearest summary parent.
Durations are ISO-8601 (PT64H0M0S) converted by the project's MinutesPerDay; slack/lag are tenths of a minute. Discipline is read
from an extended attribute whose alias contains 'discipline'/'trade', else left for the mapping step.
Material resources: quantity = assignment Units, unit = MaterialLabel. Work resources: quantity = Work hours in MH
(group 'Equipment'/'Plant' => EQUIPMENT hours, else LABOR).

XML safety: DOCTYPE / ENTITY declarations are rejected before parsing (no external entities, no entity expansion).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .models import (Issue, PActivity, PAssignment, PDependency, ParseError, ParsedSchedule, PProject, PResource, PWbs,
                     parse_date_text)

NS = "{http://schemas.microsoft.com/project}"
LINK_TYPE = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


def _t(el, tag: str) -> Optional[str]:
    c = el.find(NS + tag)
    return c.text.strip() if c is not None and c.text else None


def _hours(iso: Optional[str]) -> Optional[float]:
    """PT64H0M0S / P1DT4H -> hours (24h days only used when MS Project writes days)."""
    if not iso:
        return None
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$", iso.strip())
    if not m:
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 24 + h + mi / 60 + s / 3600


def _code(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()


def looks_like_mspdi(content: bytes) -> bool:
    return b"schemas.microsoft.com/project" in content[:4000]


def parse_mspdi(content: bytes) -> ParsedSchedule:
    head = content[:2_000_000]
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", head, re.I):
        raise ParseError("The XML contains a DOCTYPE/ENTITY declaration, which is not allowed.", "UNSAFE_XML")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ParseError(f"The XML is not well formed: {exc}", "BAD_XML") from exc
    if root.tag != NS + "Project":
        raise ParseError("This XML is not a Microsoft Project (MSPDI) file: the root element is not <Project> in the MS Project namespace.",
                         "NOT_MSPDI")
    ps = ParsedSchedule()
    mpd = float(_t(root, "MinutesPerDay") or 480)
    hpd = mpd / 60.0
    ps.project = PProject(
        name=_t(root, "Title") or _t(root, "Name"), source_format="MSPDI", hours_per_day=hpd,
        data_date=parse_date_text(_t(root, "StatusDate")) or parse_date_text(_t(root, "CurrentDate")),
        planned_start=parse_date_text(_t(root, "StartDate")), planned_finish=parse_date_text(_t(root, "FinishDate")))

    # ---- discipline custom field
    disc_field = id_field = None
    for ea in root.findall(f"{NS}ExtendedAttributes/{NS}ExtendedAttribute"):
        alias = (_t(ea, "Alias") or "")
        if disc_field is None and re.search(r"discipline|trade", alias, re.I):
            disc_field = _t(ea, "FieldID")
        if id_field is None and re.search(r"activity id|activity code|task code", alias, re.I):
            id_field = _t(ea, "FieldID")

    def ext_attr(tk, field):
        if field:
            for ea in tk.findall(f"{NS}ExtendedAttribute"):
                if _t(ea, "FieldID") == field:
                    return _t(ea, "Value")
        return None

    tasks = root.findall(f"{NS}Tasks/{NS}Task")
    by_uid = {}
    for tk in tasks:
        uid = _t(tk, "UID")
        if uid is None or _t(tk, "IsNull") == "1":
            continue
        by_uid[uid] = tk
    # outline stack: nearest summary ancestor of each task
    summary_stack: List[tuple] = []          # (outline_level, wbs_code)
    wbs_code_of_uid: Dict[str, str] = {}
    ext_of_uid: Dict[str, str] = {}
    seq = w_seq = 0
    names_used: Dict[str, int] = {}
    ordered = sorted(by_uid.values(), key=lambda x: int(_t(x, "ID") or 0))
    for tk in ordered:
        uid, lvl, summary = _t(tk, "UID"), int(_t(tk, "OutlineLevel") or 0), _t(tk, "Summary") == "1"
        while summary_stack and summary_stack[-1][0] >= lvl:
            summary_stack.pop()
        parent = summary_stack[-1][1] if summary_stack else None
        if summary:
            base = _code(_t(tk, "WBS") or _t(tk, "OutlineNumber") or _t(tk, "Name") or uid) or f"W{uid}"
            code = base if base not in names_used else f"{base}_{uid}"
            names_used[code] = 1
            w_seq += 1
            ps.wbs.append(PWbs(code=code, name=_t(tk, "Name") or code, parent_code=parent, sequence=w_seq))
            wbs_code_of_uid[uid] = code
            summary_stack.append((lvl, code))
            continue
        # ---- activity (id: custom 'Activity ID' field, else the task's WBS code, else its UID)
        seq += 1
        dur_h = _hours(_t(tk, "Duration"))
        slack = _t(tk, "TotalSlack")
        ext = ext_attr(tk, id_field) or _t(tk, "WBS") or f"T{uid}"
        ext_of_uid[uid] = ext
        ps.activities.append(PActivity(
            external_id=ext, name=_t(tk, "Name") or "", wbs_code=parent,
            start=parse_date_text(_t(tk, "Start")), finish=parse_date_text(_t(tk, "Finish")),
            duration_days=round(dur_h / hpd, 2) if dur_h is not None else None,
            total_float_days=round(float(slack) / 10.0 / mpd, 2) if slack not in (None, "") else None,
            activity_type="MILESTONE" if _t(tk, "Milestone") == "1" else "TASK",
            discipline_label=ext_attr(tk, disc_field), description=_t(tk, "Notes"), sequence=seq))
    # ---- dependencies
    uid_to_ext = ext_of_uid
    for uid, ext in uid_to_ext.items():
        for pl in by_uid[uid].findall(f"{NS}PredecessorLink"):
            pu = _t(pl, "PredecessorUID")
            if pu in uid_to_ext:
                lag = float(_t(pl, "LinkLag") or 0) / 10.0 / mpd
                ps.dependencies.append(PDependency(uid_to_ext[pu], ext, LINK_TYPE.get(_t(pl, "Type") or "1", "FS"), round(lag, 2)))
            elif pu is not None:
                ps.issues.append(Issue("LINK_TO_SUMMARY", f"{ext}: predecessor UID {pu} is a summary task or missing; link ignored", ext, "WARNING"))

    # ---- resources and assignments
    res: Dict[str, PResource] = {}
    group_of: Dict[str, str] = {}
    for r in root.findall(f"{NS}Resources/{NS}Resource"):
        ruid, name = _t(r, "UID"), _t(r, "Name")
        if ruid in (None, "0") or not name:
            continue
        rtype = _t(r, "Type")           # 0 material, 1 work, 2 cost
        grp = (_t(r, "Group") or "").lower()
        if rtype == "0":
            cls, uom = "MATERIAL", _t(r, "MaterialLabel")
        elif rtype == "1":
            cls, uom = ("EQUIPMENT", "HR") if re.search(r"equip|plant|machine", grp) else ("LABOR", "MH")
        else:
            cls, uom = "OTHER", None
        res[ruid] = PResource(code=_code(name), name=name, resource_class=cls, uom_label=uom)
    used = set()
    for a in root.findall(f"{NS}Assignments/{NS}Assignment"):
        tu, ru = _t(a, "TaskUID"), _t(a, "ResourceUID")
        if tu not in uid_to_ext or ru not in res:
            continue
        r = res[ru]
        if r.resource_class == "MATERIAL":
            qty = _t(a, "Units")
            q = float(qty) if qty not in (None, "") else None
        else:
            h = _hours(_t(a, "Work"))
            q = round(h, 2) if h is not None else None
        ps.assignments.append(PAssignment(uid_to_ext[tu], r.code, q, r.uom_label))
        used.add(ru)
    ps.resources = [res[k] for k in res if k in used]
    return ps
