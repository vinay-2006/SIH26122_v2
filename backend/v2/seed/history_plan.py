"""Deterministic execution-history PLAN for a seeded project: which claims were filed when, by whom, with what figures, and what the Supervisor
did with each. Pure: no database, no clock, no randomness (every 'random' choice is a SHA-256 of stable names). The runner executes the plan through
the real domain services, so every row of history is created by the same code paths production uses.

Rules the plan respects (so the services accept it): claims are cumulative and strictly increasing per activity; dates strictly increase per activity;
the last claim of a finished activity carries 100% of every measured assignment and its finish date; nothing is dated after the anchor date."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .projects_spec import ProjectSpec
from .schedule_gen import Act, add_work, is_work, next_work, work_between

COUNT_UNITS = {"nos", "joints", "set"}


def h(*parts) -> float:
    """stable pseudo-random number in [0, 1)"""
    d = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2 ** 64


def hi(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()[:8], "big")


@dataclass
class Step:
    act_id: str
    seq: int
    n: int
    on: date
    fraction: float                      # cumulative fraction of baseline this claim reports
    mode: str                            # QTY | PCT | DATES
    engineer: str
    start: Optional[date] = None         # claimed actual start (first claim)
    finish: Optional[date] = None        # claimed actual finish (final claim of a finished activity)
    final: bool = False
    disposition: str = "APPROVE"         # APPROVE EDIT REJECT_CORRECT REJECT_ONLY REJECT_CORRECT_PENDING PENDING HOLD_LOOP HOLD_OPEN HOLD_ANSWERED APPROVE_PCT
    overrun: float = 1.0                 # factor applied to the baseline quantity on the final claim of a finished activity
    qty: Dict[str, float] = field(default_factory=dict)       # resource code -> cumulative quantity reported
    pct: Optional[float] = None


@dataclass
class Withdrawal:
    act_id: str
    on: date
    engineer: str
    reason: str


@dataclass
class IssueSpec:
    key: str
    title: str
    category: str
    act_id: Optional[str]
    stage: Optional[str]
    severity: str
    blocks: bool
    reported_by: str                     # engineer handle or 'SUP'
    reported_on: date
    description: str
    estimate_days: float
    resolved_days: Optional[int]         # None = still active
    actual_days: Optional[float] = None
    notes: str = ""
    root_cause: Optional[str] = None
    memory: Optional[str] = None
    evidence: bool = False


@dataclass
class RootCauseSpec:
    key: str
    title: str
    category: str
    summary: str


@dataclass
class Plan:
    code: str
    anchor: date
    state: Dict[str, str] = field(default_factory=dict)         # act id -> COMPLETE | PARTIAL | NONE
    steps: List[Step] = field(default_factory=list)
    withdrawals: List[Withdrawal] = field(default_factory=list)
    issues: List[IssueSpec] = field(default_factory=list)
    root_causes: List[RootCauseSpec] = field(default_factory=list)


def measured(a: Act) -> List[Tuple[str, float, str]]:
    return [(r[0], r[3], r[4]) for r in a.res if r[2] == "Material"]


def qty_for(res: Tuple[str, float, str], fraction: float, jitter: float) -> float:
    code, base, unit = res
    q = base * fraction * (1 + jitter)
    return float(round(q)) if unit in COUNT_UNITS else round(q, 2)


def _engineers_for(spec: ProjectSpec, a: Act) -> str:
    return spec.engineers[hi(spec.code, "eng", a.wbs[0]) % len(spec.engineers)]


def build_plan(spec: ProjectSpec, anchor: date) -> Plan:
    plan = Plan(spec.code, anchor)
    if spec.lifecycle == "UPCOMING":
        plan.state = {a.id: "NONE" for a in spec.acts}
        return plan
    completed_project = spec.lifecycle == "COMPLETED"
    actual: Dict[str, Tuple[date, Optional[date]]] = {}        # act id -> (actual start, actual finish or None) for started activities
    for a in spec.acts:
        st, g, steps = _plan_activity(spec, a, anchor, completed_project, plan.state, actual)
        plan.state[a.id] = st
        plan.steps.extend(steps)
        if steps:
            actual[a.id] = (steps[0].start or steps[0].on, steps[-1].finish if st == "COMPLETE" else None)
    plan.steps.sort(key=lambda s: (s.on, s.act_id, s.seq))
    _assign_dispositions(spec, plan)
    if spec.lifecycle == "ONGOING":                                 # an engineer picks the wrong activity and takes the claim back
        wrong = [a for a in spec.acts if plan.state[a.id] == "NONE" and not a.milestone and a.start <= anchor + timedelta(days=150)][:2]
        for i, a in enumerate(wrong):
            plan.withdrawals.append(Withdrawal(a.id, anchor - timedelta(days=3 + i), spec.engineers[i % len(spec.engineers)],
                                               "Entered against the wrong activity; the work was done under the previous activity" if i == 0 else "Duplicate of a claim already filed by the other engineer"))
    build_issues(spec, plan)
    return plan


def _slip(spec: ProjectSpec, a: Act, kind: str, hi_days: int) -> int:
    return int(h(spec.code, a.id, kind) * hi_days)


def _plan_activity(spec: ProjectSpec, a: Act, anchor: date, completed_project: bool, state: Dict[str, str], actual: Dict[str, Tuple[date, Optional[date]]]):
    start = add_work(a.start, _slip(spec, a, "start", 9)) if not a.milestone else a.start
    ms = a.milestone
    for pid, typ, lag in a.preds:                                   # work follows the logic network
        if typ == "FS":
            if state.get(pid) != "COMPLETE":
                return "NONE", 0.0, []
            pf = actual[pid][1]
            start = max(start, pf if ms else pf + timedelta(days=1))
        else:                                                       # SS: the predecessor must have started
            if state.get(pid) in (None, "NONE"):
                return "NONE", 0.0, []
            start = max(start, actual[pid][0] + timedelta(days=lag))
    start = next_work(start) if not ms else start
    if completed_project:
        finish = a.finish + timedelta(days=_slip(spec, a, "finish", 45) + (_slip(spec, a, "start", 9)))
        finish = min(max(finish, start + timedelta(days=0 if ms else 2)), anchor - timedelta(days=2))
        g, status = 1.0, "COMPLETE"
    else:
        if a.start > anchor:
            return "NONE", 0.0, []
        span = max(a.dur, 1)
        f = min(1.0, (work_between(a.start, anchor) + 1) / span) if not ms else (1.0 if a.finish <= anchor else 0.0)
        perf = 0.62 + 0.46 * h(spec.code, a.id, "perf")                 # 0.62 .. 1.08 of the planned pace
        g = min(1.0, f * perf)
        if f >= 1.0 and (perf >= 0.8 or a.finish <= anchor - timedelta(days=75)):
            g = 1.0
        elif f >= 1.0:
            g = 0.7 + 0.26 * h(spec.code, a.id, "late")
        status = "COMPLETE" if g >= 1.0 else ("PARTIAL" if g >= 0.04 else "NONE")
        if status == "NONE":
            return "NONE", 0.0, []
        if status == "PARTIAL":
            g = min(g, 0.97)
        finish = anchor - timedelta(days=1 + _slip(spec, a, "last", 6))
        if status == "COMPLETE":
            finish = min(add_work(a.finish, _slip(spec, a, "finish", 25)), anchor - timedelta(days=1))
            finish = max(finish, start + timedelta(days=2 if not ms else 0))
            if finish > anchor - timedelta(days=1):
                return "NONE", 0.0, []
        if start > anchor - timedelta(days=2):
            return "NONE", 0.0, []
    meas = measured(a)
    eng = _engineers_for(spec, a)
    if ms:
        return "COMPLETE", 1.0, [Step(a.id, 1, 1, finish, 1.0, "DATES", eng, finish=finish, final=True)]
    n = 3 if status == "COMPLETE" and a.dur >= 10 else (2 if status == "COMPLETE" else 1 + int(g * 3))
    n = max(1, min(n, 4))
    steps: List[Step] = []
    first_on = start + timedelta(days=3 + _slip(spec, a, "first", 4)) if a.dur > 4 else start
    first_on = min(first_on, finish if status == "COMPLETE" else anchor - timedelta(days=1))
    last_on = finish if status == "COMPLETE" else finish
    last_on = max(last_on, first_on)
    for k in range(1, n + 1):
        on = first_on if n == 1 else first_on + timedelta(days=round((last_on - first_on).days * (k - 1) / (n - 1)))
        if steps and on <= steps[-1].on:
            on = steps[-1].on + timedelta(days=3)
        if on > anchor - timedelta(days=1):
            on = anchor - timedelta(days=1)
        frac = g * k / n if not (status == "COMPLETE" and k == n) else 1.0
        s = Step(a.id, k, n, on, round(frac, 4), "QTY" if meas else "PCT", _engineers_for(spec, a),
                 start=start if k == 1 else None, finish=on if (status == "COMPLETE" and k == n) else None, final=(k == n))
        steps.append(s)
    # monotone dates (the clamp above can collide when an activity ends right at the anchor)
    for i in range(1, len(steps)):
        if steps[i].on <= steps[i - 1].on:
            steps[i].on = steps[i - 1].on + timedelta(days=1)
    if steps[-1].on > anchor - timedelta(days=1):                           # too tight: collapse to one claim
        steps = [steps[-1]]
        steps[0].on, steps[0].seq, steps[0].n, steps[0].start = anchor - timedelta(days=1), 1, 1, start
        if steps[0].start > steps[0].on:
            steps[0].start = steps[0].on
    for s in steps:
        if s.start and s.start > s.on:
            s.start = s.on
        if s.finish:
            s.finish = s.on
        if meas:
            for code, base, unit in [(m[0], m[1], m[2]) for m in meas]:
                jit = 0.0 if s.final and status == "COMPLETE" else (h(spec.code, a.id, code, "jit") - 0.5) * 0.06
                s.qty[code] = qty_for((code, base, unit), s.fraction, jit if s.fraction < 1.0 else 0.0)
        else:
            s.pct = 100.0 if s.fraction >= 1.0 else float(max(1, int(s.fraction * 100)))
    # the cumulative figures must never go down
    for code in {c for s in steps for c in s.qty}:
        prev = 0.0
        for s in steps:
            if s.qty[code] <= prev:
                unit = next(m[2] for m in meas if m[0] == code)
                s.qty[code] = prev + (1.0 if unit in COUNT_UNITS else 0.01)
            prev = s.qty[code]
    return status, g, steps


def _assign_dispositions(spec: ProjectSpec, plan: Plan) -> None:
    by_act: Dict[str, List[Step]] = {}
    for s in plan.steps:
        by_act.setdefault(s.act_id, []).append(s)
    acts = {a.id: a for a in spec.acts}
    rank = lambda tag, s: h(spec.code, tag, s.act_id, s.seq)
    mid = [s for s in plan.steps if not s.final and s.mode == "QTY"]
    finals_partial = [by[-1] for aid, by in by_act.items() if plan.state[aid] == "PARTIAL"]
    finals_complete_qty = [by[-1] for aid, by in by_act.items() if plan.state[aid] == "COMPLETE" and by[-1].mode == "QTY"]
    taken: set = set()
    used_acts: set = set()

    def pick(pool, tag, k, pred=lambda s: True, distinct=True):
        out = []
        for s in sorted(pool, key=lambda s: rank(tag, s)):
            if len(out) == k:
                break
            if (s.act_id, s.seq) in taken or not pred(s) or (distinct and s.act_id in used_acts):
                continue
            taken.add((s.act_id, s.seq))
            used_acts.add(s.act_id)
            out.append(s)
        return out

    completed = spec.lifecycle == "COMPLETED"
    ongoing = spec.lifecycle == "ONGOING"
    for s in pick(mid, "edit", max(3, len(mid) // 10)):
        s.disposition = "EDIT"
    for s in pick(mid, "rc", 4 if completed else 2):
        s.disposition = "REJECT_CORRECT"
    if ongoing:
        for s in pick(mid, "ro", 1):
            s.disposition = "REJECT_ONLY"
        for s in pick(mid, "rcp", 1):
            s.disposition = "REJECT_CORRECT_PENDING"
        if spec.code.startswith("NRL"):
            for s in pick([s for s in finals_partial if s.mode == "QTY"], "pct", 3):
                s.disposition = "APPROVE_PCT"
                s.mode = "PCT"
                s.pct = float(max(1, int(s.fraction * 100)))
                s.qty = {}
        for s in pick(finals_partial, "hold_open", 1):
            s.disposition = "HOLD_OPEN"
        loops = 2 if spec.code.startswith("NRL") else 1
        for s in pick(mid + finals_partial, "loop", loops):
            s.disposition = "HOLD_LOOP"
        if spec.code.startswith("NRL"):
            for s in pick(finals_partial, "answered", 1):
                s.disposition = "HOLD_ANSWERED"
        for s in pick(finals_partial, "pend", max(3, len(finals_partial) // 3)):
            s.disposition = "PENDING"
    if ongoing:                                                     # the newest claims have not been reviewed yet: that is what a review backlog is
        for s in plan.steps:
            if s.disposition == "APPROVE" and s.on >= plan.anchor - timedelta(days=12):
                s.disposition = "PENDING"
    # over-baseline figures on finished activities (never clamped; beyond tolerance needs the supervisor's acknowledgement)
    def measurable_overrun(s):                                       # a 4% overrun on a count of 8 would round away: pick activities where it is visible
        m = measured(acts[s.act_id])
        return bool(m) and m[0][2] not in COUNT_UNITS and m[0][1] >= 20
    cands = sorted([c for c in finals_complete_qty if measurable_overrun(c)], key=lambda s: rank("over", s))
    within = 1 if completed else 2
    for s in [c for c in cands if c.disposition == "APPROVE"][:within]:
        s.overrun = 1.04 + 0.04 * h(spec.code, "ov", s.act_id)
    if spec.code.startswith("NRL"):
        for s in [c for c in cands if c.disposition == "APPROVE" and c.overrun == 1.0][:1]:
            s.overrun = 1.13
    for s in plan.steps:
        if s.overrun != 1.0:
            a = acts[s.act_id]
            for code, base, unit in measured(a):
                if code == measured(a)[0][0]:
                    s.qty[code] = qty_for((code, base, unit), s.overrun, 0.0)


# ------------------------------------------------------------------------------------------------ issues, delays, root causes
def _I(key, title, cat, act, sev, blocks, by, off, desc, est, res_days, actual=None, notes="", rc=None, memory=None, evidence=False, stage=None):
    return dict(key=key, title=title, category=cat, act=act, stage=stage, severity=sev, blocks=blocks, by=by, off=off, desc=desc, est=est, res=res_days, actual=actual,
                notes=notes, rc=rc, memory=memory, evidence=evidence)


ISSUES: Dict[str, dict] = {
    "NNB-CRUDE": dict(
        root_causes=[("RC-MONSOON", "Monsoon flooding of the Brahmaputra and Kosi floodplains", "WEATHER", "Two spreads lost working days to the same seasonal flooding that the baseline did not allow for."),
                     ("RC-LAND", "Land title disputes along the ROW", "SITE_ACCESS", "Unsettled parcels forced ROW to be released in pieces."),
                     ("RC-RAILPERMIT", "Railway crossing approvals serialised through one divisional office", "PERMIT_APPROVAL", "Approvals for cased crossings queued behind each other.")],
        issues=[
            _I("A1", "ROW flooded - trench collapse risk", "WEATHER", "NNB-3250", "HIGH", True, "SUP", 20, "Floodwater filled the open trench over 3 km; trenching stopped.", 12, 14, 14, "Pumped out, trench re-profiled, dewatering added.", "RC-MONSOON"),
            _I("A2", "Kosi floodplain access road washed out", "WEATHER", "NNB-3360", "HIGH", True, "debojit.gogoi", 25, "Haul road washed out; sideboom spread could not reach lowering-in location.", 10, 9, 9, "Temporary causeway built.", "RC-MONSOON"),
            _I("A3", "Frac-out during pilot hole of the Brahmaputra HDD", "TECHNICAL", "NNB-4010", "CRITICAL", True, "pranav.rao", 15, "Drilling fluid lost to the riverbed; pilot hole abandoned and re-drilled on a deeper profile.", 21, 25, 24,
               "Redesigned profile approved; fluid additives changed; monitoring added.", None, "Run a geotechnical review and a frac-out contingency plan before any river HDD; keep bentonite and sealing agents on the rig."),
            _I("A4", "Parcel dispute stops ROW release at chainage 42", "SITE_ACCESS", "NNB-1010", "MEDIUM", False, "nirmali.saikia", 70, "Two landowners contested compensation; 1.2 km of ROW unavailable.", 30, 38, 33, "Settled through district administration.", "RC-LAND"),
            _I("A5", "Line pipe delivery slipped by three weeks", "MATERIAL_DELIVERY_DELAY", "NNB-2000", "HIGH", True, "SUP", 60, "Mill rolling programme slipped; first stockyard lot arrived late.", 21, 20, 22, "Second mill used for the balance.", None),
            _I("A6", "Welder shortage on Spread 2 after festival break", "LABOUR_SHORTAGE", "NNB-3220", "MEDIUM", False, "pranav.rao", 30, "Qualified welders did not return on time.", 6, 7, 6, "Additional gang mobilised from another spread.", None),
            _I("A7", "Railway authority approval for cased crossing delayed", "PERMIT_APPROVAL", "NNB-4100", "HIGH", True, "debojit.gogoi", 20, "Block-closure permission not granted for the planned window.", 28, 35, 31,
               "Escalated to the divisional office; alternate window agreed.", "RC-RAILPERMIT", "File rail-crossing applications for all crossings together at the start, with the block-closure calendar attached."),
            _I("A8", "Vibration non-conformance on a pump skid", "TECHNICAL", "NNB-5120", "MEDIUM", False, "nirmali.saikia", 10, "Skid baseplate grout voids found at alignment check.", 8, 10, 8, "Re-grouted and re-aligned.", None, evidence=True),
        ]),
    "AEC-OFFSHORE": dict(
        root_causes=[("RC-SPARES", "Offshore spares and consumables not stocked at the supply base", "EQUIPMENT_SHORTAGE", "Repeat delays waiting for small mechanical spares from the mainland."),
                     ("RC-SEASTATE", "Sea-state limits for jack-up operations tighter than planned", "WEATHER", "Forecast windows were optimistic; standby days exceeded the allowance.")],
        issues=[
            _I("B1", "Sea state above jack-up limits - operations on standby", "WEATHER", "OSD-2240", "HIGH", True, "arun.nair", -6, "Significant wave height above the leg-loading limit; drilling suspended and string pulled to shoe.", 5, None, None, "", "RC-SEASTATE", evidence=True),
            _I("B2", "Regulator stop-work notice pending closure of HSE audit findings", "SAFETY", None, "CRITICAL", True, "SUP", -3, "Two audit findings on well-control drill records remain open; the regulator has asked for operations on Well W2 to be held until closed.", 4, None, stage="Well W2 (East Coast)"),
            _I("B3", "Supply vessel breakdown - bulk barite running short", "MATERIAL_SHORTAGE", "OSD-2297", "MEDIUM", False, "sneha.pillai", -9, "Primary supply vessel out of service; barite stock on rig below five days.", 6, None),
            _I("B4", "Berth congestion at Kakinada delayed casing loadout", "MATERIAL_DELIVERY_DELAY", "OSD-1030", "MEDIUM", True, "sneha.pillai", 40, "Casing lot waited for a berth for nine days.", 9, 9, 9, "Priority berth booked for the remaining lots.", None),
            _I("B5", "BOP annular preventer leaked on the first pressure test", "TECHNICAL", "OSD-1060", "HIGH", True, "arun.nair", 2, "Annular element replaced from the spares kit and the test repeated.", 5, 6, 6, "Element replaced, retest passed at rated pressure.", None, "Carry two spare annular elements and elastomer kits on any jack-up campaign."),
            _I("B6", "Mud pump liner failure while drilling the 12.25 in section", "EQUIPMENT_SHORTAGE", "OSD-2160", "MEDIUM", True, "sneha.pillai", 20, "Liner set failed; spares had to be air-freighted.", 4, 5, 5, "Spares delivered by chartered flight.", "RC-SPARES"),
            _I("B7", "Crew-change permit lapsed with the port authority", "PERMIT_APPROVAL", "OSD-1070", "LOW", False, "arun.nair", 12, "Renewal filed late; two crew changes were rescheduled.", 2, 3, 2, "Renewed.", None),
        ]),
    "NRL-EXPANSION": dict(
        root_causes=[("RC-STEEL", "Structural steel fabricator capacity", "CONTRACTOR_ISSUE", "Three deliveries slipped because the fabricator's yard was overbooked."),
                     ("RC-CRANE", "Heavy-lift crane availability and ground bearing", "EQUIPMENT_SHORTAGE", "One crawler crane served two units; ground preparation was not rated for its loads.")],
        issues=[
            _I("C1", "Structural steel delivery slipped two weeks (CDU-2)", "MATERIAL_DELIVERY_DELAY", "NRE-2020", "HIGH", True, "tenzin.bhutia", 30, "Fabricator missed the first dispatch date.", 14, 15, 15, "Weekly fabrication meetings started.", "RC-STEEL"),
            _I("C2", "Structural steel delivery slipped again (Hydrotreater)", "MATERIAL_DELIVERY_DELAY", "NRE-4020", "MEDIUM", False, "ritu.baruah", 40, "Second package arrived ten days late.", 10, 10, 11, "Part fabrication moved to a second yard.", "RC-STEEL",
               "Split structural steel packages between two fabricators from the start; track fabrication hours, not dispatch dates."),
            _I("C3", "450 t crawler crane out of service", "EQUIPMENT_SHORTAGE", "NRE-2030", "HIGH", True, "manoj.kalita", 50, "Boom hoist failure; erection of the wash column waited.", 12, 13, 12, "Repaired on site by the OEM.", "RC-CRANE"),
            _I("C4", "Crane ground-bearing failure during column lift prep", "EQUIPMENT_SHORTAGE", "NRE-4030", "CRITICAL", True, "SUP", 35, "Crane mat settled; lift stopped before the pick.", 18, 20, 21, "Crane pad redesigned and re-compacted; lift plan re-approved.", "RC-CRANE",
               "Test ground-bearing capacity under every crane pad before the lift plan is approved, not after."),
            _I("C5", "Six piles failed integrity testing", "TECHNICAL", "NRE-2000", "HIGH", True, "tenzin.bhutia", 60, "Low-strain tests showed necking in six piles.", 16, 18, 17, "Piles supplemented with additional piles under the cap.", None,
               "Use tremie placement with continuous concrete supply checks for piles over 25 m."),
            _I("C6", "Heavy rain - foundation pour postponed", "WEATHER", "NRE-2010", "LOW", False, "ritu.baruah", 90, "Pour postponed by three days.", 3, 3, 3, "Pour completed after covered formwork.", None),
            _I("C7", "Licensor revision of piping isometrics pending", "DESIGN_DOCUMENTATION", "NRE-2050", "HIGH", True, "manoj.kalita", -14, "Isometrics for the revamp lines are on hold until the licensor reissues revision C.", 15, None, evidence=True),
            _I("C8", "Welder availability for hydrotreater piping", "LABOUR_SHORTAGE", "NRE-4050", "MEDIUM", False, "ritu.baruah", -10, "Qualified 6G welders are fewer than the plan.", 8, None),
            _I("C9", "Hot-work permit backlog at the flare area", "PERMIT_APPROVAL", "NRE-7010", "MEDIUM", True, "tenzin.bhutia", -5, "Permit issuers are two days behind during the shutdown window.", 5, None),
        ]),
}


def build_issues(spec: ProjectSpec, plan: Plan) -> None:
    cfg = ISSUES.get(spec.code)
    if not cfg:
        return
    acts = {a.id: a for a in spec.acts}
    plan.root_causes = [RootCauseSpec(*r) for r in cfg["root_causes"]]
    for d in cfg["issues"]:
        if d["act"] and d["act"] not in acts:
            raise ValueError(f"{spec.code}: issue {d['key']} refers to unknown activity {d['act']}")
        if d["act"] and plan.state.get(d["act"]) == "NONE":
            raise ValueError(f"{spec.code}: issue {d['key']} refers to an activity with no progress ({d['act']})")
        base = acts[d["act"]].start if d["act"] else plan.anchor
        on = plan.anchor + timedelta(days=d["off"]) if d["off"] < 0 else min(base + timedelta(days=d["off"]), plan.anchor - timedelta(days=45))
        plan.issues.append(IssueSpec(d["key"], d["title"], d["category"], d["act"], d["stage"], d["severity"], d["blocks"], d["by"], on, d["desc"], float(d["est"]), d["res"], d["actual"],
                                     d["notes"], d["rc"], d["memory"], d["evidence"]))
