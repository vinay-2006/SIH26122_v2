"""Deterministic generator for the schedule-import fixtures. ONE canonical project is written as a Primavera .xer, a Microsoft Project
XML (MSPDI), and a CSV + resource CSV, so the three parsers can be proven to read the same schedule. `revision=1` produces the
revised plan used by the reconciliation tests. Run:  python tests/schedule_import/make_fixtures.py"""
from __future__ import annotations

import csv
import io
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.v2.schedule_import.models import working_days  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures"
PROJECT_NAME, PROJECT_CODE = "Northern Spur Test Pipeline", "NSP-TEST"
DATA_DATE, P_START, P_FINISH = date(2026, 1, 5), date(2026, 1, 12), date(2026, 7, 31)
D = lambda m, d: date(2026, m, d)

R = "Northern Spur Test Pipeline"
W_S1, W_S1A, W_S1B = [R, "Civil & ROW Preparation"], [R, "Civil & ROW Preparation", "Right of Way"], [R, "Civil & ROW Preparation", "Foundations"]
W_S2M = [R, "Pipeline Construction", "Mainline Section 1"]
W_S3 = [R, "Commissioning"]
# resource code -> (name, class, xer/csv unit label, msp material label)
RES = {
    "ROW_SURVEY_KM": ("Right of way survey", "MATERIAL", "km"), "CLEARED_ROW_KM": ("Cleared and graded ROW", "MATERIAL", "km"),
    "EXCAVATOR_HOURS": ("Excavator hours", "EQUIPMENT", "hr"), "MANHOURS": ("Manhours", "LABOR", "mh"),
    "CONCRETE_M3": ("Concrete", "MATERIAL", "m3"), "STEEL_TONNES": ("Reinforcement steel", "MATERIAL", "tonne"),
    "PIPE_STRUNG_KM": ("Pipe strung", "MATERIAL", "km"), "WELD_JOINTS": ("Weld joints", "MATERIAL", "joints"),
    "BACKFILL_M3": ("Backfill", "MATERIAL", "m3"), "TEST_SECTIONS": ("Hydrotest sections", "MATERIAL", "nos"),
    "LOOP_CHECKS": ("Instrument loop checks", "MATERIAL", "nos"),
}


def canon(revision: int = 0):
    """list of activity dicts: id, name, wbs(list of names), disc, type, start, finish, float, preds[(pred, type, lag)], res[(code, qty)]"""
    A = []
    def a(i, name, wbs, disc, s, f, flt, preds=(), res=(), typ="TASK"):
        A.append(dict(id=i, name=name, wbs=wbs, disc=disc, type=typ, start=s, finish=f, float=flt, preds=list(preds), res=list(res)))
    shift = timedelta(days=7) if revision else timedelta(0)
    a("A0100", "Statutory approvals and clearances", W_S1, "Safety", D(1, 12), D(2, 28), 3)
    a("A1000", "Survey and stake right of way", W_S1A, "Civil Works", D(1, 12) + shift, D(1, 31) + shift, 0,
      res=[("ROW_SURVEY_KM", 24), ("MANHOURS", 800)])
    a("CG-1010" if revision else "A1010", "Clear and grade right of way", W_S1A, "Civil Works", D(2, 2), D(3, 14), 0,
      [("A1000", "FS", 0)], [("CLEARED_ROW_KM", 24), ("EXCAVATOR_HOURS", 1200), ("MANHOURS", 3000)])
    a("A1020", "Pour pump station foundations", W_S1B, "CIVIL", D(2, 16), D(3, 7), 12, [("A1000", "SS", 5)],
      [("CONCRETE_M3", 600 if revision else 480), ("STEEL_TONNES", 36), ("MANHOURS", 2400)])
    prev = "CG-1010" if revision else "A1010"
    if revision:                                                        # A2000 split into two sections of 12 km
        a("A2000-1", "Pipe stringing - section 1", W_S2M, "Piping Works", D(3, 16), D(3, 28), 0, [(prev, "FS", 0)], [("PIPE_STRUNG_KM", 12), ("MANHOURS", 1300)])
        a("A2000-2", "Pipe stringing - section 2", W_S2M, "Piping Works", D(3, 30), D(4, 11), 0, [("A2000-1", "FS", 0)], [("PIPE_STRUNG_KM", 12), ("MANHOURS", 1300)])
        weld_pred = "A2000-1"
    else:
        a("A2000", "Pipe stringing", W_S2M, "Piping Works", D(3, 16), D(4, 11), 0, [(prev, "FS", 0)], [("PIPE_STRUNG_KM", 24), ("MANHOURS", 2600)])
        weld_pred = "A2000"
    a("A2010", "Welding mainline", W_S2M, "Piping Works", D(3, 30), D(5, 16), 0, [(weld_pred, "SS", 10)], [("WELD_JOINTS", 2000), ("MANHOURS", 9000)])
    if not revision:                                                    # removed in the revision
        a("A2030", "Lower-in and backfill", W_S2M, "Civil Works", D(5, 4), D(6, 6), 0, [("A2010", "FF", 0)], [("BACKFILL_M3", 14000), ("MANHOURS", 5200)])
        after = "A2030"
    else:
        after = "A2010"
    if revision:                                                        # A3000 + A3001 merged into one
        a("A3000M", "Hydrostatic test sections 1 and 2", W_S3, "Process", D(6, 8), D(6, 20), 0, [(after, "FS", 0)], [("TEST_SECTIONS", 4), ("MANHOURS", 600)])
        last_test = "A3000M"
    else:
        a("A3000", "Hydrostatic test section 1", W_S3, "Process", D(6, 8), D(6, 13), 0, [(after, "FS", 0)], [("TEST_SECTIONS", 2), ("MANHOURS", 300)])
        a("A3001", "Hydrostatic test section 2", W_S3, "Process", D(6, 15), D(6, 20), 0, [("A3000", "FS", 0)], [("TEST_SECTIONS", 2), ("MANHOURS", 300)])
        last_test = "A3001"
    a("A3010", "Instrument loop checks", W_S3, "Instrumentation", D(6, 8), D(6, 27), 4, [(after, "FS", 0)], [("LOOP_CHECKS", 64), ("MANHOURS", 900)])
    a("A3020", "Electrical energisation", W_S3, "Electrical", D(6, 22), D(6, 27), 4, [("A3010", "SS", 3)])
    if revision:
        a("A4000", "Cathodic protection installation", W_S3, "Electrical", D(6, 22), D(7, 18), 6, [("A3020", "SS", 0)], [("MANHOURS", 700)])
    a("A3030", "Commissioning complete", W_S3, "Process", D(7, 31), D(7, 31), 0, [(last_test, "FS", 0), ("A3020", "FS", 0)], typ="MILESTONE")
    for x in A:
        x["dur"] = 0 if x["type"] == "MILESTONE" else working_days(x["start"], x["finish"])
    return A


def wbs_nodes(A):
    """ordered unique (path tuple) nodes incl. ancestors"""
    seen, out = set(), []
    for x in A:
        for i in range(1, len(x["wbs"]) + 1):
            p = tuple(x["wbs"][:i])
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def iso(d): return d.isoformat()


# ----------------------------------------------------------------------------------------------------------- XER
def write_xer(A) -> str:
    L = []
    def table(name, fields, rows):
        L.append(f"%T\t{name}")
        L.append("%F\t" + "\t".join(fields))
        for r in rows:
            L.append("%R\t" + "\t".join("" if v is None else str(v) for v in r))
    L.append("ERMHDR\t19.12\t2026-01-05\tProject\tadmin\tprimavera\tProject Management\tUSD")
    table("CURRTYPE", ["curr_id", "curr_short_name"], [(1, "USD")])
    table("CALENDAR", ["clndr_id", "clndr_name", "day_hr_cnt", "week_hr_cnt"], [(10, "Six-day site calendar", 8, 48)])
    table("PROJECT", ["proj_id", "proj_short_name", "clndr_id", "plan_start_date", "scd_end_date", "last_recalc_date"],
          [(1001, PROJECT_CODE, 10, f"{iso(P_START)} 08:00", f"{iso(P_FINISH)} 17:00", f"{iso(DATA_DATE)} 00:00")])
    nodes = wbs_nodes(A)
    wid = {p: 5000 + i for i, p in enumerate(nodes, start=1)}
    rows = []
    for i, p in enumerate(nodes, start=1):
        short = "NSP" if len(p) == 1 else {"Civil & ROW Preparation": "S1", "Pipeline Construction": "S2", "Commissioning": "S3",
                                         "Right of Way": "ROW", "Foundations": "FND", "Mainline Section 1": "ML1"}[p[-1]]
        rows.append((wid[p], 1001, short, p[-1], wid.get(p[:-1], ""), "Y" if len(p) == 1 else "N", i))
    table("PROJWBS", ["wbs_id", "proj_id", "wbs_short_name", "wbs_name", "parent_wbs_id", "proj_node_flag", "seq_num"], rows)
    discs = sorted({x["disc"] for x in A})
    table("ACTVTYPE", ["actv_code_type_id", "actv_code_type", "proj_id"], [(701, "Discipline", 1001)])
    table("ACTVCODE", ["actv_code_id", "actv_code_type_id", "short_name", "actv_code_name"],
          [(800 + i, 701, d, d) for i, d in enumerate(discs, start=1)])
    tid = {x["id"]: 9000 + i for i, x in enumerate(A, start=1)}
    TT = {"TASK": "TT_Task", "MILESTONE": "TT_FinMile", "LOE": "TT_LOE"}
    table("TASK", ["task_id", "proj_id", "wbs_id", "clndr_id", "task_code", "task_name", "task_type", "status_code",
                   "target_drtn_hr_cnt", "total_float_hr_cnt", "target_start_date", "target_end_date"],
          [(tid[x["id"]], 1001, wid[tuple(x["wbs"])], 10, x["id"], x["name"], TT[x["type"]], "TK_NotStart", x["dur"] * 8,
            x["float"] * 8, f"{iso(x['start'])} 08:00", f"{iso(x['finish'])} 17:00") for x in A])
    table("TASKACTV", ["task_id", "actv_code_type_id", "actv_code_id", "proj_id"],
          [(tid[x["id"]], 701, 800 + discs.index(x["disc"]) + 1, 1001) for x in A])
    preds = [(x["id"], p) for x in A for p in x["preds"]]
    table("TASKPRED", ["task_pred_id", "task_id", "pred_task_id", "proj_id", "pred_proj_id", "pred_type", "lag_hr_cnt"],
          [(7000 + i, tid[s], tid[p[0]], 1001, 1001, "PR_" + p[1], p[2] * 8) for i, (s, p) in enumerate(preds, start=1)])
    units = sorted({v[2] for k, v in RES.items() if v[1] == "MATERIAL"})
    table("UMEASURE", ["unit_id", "unit_abbrev", "unit_name"], [(300 + i, u, u) for i, u in enumerate(units, start=1)])
    used = [k for k in RES if any(r[0] == k for x in A for r in x["res"])]
    rid = {k: 100 + i for i, k in enumerate(used, start=1)}
    TYPE = {"MATERIAL": "RT_Mat", "LABOR": "RT_Labor", "EQUIPMENT": "RT_Equip"}
    table("RSRC", ["rsrc_id", "rsrc_short_name", "rsrc_name", "rsrc_type", "unit_id"],
          [(rid[k], k, RES[k][0], TYPE[RES[k][1]], (300 + units.index(RES[k][2]) + 1) if RES[k][1] == "MATERIAL" else "") for k in used])
    table("TASKRSRC", ["taskrsrc_id", "task_id", "rsrc_id", "proj_id", "target_qty"],
          [(8000 + i, tid[x["id"]], rid[c], 1001, q) for i, (x, c, q) in enumerate([(x, c, q) for x in A for c, q in x["res"]], start=1)])
    L.append("%E")
    return "\r\n".join(L) + "\r\n"


# ----------------------------------------------------------------------------------------------------------- MSPDI
NS = "http://schemas.microsoft.com/project"


def write_mspdi(A) -> str:
    ET.register_namespace("", NS)
    q = lambda t: f"{{{NS}}}{t}"
    root = ET.Element(q("Project"))
    def sub(p, tag, text=None):
        e = ET.SubElement(p, q(tag))
        if text is not None:
            e.text = str(text)
        return e
    sub(root, "Name", "nsp.xml"); sub(root, "Title", PROJECT_NAME)
    sub(root, "StartDate", f"{iso(P_START)}T08:00:00"); sub(root, "FinishDate", f"{iso(P_FINISH)}T17:00:00")
    sub(root, "StatusDate", f"{iso(DATA_DATE)}T00:00:00"); sub(root, "MinutesPerDay", 480)
    eas = sub(root, "ExtendedAttributes")
    for fid, alias in (("188743731", "Discipline"), ("188743734", "Activity ID")):
        ea = sub(eas, "ExtendedAttribute"); sub(ea, "FieldID", fid); sub(ea, "FieldName", "Text"); sub(ea, "Alias", alias)
    tasks = sub(root, "Tasks")
    nodes = wbs_nodes(A)
    uid = {}
    n = 0
    def task(**kw):
        t = sub(tasks, "Task")
        for k, v in kw.items():
            if v is not None:
                sub(t, k, v)
        return t
    task(UID=0, ID=0, Name=PROJECT_NAME, Summary=1, OutlineLevel=0, OutlineNumber="0", WBS="0")   # project summary task = the WBS root
    outline_no = {}
    counters = {}
    ordered = []
    def emit(path):
        """path of len 1 is the project summary task itself (UID 0); deeper nodes are summary tasks below it."""
        nonlocal n
        if len(path) == 1:
            no = ""
            uid[("W", path)] = 0
        else:
            n += 1
            counters[path[:-1]] = counters.get(path[:-1], 0) + 1
            parent_no = outline_no.get(path[:-1], "")
            no = f"{parent_no}.{counters[path[:-1]]}" if parent_no else str(counters[path[:-1]])
            task(UID=n, ID=n, Name=path[-1], Summary=1, OutlineLevel=len(path) - 1, OutlineNumber=no, WBS=no)
            uid[("W", path)] = n
        outline_no[path] = no
        for x in A:
            if tuple(x["wbs"]) == path:
                n += 1
                counters[path] = counters.get(path, 0) + 1
                ano = f"{no}.{counters[path]}" if no else str(counters[path])
                uid[x["id"]] = n
                ordered.append((x, n, ano))
                task(UID=n, ID=n, Name=x["name"], Summary=0, OutlineLevel=len(path), OutlineNumber=ano, WBS=ano,
                     Milestone=1 if x["type"] == "MILESTONE" else 0, Start=f"{iso(x['start'])}T08:00:00",
                     Finish=f"{iso(x['finish'])}T17:00:00", Duration=f"PT{x['dur'] * 8}H0M0S",
                     TotalSlack=int(x["float"] * 480 * 10))
        for child in [p for p in nodes if p[:-1] == path]:
            emit(child)
    for top in [p for p in nodes if len(p) == 1]:
        emit(top)
    # second pass: predecessor links + extended attributes (need all UIDs)
    by_uid = {t.find(q("UID")).text: t for t in tasks.findall(q("Task"))}
    for x, u, _ in ordered:
        t = by_uid[str(u)]
        for p in x["preds"]:
            pl = sub(t, "PredecessorLink")
            sub(pl, "PredecessorUID", uid[p[0]]); sub(pl, "Type", {"FF": 0, "FS": 1, "SF": 2, "SS": 3}[p[1]])
            sub(pl, "LinkLag", int(p[2] * 480 * 10)); sub(pl, "LagFormat", 7)
        for fid, val in (("188743731", x["disc"]), ("188743734", x["id"])):
            ea = sub(t, "ExtendedAttribute"); sub(ea, "FieldID", fid); sub(ea, "Value", val)
    resources = sub(root, "Resources")
    used = [k for k in RES if any(r[0] == k for x in A for r in x["res"])]
    ruid = {k: i for i, k in enumerate(used, start=1)}
    for k in used:
        r = sub(resources, "Resource"); sub(r, "UID", ruid[k]); sub(r, "ID", ruid[k]); sub(r, "Name", k)
        if RES[k][1] == "MATERIAL":
            sub(r, "Type", 0); sub(r, "MaterialLabel", RES[k][2])
        else:
            sub(r, "Type", 1); sub(r, "Group", "Equipment" if RES[k][1] == "EQUIPMENT" else "Labor")
    assigns = sub(root, "Assignments")
    i = 0
    for x, u, _ in ordered:
        for c, qty in x["res"]:
            i += 1
            a = sub(assigns, "Assignment"); sub(a, "UID", i); sub(a, "TaskUID", u); sub(a, "ResourceUID", ruid[c])
            if RES[c][1] == "MATERIAL":
                sub(a, "Units", qty)
            else:
                sub(a, "Units", 1); sub(a, "Work", f"PT{qty}H0M0S")
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ----------------------------------------------------------------------------------------------------------- CSV
def write_csv(A):
    ds = lambda d: d.strftime("%d-%m-%Y")                       # Indian day-first
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\r\n")
    w.writerow(["Activity ID", "Activity Name", "WBS Path", "Discipline", "Activity Type", "Baseline Duration", "Baseline Start",
                "Baseline Finish", "Total Float", "Predecessors"])
    for x in A:
        preds = ";".join(f"{p[0]}{p[1]}{'+%g' % p[2] if p[2] else ''}" for p in x["preds"])
        w.writerow([x["id"], x["name"], " > ".join(x["wbs"]), x["disc"], x["type"].title(), x["dur"], ds(x["start"]), ds(x["finish"]),
                    x["float"], preds])
    r = io.StringIO()
    rw = csv.writer(r, lineterminator="\r\n")
    rw.writerow(["Activity ID", "Resource ID", "Resource Name", "Resource Class", "Baseline Qty", "Unit"])
    for x in A:
        for c, qty in x["res"]:
            rw.writerow([x["id"], c, RES[c][0], RES[c][1].title(), qty, RES[c][2]])
    return b.getvalue(), r.getvalue()


DISC_CODE = {"Safety": "HSE", "Civil Works": "CIVIL", "CIVIL": "CIVIL", "Piping Works": "PIPING", "Process": "PROCESS",
             "Instrumentation": "INSTRUMENTATION", "Electrical": "ELECTRICAL"}
UOM_CODE = {"km": "KM", "hr": "HR", "mh": "MH", "m3": "M3", "tonne": "TONNE", "joints": "JOINT", "nos": "NOS"}


def expected_json(A) -> dict:
    """The golden: what ANY correct parser must produce for this schedule, derived from the canonical definition (never from a parser)."""
    return {
        "project": {"data_date": iso(DATA_DATE), "planned_start": iso(P_START), "planned_finish": iso(P_FINISH)},
        "wbs_paths": sorted(" > ".join(p) for p in wbs_nodes(A)),
        "activities": {x["id"]: {
            "name": x["name"], "wbs_path": " > ".join(x["wbs"]), "discipline": DISC_CODE[x["disc"]], "type": x["type"],
            "start": iso(x["start"]), "finish": iso(x["finish"]), "duration_days": float(x["dur"]), "total_float_days": float(x["float"]),
            "predecessors": sorted([p[0], p[1], float(p[2])] for p in x["preds"]),
            "resources": sorted([c, float(q), UOM_CODE[RES[c][2]]] for c, q in x["res"])} for x in A}}


def main():
    OUT.mkdir(exist_ok=True)
    for rev, tag in ((0, "nsp"), (1, "nsp_rev1")):
        A = canon(rev)
        (OUT / f"{tag}.xer").write_text(write_xer(A), newline="")
        (OUT / f"{tag}_mspdi.xml").write_text(write_mspdi(A))
        a, r = write_csv(A)
        (OUT / f"{tag}.csv").write_text(a, newline="")
        (OUT / f"{tag}_resources.csv").write_text(r, newline="")
        (OUT / f"{tag}_expected.json").write_text(__import__("json").dumps(expected_json(A), indent=1, sort_keys=True) + "\n")
    print("fixtures written to", OUT)


if __name__ == "__main__":
    main()
