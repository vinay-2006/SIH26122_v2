"""
Schedule export (CSV / Primavera-P6-style XER) from ONE source of truth: the activities and dependencies stored for
a schedule version. Pure functions over plain dict rows (as read from schedule_activities / schedule_dependencies),
so the same code exports the canonical dataset, an imported CSV or an imported XER.

Round-trip contract (asserted in tests): export -> parse_schedule_csv / parse_schedule_xer -> the same activity ids,
names, WBS codes, disciplines, planned dates, float/critical flags and the same dependency set (type + lag).

XER boundary: only what the V7 model holds is written -- PROJECT, PROJWBS (discipline node -> one leaf per WBS
code), TASK, TASKPRED. Resources, calendars, baselines, cost accounts and user-defined fields are NOT represented.
Durations are calendar-day * 8h, lags/float are stored in hours (/8 on import), matching xer_parser's conventions.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Dict, Iterable, List

CSV_HEADER = [
    "L6 Task ID", "L5 Activity ID", "Activity", "Discipline", "Unit", "Planned Qty", "Baseline Start", "Baseline Finish",
    "L1", "L2", "Predecessor Activity ID", "Relationship Type", "Lag Days", "Total Float", "Is Critical",
]
DISCIPLINE_WBS_NAME = {
    "CIVIL": "Civil Works", "PIPING": "Piping Works", "STATIC_ROTATING_EQUIPMENT": "Static and Rotating Equipment",
    "ELECTRICAL": "Electrical Works", "INSTRUMENTATION": "Instrumentation Works", "HSE": "Health Safety Environment",
}


def _dep_key(d):
    """Total order (a pair may be linked twice, e.g. FF and SS) so exports are byte-stable."""
    return (d["predecessor_activity_id"], d["relationship_type"], float(d.get("lag_days") or 0))


def _d(v) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)


def _num(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def export_schedule_csv(activities: Iterable[Dict[str, Any]], dependencies: Iterable[Dict[str, Any]]) -> str:
    """One row per activity; predecessor / relationship / lag columns hold ';'-separated lists (empty when none)."""
    preds: Dict[str, List[Dict[str, Any]]] = {}
    for d in dependencies:
        preds.setdefault(d["successor_activity_id"], []).append(d)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for a in sorted(activities, key=lambda r: r["activity_id"]):
        base = [a["activity_id"], a.get("wbs_code") or "", a["activity_name"], a["discipline"], a.get("uom") or "", _num(a.get("planned_quantity")),
                _d(a["planned_start"]), _d(a["planned_finish"]), a.get("location") or "", ""]
        tail = [_num(a.get("total_float")), "" if a.get("is_critical") is None else str(bool(a["is_critical"])).lower()]
        ps = sorted(preds.get(a["activity_id"]) or [], key=_dep_key)
        # one row per activity (the importer rejects repeated activity rows); several predecessors are ';'-separated
        pcols = [";".join(p["predecessor_activity_id"] for p in ps), ";".join(p["relationship_type"] for p in ps),
                 ";".join(_num(p.get("lag_days") or 0) for p in ps)]
        w.writerow(base + pcols + tail)
    return buf.getvalue()


def export_schedule_xer(activities: Iterable[Dict[str, Any]], dependencies: Iterable[Dict[str, Any]], *, project_short_name: str, data_date: str) -> str:
    acts = sorted(activities, key=lambda r: r["activity_id"])
    lines = ["ERMHDR\t19.12\t%s\tProject\tadmin\tSETUAI\t%s\tUSD\tDD/MM/YYYY\t1\t0\t0" % (data_date, project_short_name)]
    starts = [_d(a["planned_start"]) for a in acts]
    ends = [_d(a["planned_finish"]) for a in acts]
    lines += ["%T\tPROJECT", "%F\tproj_id\tproj_short_name\tplan_start_date\tplan_end_date",
              "%R\t1\t{}\t{} 08:00\t{} 18:00".format(project_short_name, min(starts), max(ends))]
    rows = [(100, "", "1", project_short_name)]
    nxt = 101
    disc_node: Dict[str, int] = {}
    for a in acts:
        disc = a["discipline"]
        if disc not in disc_node:
            disc_node[disc] = nxt
            rows.append((nxt, "100", f"D{nxt}", DISCIPLINE_WBS_NAME.get(disc, disc.title())))
            nxt += 1
    leaf: Dict[tuple, int] = {}
    for a in acts:
        key = (a["discipline"], a.get("wbs_code") or "WBS-MAIN")
        if key not in leaf:
            leaf[key] = nxt
            rows.append((nxt, str(disc_node[a["discipline"]]), key[1], key[1]))
            nxt += 1
    lines += ["%T\tPROJWBS", "%F\twbs_id\tproj_id\tparent_wbs_id\twbs_short_name\twbs_name"]
    lines += ["%R\t{}\t1\t{}\t{}\t{}".format(i, p, s, n) for i, p, s, n in rows]
    tid = {a["activity_id"]: 2000 + i for i, a in enumerate(acts, 1)}
    lines += ["%T\tTASK", "%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\ttarget_start_date\ttarget_end_date\ttotal_float_hr_cnt\ttarget_qty_cnt"]
    for a in acts:
        days = (a["planned_finish"] - a["planned_start"]).days + 1
        fl = "" if a.get("total_float") is None else _num(float(a["total_float"]) * 8)
        lines.append("%R\t{}\t1\t{}\t{}\t{}\tTT_Task\tTK_NotStart\t{}\t{} 08:00\t{} 18:00\t{}\t{}".format(
            tid[a["activity_id"]], leaf[(a["discipline"], a.get("wbs_code") or "WBS-MAIN")], a["activity_id"], a["activity_name"].replace("\t", " "),
            days * 8, _d(a["planned_start"]), _d(a["planned_finish"]), fl, _num(a.get("planned_quantity"))))
    lines += ["%T\tTASKPRED", "%F\ttask_pred_id\ttask_id\tpred_task_id\tproj_id\tpred_proj_id\tpred_type\tlag_hr_cnt"]
    for i, d in enumerate(sorted(dependencies, key=lambda x: (x["successor_activity_id"],) + _dep_key(x)), 1):
        lines.append("%R\t{}\t{}\t{}\t1\t1\tPR_{}\t{}".format(5000 + i, tid[d["successor_activity_id"]], tid[d["predecessor_activity_id"]],
                                                              d["relationship_type"], _num(float(d.get("lag_days") or 0) * 8) or "0"))
    lines.append("%E")
    return "\n".join(lines) + "\n"
