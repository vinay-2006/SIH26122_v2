"""Primavera P6 .xer -> ParsedSchedule. Reuses the table reader in backend.shared.xer_parser (the %T/%F/%R format is
column-driven, never positional) and adds what the baseline model needs: project header (data date, planned window), the WBS tree,
durations/float converted from hours by the project calendar, relationships, resources (RSRC/TASKRSRC/UMEASURE) and
activity-code disciplines (ACTVTYPE/ACTVCODE/TASKACTV)."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional

from backend.shared.xer_parser import XERParseError, parse_xer_tables

from .models import (Issue, PActivity, PAssignment, PDependency, ParseError, ParsedSchedule, PProject, PResource, PWbs,
                     parse_date_text)

TASK_TYPE = {"TT_Task": "TASK", "TT_Rsrc": "TASK", "TT_Mile": "MILESTONE", "TT_FinMile": "MILESTONE", "TT_LOE": "LOE"}
PRED_TYPE = {"PR_FS": "FS", "PR_SS": "SS", "PR_FF": "FF", "PR_SF": "SF"}
RES_CLASS = {"RT_Labor": "LABOR", "RT_Mat": "MATERIAL", "RT_Equip": "EQUIPMENT"}


def _f(v) -> Optional[float]:
    try:
        return float(str(v).strip()) if v not in (None, "") else None
    except ValueError:
        return None


def _code(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()


def parse_xer(content: bytes, project_id: Optional[str] = None) -> ParsedSchedule:
    if not content.lstrip()[:6].startswith(b"ERMHDR") and b"ERMHDR" not in content[:200]:
        raise ParseError("This does not look like a Primavera P6 XER export (no ERMHDR header).", "NOT_AN_XER")
    try:
        t = parse_xer_tables(content)
    except XERParseError as exc:
        raise ParseError(f"XER could not be read: {exc}", "BAD_XER") from exc
    rows = lambda name: t[name].rows if name in t else []
    ps = ParsedSchedule()

    # ---- which P6 project (an XER can hold several, including baselines)
    tasks_by_proj: Dict[str, list] = defaultdict(list)
    for r in rows("TASK"):
        tasks_by_proj[r.get("proj_id", "")].append(r)
    if not tasks_by_proj:
        raise ParseError("The XER has a TASK table but no activities.", "EMPTY_FILE")
    if project_id and project_id in tasks_by_proj:
        pid = project_id
    else:
        pid = max(tasks_by_proj, key=lambda k: len(tasks_by_proj[k]))
        if len(tasks_by_proj) > 1:
            names = {p.get("proj_id"): p.get("proj_short_name") for p in rows("PROJECT")}
            ps.issues.append(Issue("MULTIPLE_PROJECTS", "The XER contains several projects ("
                                   + ", ".join(f"{names.get(k, k)}: {len(v)} activities" for k, v in tasks_by_proj.items())
                                   + f"); importing {names.get(pid, pid)}", None, "WARNING"))
    prow = next((p for p in rows("PROJECT") if p.get("proj_id") == pid), {})
    cal = {c.get("clndr_id"): _f(c.get("day_hr_cnt")) for c in rows("CALENDAR")}
    hpd = cal.get(prow.get("clndr_id")) or 8.0
    ps.project = PProject(
        name=prow.get("proj_short_name"), code=prow.get("proj_short_name"), source_format="XER", hours_per_day=hpd,
        data_date=parse_date_text(prow.get("last_recalc_date")) or parse_date_text(prow.get("sum_data_date")),
        planned_start=parse_date_text(prow.get("plan_start_date")), planned_finish=parse_date_text(prow.get("scd_end_date")))

    # ---- WBS tree (codes are dotted paths of P6 short names so they are unique within the version)
    wrows = [w for w in rows("PROJWBS") if w.get("proj_id") == pid]
    by_id = {w["wbs_id"]: w for w in wrows}
    code_of: Dict[str, str] = {}

    def wcode(wid: str, depth: int = 0) -> str:
        if wid in code_of:
            return code_of[wid]
        w = by_id[wid]
        par = w.get("parent_wbs_id")
        short = _code(w.get("wbs_short_name") or w.get("wbs_name") or wid) or f"W{wid}"
        c = short if (not par or par not in by_id or depth > 50) else f"{wcode(par, depth + 1)}.{short}"
        code_of[wid] = c
        return c

    for w in sorted(wrows, key=lambda x: (_f(x.get("seq_num")) or 0)):
        code = wcode(w["wbs_id"])
        par = w.get("parent_wbs_id")
        ps.wbs.append(PWbs(code=code, name=w.get("wbs_name") or code, parent_code=code_of.get(par) if par in by_id else None,
                           sequence=len(ps.wbs) + 1))

    # ---- discipline via activity codes
    atype_ids = {a["actv_code_type_id"] for a in rows("ACTVTYPE") if re.search(r"discipline|trade", a.get("actv_code_type", ""), re.I)}
    code_name = {c["actv_code_id"]: (c.get("short_name") or c.get("actv_code_name"))
                 for c in rows("ACTVCODE") if c.get("actv_code_type_id") in atype_ids}
    disc_of_task = {}
    for ta in rows("TASKACTV"):
        if ta.get("actv_code_id") in code_name:
            disc_of_task[ta["task_id"]] = code_name[ta["actv_code_id"]]

    # ---- activities
    task_code: Dict[str, str] = {}
    seq = 0
    for r in tasks_by_proj[pid]:
        ttype = r.get("task_type", "TT_Task")
        if ttype == "TT_WBS":
            ps.issues.append(Issue("WBS_SUMMARY_SKIPPED", f"WBS-summary task {r.get('task_code')} is not an activity", r.get("task_code"), "INFO"))
            continue
        ext = (r.get("task_code") or "").strip()
        task_code[r["task_id"]] = ext
        seq += 1
        start = parse_date_text(r.get("target_start_date")) or parse_date_text(r.get("early_start_date"))
        finish = parse_date_text(r.get("target_end_date")) or parse_date_text(r.get("early_end_date"))
        d_h, f_h = _f(r.get("target_drtn_hr_cnt")), _f(r.get("total_float_hr_cnt"))
        wid = r.get("wbs_id")
        ps.activities.append(PActivity(
            external_id=ext, name=(r.get("task_name") or "").strip(), wbs_code=code_of.get(wid), start=start, finish=finish,
            duration_days=round(d_h / hpd, 2) if d_h is not None else None,
            total_float_days=round(f_h / hpd, 2) if f_h is not None else None,
            activity_type=TASK_TYPE.get(ttype, "TASK"), discipline_label=disc_of_task.get(r["task_id"]), sequence=seq))
        if ttype not in TASK_TYPE:
            ps.issues.append(Issue("UNKNOWN_ACTIVITY_TYPE", f"Task type {ttype} treated as TASK", ext, "WARNING"))

    # ---- relationships
    for p in rows("TASKPRED"):
        s, pr = task_code.get(p.get("task_id")), task_code.get(p.get("pred_task_id"))
        if not s or not pr:
            continue                                                    # link to/from another project or a skipped WBS task
        lag = _f(p.get("lag_hr_cnt")) or 0.0
        ps.dependencies.append(PDependency(pr, s, PRED_TYPE.get(p.get("pred_type", "PR_FS"), "FS"), round(lag / hpd, 2)))

    # ---- resources
    unit_abbrev = {u["unit_id"]: (u.get("unit_abbrev") or u.get("unit_name")) for u in rows("UMEASURE")}
    rs: Dict[str, PResource] = {}
    for r in rows("RSRC"):
        cls = RES_CLASS.get(r.get("rsrc_type", ""), "OTHER")
        uom = unit_abbrev.get(r.get("unit_id")) or ("MH" if cls == "LABOR" else "HR" if cls == "EQUIPMENT" else None)
        rs[r["rsrc_id"]] = PResource(code=_code(r.get("rsrc_short_name") or r.get("rsrc_name") or r["rsrc_id"]),
                                     name=r.get("rsrc_name") or r.get("rsrc_short_name") or r["rsrc_id"], resource_class=cls, uom_label=uom)
    used = set()
    for a in rows("TASKRSRC"):
        ext, res = task_code.get(a.get("task_id")), rs.get(a.get("rsrc_id"))
        if not ext or res is None:
            continue
        ps.assignments.append(PAssignment(ext, res.code, _f(a.get("target_qty")), res.uom_label))
        used.add(a["rsrc_id"])
    ps.resources = [rs[k] for k in rs if k in used]
    return ps
