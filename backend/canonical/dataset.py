"""
Canonical V7 demo dataset definition (pure data + derivations; no I/O against the database).

Source of truth is the repo's real benchmark under sample_data/: canonical/schedule.csv (45 activities, 34
dependencies, FS/SS/FF/SF, lags -1..5) and canonical/activity_master.csv (responsible role, inspection hold
point, safety criticality). Nothing here invents schedule logic; the only derived artefacts are
  * stages           = WBS level-1 branches (Civil, Piping, Static/Rotating, Electrical, Instrumentation, HSE)
  * stage weights    = share of summed planned duration (documented, deterministic)
  * Rev A (V1)       = an EARLIER issue of the schedule produced by the explicit REV_A_CHANGES below
  * quality gates    = one per activity that has an Inspection_Hold_Point (Safety_Critical -> HOLD category)
  * WBS hierarchy    = dotted wbs_code (1.02.02 is the parent of 1.02.02.01), see V7_CANONICAL_DATASET_SPEC.md
"""
from __future__ import annotations

import csv
import io
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample_data"
SCHEDULE_CSV = SAMPLE / "canonical" / "schedule.csv"
ACTIVITY_MASTER_CSV = SAMPLE / "canonical" / "activity_master.csv"

PROJECT_CODE = "SIH26122-NFU"
PROJECT_NAME = "North Field Utility Corridor (Pump Station 3 Tie-In)"
SIBLING_CODE = "SIH26122-NFU-B"
SIBLING_NAME = "South Yard Drainage (isolation control project)"
DATA_DATE_V2 = "2026-08-10"
DATA_DATE_V1 = "2026-07-27"

# identity key -> (email, membership role, profile role)
IDENTITIES = OrderedDict([
    ("owner", ("owner@nfu.setuai.test", "OWNER", "SUPERVISOR")),
    ("pm", ("pm@nfu.setuai.test", "PROJECT_MANAGER", "SUPERVISOR")),
    ("planner", ("planner@nfu.setuai.test", "PLANNER", "SUPERVISOR")),
    ("supervisor", ("supervisor@nfu.setuai.test", "SUPERVISOR", "SUPERVISOR")),
    ("engineer", ("engineer@nfu.setuai.test", "SITE_ENGINEER", "SITE_ENGINEER")),
    ("inspector", ("inspector@nfu.setuai.test", "QUALITY_INSPECTOR", "SUPERVISOR")),
    ("auditor", ("auditor@nfu.setuai.test", "AUDITOR", "SUPERVISOR")),
    ("outsider", ("outsider@nfu.setuai.test", "SUPERVISOR", "SUPERVISOR")),  # member of the sibling project only
])

CONTRACTORS = [  # code, company, category
    ("CON-APEX", "Apex Civil & Foundations", "CIVIL"),
    ("CON-BHARAT", "Bharat Mechanical & Piping", "MECHANICAL"),
    ("CON-COASTAL", "Coastal Electrical & Instrumentation", "ELECTRICAL"),
]
WORK_PACKAGES = [  # code, name, contractor, WBS level-1 discipline it covers
    ("WP-CIV", "Civil & Foundations", "CON-APEX", "Civil"),
    ("WP-PIP", "Utility Piping", "CON-BHARAT", "Piping"),
    ("WP-EQP", "Static & Rotating Equipment", "CON-BHARAT", "Static/Rotating Equipment"),
    ("WP-EI", "Electrical & Instrumentation", "CON-COASTAL", "Electrical"),
    ("WP-EI-INS", "Instrumentation", "CON-COASTAL", "Instrumentation"),
    ("WP-HSE", "HSE & Temporary Works", "CON-APEX", "HSE"),
]
DISCIPLINE_STAGE_ORDER = ["Civil", "Piping", "Static/Rotating Equipment", "Electrical", "Instrumentation", "HSE"]

# Rev A -> Rev B (executing baseline). Documented, deterministic, historical only (no execution against Rev A).
REV_A_CHANGES = {
    "drop_activities": ["HSE-PS3-AUD-001"],          # Rev A had no HSE close-out audit activity
    "shift_days": {"CIV-PS3-FND-003": -2, "ELE-PS3-CBL-001": -3},   # pour and cable pull were planned earlier
    "duration_delta": {"PIP-PS3-WLD-024": -1},        # welding was under-estimated in Rev A
}


def _read_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def schedule_rows():
    return _read_csv(SCHEDULE_CSV)


def activity_master():
    return {r["Activity_ID"]: r for r in _read_csv(ACTIVITY_MASTER_CSV)}


def schedule_csv_text() -> str:
    return SCHEDULE_CSV.read_text(encoding="utf-8")


def _shift(d: str, days: int) -> str:
    from datetime import date, timedelta
    y, m, dd = map(int, d.split("-"))
    return (date(y, m, dd) + timedelta(days=days)).isoformat()


def rev_a_csv_text() -> str:
    """Rev A issue: derived from Rev B by REV_A_CHANGES (drops one activity + its dependency rows, shifts, resizes)."""
    rows = schedule_rows()
    drop = set(REV_A_CHANGES["drop_activities"])
    out = []
    for r in rows:
        if r["L6 Task ID"] in drop:
            continue
        if r["Predecessor Activity ID"] in drop:
            r = {**r, "Predecessor Activity ID": "", "Relationship Type": "", "Lag Days": ""}
        aid = r["L6 Task ID"]
        r = dict(r)
        if aid in REV_A_CHANGES["shift_days"]:
            n = REV_A_CHANGES["shift_days"][aid]
            r["Baseline Start"], r["Baseline Finish"] = _shift(r["Baseline Start"], n), _shift(r["Baseline Finish"], n)
        if aid in REV_A_CHANGES["duration_delta"]:
            r["Baseline Finish"] = _shift(r["Baseline Finish"], REV_A_CHANGES["duration_delta"][aid])
        out.append(r)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(out)
    return buf.getvalue()


DISCIPLINE_KEY_TO_STAGE = {
    "CIVIL": "Civil", "PIPING": "Piping", "STATIC_ROTATING_EQUIPMENT": "Static/Rotating Equipment",
    "ELECTRICAL": "Electrical", "INSTRUMENTATION": "Instrumentation", "HSE": "HSE",
}
STAGE_CODES = {d: f"WBS-{c}" for d, c in zip(DISCIPLINE_STAGE_ORDER, ["CIV", "PIP", "EQP", "ELE", "INS", "HSE"])}


def stage_of(row) -> str:
    return DISCIPLINE_KEY_TO_STAGE[row["Discipline"]]


def stage_weights(rows=None):
    """stage name -> weight_pct summing to exactly 100.00 (proportional to summed planned duration in days)."""
    from datetime import date
    rows = rows or schedule_rows()
    dur = OrderedDict((d, 0) for d in DISCIPLINE_STAGE_ORDER)
    seen = set()
    for r in rows:
        if r["L6 Task ID"] in seen:
            continue
        seen.add(r["L6 Task ID"])
        a, b = date.fromisoformat(r["Baseline Start"]), date.fromisoformat(r["Baseline Finish"])
        dur[stage_of(r)] += (b - a).days + 1
    total = sum(dur.values())
    w = OrderedDict((d, round(100.0 * v / total, 2)) for d, v in dur.items())
    last = list(w)[-1]
    w[last] = round(100.0 - sum(v for k, v in w.items() if k != last), 2)
    return w


HOLD_OVERRIDES = {"CIV-PS3-FND-002"}  # pre-pour rebar/anchor check is a contractual hold point although not "safety critical"


def gate_spec(activity_id: str, master_row: dict) -> dict:
    """Quality gate for one activity, derived from activity_master.Inspection_Hold_Point / Safety_Critical."""
    text = master_row["Inspection_Hold_Point"]
    low = text.lower()
    if "radiograph" in low or "ndt" in low:
        gtype = "NDT"
    elif "weld" in low or "fit-up" in low:
        gtype = "WELD_INSPECTION"
    elif "sign-off" in low and "client" in low:
        gtype = "CLIENT_APPROVAL"
    elif "pre-pour" in low:
        gtype = "INSPECTION"  # follows the rebar work and releases the pour (R4); NOT a pre-commencement gate of FND-002 itself
    elif any(k in low for k in ("test", "megger", "calibration", "torque", "density")):
        gtype = "TEST"
    elif master_row["Discipline"] == "HSE":
        gtype = "SAFETY_AUDIT"
    else:
        gtype = "INSPECTION"
    hold = master_row["Safety_Critical"].strip().lower() == "yes" or activity_id in HOLD_OVERRIDES
    return {"gate_name": text, "gate_type": gtype, "checkpoint_category": "HOLD" if hold else "WITNESS"}
