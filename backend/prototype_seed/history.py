"""
Execution history generator for the prototype dataset (pure, deterministic, no database access).

Every activity that has approved progress gets its own believable chronological history of site claims, ending exactly at the
progress the dataset already says it has (so dashboards, which read approved_actuals, do not change).

How an activity's story is derived (nothing is a fixed template):
  1. What the work IS, from its name/description/discipline -> a work family (excavation, welding, procurement, approval,
     testing, drilling, ...). The family sets how often work is reported, how many updates are plausible, the SHAPE of the
     progress curve (steady, ramp, milestone steps with plateaus, back-loaded, test bursts) and the vocabulary of the report.
  2. When it ran: the real execution window (actual start .. the date of the latest claim), so a 5-month pipe-laying job
     gets many more updates than a 3-week approval. Gaps between updates vary (waiting periods, bursts of activity).
  3. What happened to it: issues recorded against the activity open a gap in reporting while they are active, and the first
     update afterwards says work resumed (or that progress is being held back, for an issue still open).
  4. How far it got: the cumulative progress at each update is drawn from the family's curve with activity-specific
     variation, strictly increasing, and the last update equals the activity's existing approved progress.
  5. Words: a site-report sentence from the family's phrase bank, with the physical quantity completed in the period
     (derived from the activity's planned quantity and the progress increment), the location, and phase wording
     (start / mid / late / finish).
  6. Review: most updates are approved as reported; some are adjusted (EDIT: supervisor approves less than claimed), and a few
     were first rejected for missing measurement records and resubmitted. Accepted progress never decreases.

Randomness only comes from a generator seeded by (project, activity) and uses nothing but `random()`, so output is identical
on every run and every machine.
"""
from __future__ import annotations

import math
import random
import re
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------------------------------------------------
# Work families
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Family:
    name: str
    keywords: Tuple[str, ...]
    gap: float          # typical days between reported updates while work is active
    n_min: int
    n_max: int
    shape: str          # ramp | steps | steady | late | front | burst
    measured: bool      # work measured by quantity (so a measurement-book source is plausible)


# Order matters: the first family with a matching keyword wins.
FAMILIES: Tuple[Family, ...] = (
    # specific work first, general families after
    Family("hse", ("helideck", "evacuation"), 21, 2, 6, "steady", False),
    Family("approval", ("approval", "clearance", "permit", "authoris", "notification", "consent", "licence", "agreements", "certification", "noc"), 24, 2, 5, "steps", False),
    Family("drilling", ("blowout",), 7, 3, 8, "ramp", True),
    Family("checkout", ("power generation", "inspection and acceptance"), 9, 3, 6, "burst", False),
    Family("testing", ("hydro", "pressure test", "run tests", "run-in", "loop check", "integration testing", "functional testing", "trial run", "performance", "pigging", "line fill", "line-pack", "commissioning", "feed-in", "drill-stem", "flow-back", "start-up", "procedure preparation", "energisation", "safety audit" ), 7, 2, 5, "burst", False),
    Family("ndt", ("radiography",), 8, 3, 6, "burst", True),
    Family("documentation", ("documentation", "handover", "as-built", "close-out", "dossier", "data submission", "report"), 26, 2, 4, "late", False),
    Family("study", ("survey", "study", "studies", "design", "engineering", "feasibility", "assessment", "hazop", "simulation", "investigation", "selection", "finalisation", "programme", "impact", "model", "isometric"), 19, 2, 6, "front", False),
    Family("coating", ("joint coating", "field joint"), 9, 3, 8, "ramp", True),
    Family("procurement", ("procurement", "purchase", "supply", "order", "tender", "award", "delivery", "manufacture", "column fabrication", "charter", "stocking", "contract", "coating and", "tubular", "transfer"), 27, 3, 6, "steps", False),
    Family("hdd", ("horizontal directional", "hdd", "river crossing"), 10, 4, 8, "ramp", True),
    Family("drilling", ("drill", "casing", "spud", "conductor", "mooring", "anchor", "preload", "jacking", "tow", "wireline", "perforation", "kill"), 7, 3, 8, "ramp", True),
    Family("earthworks", ("excavat", "trench", "clearing", "grading", "backfill", "levelling", "land ", "sewer", "drain", "lowering", "roads", "stringing"), 11, 3, 9, "ramp", True),
    Family("foundations", ("pile", "foundation", "concrete", "buildings", "civil"), 11, 3, 8, "ramp", True),
    Family("welding", ("weld", "piping", "spool", "bending", "fit-up"), 10, 3, 9, "ramp", True),
    Family("erection", ("erect", "steel", "structural", "column", "heater", "exchanger", "grouting", "platform", "rack", "insulation", "installation and alignment", "setting"), 12, 3, 8, "ramp", True),
    Family("electrical", ("cable", "switchgear", "transformer", "substation", "electrical", "cathodic"), 11, 3, 8, "ramp", True),
    Family("instrument", ("instrument", "calibration", "dcs", "scada", "telecom", "esd", "tubing", "mud-logging"), 12, 3, 7, "ramp", True),
    Family("hse", ("safety", "induction", "training", "fire", "waste", "fluid management", "environmental"), 21, 2, 6, "steady", False),
)

FALLBACK = Family("general", (), 14, 3, 7, "ramp", True)


def family_of(name: str, description: str = "", discipline: str = "") -> Family:
    text = f" {name.lower()} "
    for f in FAMILIES:
        if any(k in text for k in f.keywords):
            return f
    text = f" {name.lower()} {description.lower()} "
    for f in FAMILIES:
        if any(k in text for k in f.keywords):
            return f
    return {"HSE": FAMILIES[-1], "DRILLING": next(f for f in FAMILIES if f.name == "drilling")}.get(discipline, FALLBACK)


# --------------------------------------------------------------------------------------------------------------------
# Site-report vocabulary. {work}: the activity name (lower case), {loc}: where, {qty}: what was done this period.
# --------------------------------------------------------------------------------------------------------------------
PHRASES: Dict[str, Dict[str, List[str]]] = {
    "approval": {
        "start": ["Application for {work} prepared and lodged with the authority; acknowledgement received.",
                  "Documents for {work} compiled and submitted; authority registered the file."],
        "mid": ["Queries raised by the authority on {work} answered and the clarification meeting attended.",
                "Follow-up held with the department on {work}; additional drawings and undertakings furnished.",
                "{work}: site inspection by the authority's team completed, observations being closed."],
        "late": ["{work} is with the competent authority for final sign-off; no further queries outstanding.",
                 "Compliance points on {work} closed; recommendation forwarded for the final decision."],
        "final": ["{work} granted; the approval letter is received and filed in the project records.",
                  "Final approval for {work} issued by the authority; conditions noted for execution."],
    },
    "study": {
        "start": ["Kick-off held for {work}; data collection and base information gathered.",
                  "Field data and client inputs for {work} collected; work plan agreed."],
        "mid": ["{work}: draft deliverable circulated and review comments being incorporated.",
                "Analysis for {work} continued; interim results presented to the project team.",
                "{work}: {qty} reviewed this period; open points listed for the next meeting."],
        "late": ["Review comments on {work} closed out and the revised issue prepared.",
                 "{work} in final checking; sign-off sheets circulated."],
        "final": ["{work} issued for use after client approval; records filed.",
                  "{work} completed and formally accepted; final report on file."],
    },
    "procurement": {
        "start": ["Enquiries floated and vendor offers received for {work}; technical bid evaluation started.",
                  "{work}: purchase requisition released and bidders shortlisted."],
        "mid": ["{work}: vendor confirmed progress on the order, {qty} dispatched or accepted this period.",
                "Inspection release obtained for {qty} under {work}; shipping documents under review.",
                "{work}: expediting visit held; {qty} cleared for dispatch and in transit."],
        "late": ["Balance quantity under {work} ready at the vendor works; final inspection scheduled.",
                 "{work}: remaining lots released; {qty} received at site and stacked."],
        "final": ["Last lot under {work} received and inspected at site; order closed out.",
                  "{work} fully delivered; receiving inspection records signed."],
    },
    "earthworks": {
        "start": ["Mobilised equipment to {place} and opened the first work front for {work}.",
                  "{work} commenced {loc}; initial section set out and started."],
        "mid": ["{work} progressed {loc}; {qty} completed during this reporting period.",
                "Work continued {loc}; {qty} done and the area made safe at day end.",
                "{work}: {qty} completed; levels checked by the surveyor."],
        "late": ["{qty} completed this period; the remaining stretch {loc} is being opened up.",
                 "{work} nearing completion {loc}; {qty} done and the last sections are marked out."],
        "final": ["Last section of {work} completed {loc}; levels verified and the area handed over.",
                  "{work} finished {loc}; as-built levels recorded with the surveyor."],
    },
    "foundations": {
        "start": ["Reinforcement and shuttering started {loc} for {work}.",
                  "{work} commenced {loc}; first bays set out and prepared."],
        "mid": ["{work}: {qty} completed {loc} this period; cube and slump results within limits.",
                "Concreting for {work} continued {loc}; {qty} done and curing in progress.",
                "{work} progressed {loc}, {qty} achieved; shuttering stripped on the earlier bays."],
        "late": ["{work}: {qty} completed; the last bays {loc} are being reinforced and closed.",
                 "Remaining units of {work} {loc} prepared for the final pours."],
        "final": ["Final bays of {work} {loc} completed and accepted by the inspector.",
                  "{work} finished {loc}; stripping, curing and record sheets completed."],
    },
    "welding": {
        "start": ["Welders qualified and fit-up begun {loc} for {work}.",
                  "{work} started {loc}; first spools/joints fitted and welded."],
        "mid": ["{work} progressed; {qty} completed this period {loc} with the repair rate within limits.",
                "{qty} completed under {work}; weld records and joint maps updated.",
                "{work}: production continued {loc}, {qty} done, NDT requests raised for completed joints."],
        "late": ["{qty} completed this period; the remaining work {loc} is being fitted up.",
                 "{work} nearing completion; {qty} done and punch items being cleared."],
        "final": ["The last joints under {work} welded and accepted {loc}; records closed.",
                  "{work} completed {loc}; final weld map and test records signed off."],
    },
    "erection": {
        "start": ["Cranes and rigging crew mobilised to {place}; {work} started with the first lifts.",
                  "{work} commenced {loc}; first members/items positioned and temporarily secured."],
        "mid": ["{work}: {qty} erected {loc} this period; alignment checked.",
                "Lifting {loc} continued under {work}, {qty} set and bolted up.",
                "{work} progressed {loc}; {qty} completed and plumb/level records taken."],
        "late": ["{qty} completed this period; the remaining items {loc} are staged for lifting.",
                 "{work} close to completion {loc}; {qty} set and final torquing started."],
        "final": ["The last items of {work} set and aligned {loc}; acceptance by the inspector.",
                  "{work} complete {loc}; as-built dimensions recorded and punch list cleared."],
    },
    "electrical": {
        "start": ["Materials issued and routes marked {loc}; {work} started.",
                  "{work} commenced {loc}; first trays/cable drums positioned."],
        "mid": ["{work}: {qty} completed {loc} this period; insulation resistance checks taken.",
                "{work} continued, {qty} laid and terminated {loc}; ferruling and glanding in progress.",
                "{qty} done under {work}; megger records signed by the inspector."],
        "late": ["{work}: {qty} completed; terminations {loc} are being finished and checked.",
                 "Remaining routes under {work} {loc} cleared for laying."],
        "final": ["{work} completed {loc}; final insulation and continuity tests passed.",
                  "Last terminations under {work} done and tested; as-built cable schedule updated."],
    },
    "instrument": {
        "start": ["Instrument materials received and work areas prepared {loc}; {work} started.",
                  "{work} commenced {loc}; first loops mounted and tagged."],
        "mid": ["{work}: {qty} completed {loc} this period; tubing pressure-tested.",
                "{work} progressed {loc}; {qty} installed and tagged.",
                "{qty} done under {work}; loop folders updated."],
        "late": ["{work}: {qty} completed; the remaining items {loc} are being installed.",
                 "Final installation checks on the completed part of {work} carried out."],
        "final": ["{work} completed {loc}; installation records signed and handed to testing.",
                  "Last items of {work} installed and tagged; punch list closed."],
    },
    "coating": {
        "start": ["Coating crew, primer and holiday detectors mobilised to {place}; {work} started.",
                  "{work} commenced {loc}; surface preparation and the first joints coated."],
        "mid": ["{work}: {qty} coated {loc} this period; holiday detection passed on the completed joints.",
                "Coating continued {loc}, {qty} done; rejected joints stripped and recoated.",
                "{qty} completed under {work}; coating thickness and adhesion records signed."],
        "late": ["{qty} coated this period; the remaining joints {loc} are being prepared.",
                 "{work} nearing completion; {qty} done and the final inspection records are being compiled."],
        "final": ["The last joints under {work} were coated and holiday-tested {loc}; records closed.",
                  "{work} completed {loc}; the final coating inspection report was accepted."],
    },
    "checkout": {
        "start": ["Checkout of {work} started; the first systems were isolated and inspected.",
                  "{work}: inspection plan agreed and the first checks carried out."],
        "mid": ["{work}: {qty} checked this period; findings logged and the punch list updated.",
                "Checks under {work} continued {loc}; {qty} completed and defects passed to the contractor.",
                "{qty} verified under {work}; the load and function readings were recorded."],
        "late": ["{work}: {qty} completed; the outstanding defects are being closed out.",
                 "Remaining checks under {work} scheduled with the class surveyor."],
        "final": ["{work} completed and the findings accepted by the company representative.",
                  "Final checks under {work} passed; the acceptance certificate was signed."],
    },
    "ndt": {
        "start": ["NDT crew and consumables mobilised to {place}; {work} started on the first batch.",
                  "{work} commenced; first films/reports issued for review."],
        "mid": ["{work}: {qty} examined this period; reports reviewed by the third-party inspector.",
                "{qty} completed under {work}; repair joints re-tested.",
                "{work} continued {loc}; {qty} cleared and the rejected items cut out and re-welded."],
        "late": ["{qty} cleared this period; the last backlog under {work} is being scheduled.",
                 "{work}: remaining items queued; {qty} completed this week."],
        "final": ["{work} completed; the final batch cleared and the summary report signed.",
                  "All items under {work} examined and accepted; records compiled."],
    },
    "drilling": {
        "start": ["Operations for {work} started on location; first run completed without incident.",
                  "{work} commenced; equipment checked and the first operation completed."],
        "mid": ["{work}: {qty} completed in the last reporting period; parameters within the programme.",
                "Operations for {work} continued, {qty} achieved; daily drilling reports submitted.",
                "{work} progressed; {qty} done and the next stage of the programme prepared."],
        "late": ["{work}: {qty} completed; final checks under way before sign-off.",
                 "{work} approaching completion; {qty} done and the wrap-up operations scheduled."],
        "final": ["{work} completed as per the programme; final records submitted and accepted.",
                  "Last operation under {work} completed and verified by the company representative."],
    },
    "hdd": {
        "start": ["Rig set up on the entry side and pilot hole started for {work}.",
                  "{work} commenced; pilot drilling began after the entry-pit checks."],
        "mid": ["Pilot hole advanced, {qty} drilled this period; steering within the design corridor.",
                "{work}: reaming passes continued and {qty} completed; mud properties maintained.",
                "{work} progressed; {qty} achieved and the exit-side preparations continued."],
        "late": ["Final reaming for {work} in progress; {qty} completed and the pull-back string is being prepared.",
                 "{work}: pipe string positioned and tested; pull-back planned for the next window."],
        "final": ["Pull-back for {work} completed successfully; the post-installation survey is on file.",
                  "{work} finished; the crossing section was tested and accepted."],
    },
    "testing": {
        "start": ["Test packs and procedures for {work} approved; preparations began.",
                  "{work} started; the first test packs were isolated and prepared."],
        "mid": ["{work}: {qty} completed this period; test records witnessed by the client.",
                "{qty} tested under {work}; defects found were rectified and re-tested.",
                "{work} continued, {qty} done; certificates being compiled."],
        "late": ["{qty} completed this period; the remaining test packs are lined up.",
                 "{work}: the last scheduled tests are being arranged with the witnesses."],
        "final": ["{work} completed and all certificates accepted by the client.",
                  "The last tests under {work} passed; the pack was closed out."],
    },
    "hse": {
        "start": ["{work} started; first sessions and checks held with the site team.",
                  "Plan for {work} issued and the first batch completed."],
        "mid": ["{work} continued; {qty} completed during this period, records signed.",
                "{work}: next batch completed; deficiencies from the earlier round closed."],
        "late": ["{work}: most of the planned scope is done; the remainder is scheduled.",
                 "Follow-up on the open points under {work} completed; closing visits planned."],
        "final": ["{work} completed for the full scope; certificates and records filed.",
                  "Final session under {work} held; close-out report signed by the HSE manager."],
    },
    "documentation": {
        "start": ["Compilation started for {work}; document registers prepared.",
                  "{work}: document lists agreed with the client and collection started."],
        "mid": ["{work}: sections compiled and sent to the engineers for review.",
                "Review comments on {work} received; revisions under way."],
        "late": ["{work}: remaining packages being finalised and indexed.",
                 "Final sets for {work} printed and awaiting signatures."],
        "final": ["{work} completed and handed over; acknowledgement received from the owner.",
                  "Final dossier for {work} issued and accepted."],
    },
    "general": {
        "start": ["{work} started {loc}; first part completed.", "Work on {work} commenced {loc}."],
        "mid": ["{work} progressed; {qty} completed this period.", "{work}: {qty} done {loc}."],
        "late": ["{work}: {qty} completed; the remaining work is being scheduled.", "{work} nearing completion {loc}."],
        "final": ["{work} completed and accepted.", "{work} finished; records closed."],
    },
}

APPROVE_COMMENTS = {
    "start": ["Start of work verified on site; mobilisation confirmed.", "Commencement confirmed against the daily record."],
    "mid": ["Quantities agree with the measurement record; approved.", "Verified on site by the supervisor; approved as reported.",
            "Consistent with the contractor's joint measurement.", "Progress cross-checked against the daily report; approved."],
    "final": ["Completion verified on site and records checked; approved.", "Final acceptance recorded; no open punch items."],
}

# deliverables counted as one thing: the sentence talks about "a further part" instead of inventing a unit count
SINGULAR_UOM = {"contract", "approval", "report", "package", "system", "campaign", "dossier", "order", "certificate", "procedure",
                "model", "audit", "commissioning", "test run", "line fill", "trial run", "spread", "survey", "operation", "inspection",
                "conductor", "stack", "string", "rig", "tow", "test", "well", "unit", "logging run", "programmes"}

SOURCE_WEIGHTS = (("DSR", 0.50), ("WPR", 0.20), ("DIARY", 0.12), ("MBOOK", 0.08), ("TYPED", 0.10))
SOURCE_LABEL = {"DSR": "Daily Site Report", "WPR": "Weekly Progress Report", "DIARY": "Site Diary", "MBOOK": "Measurement Book"}


@dataclass
class HClaim:
    day: date
    pct: int                      # cumulative approved progress after this claim
    claimed: float                # what the engineer reported (equals pct unless the supervisor adjusted it)
    text: str
    comment: str
    action: str                   # APPROVE | EDIT | REJECT
    event_type: str               # ACTUAL_START | PROGRESS_UPDATE | ACTUAL_FINISH
    source: str                   # DSR | WPR | DIARY | MBOOK | TYPED
    corroborated_by: Optional[str] = None
    decision_lag: int = 0         # days between the claim and the supervisor's decision
    phase: str = "mid"
    accepted: bool = True         # False for a rejected claim (not part of accepted progress)


@dataclass
class IssueWindow:
    start: date
    end: Optional[date]           # None: still open
    title: str
    blocks: bool


# --------------------------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------------------------
class _Rng:
    def __init__(self, seed: str):
        self.r = random.Random(zlib.crc32(seed.encode()))

    def u(self) -> float:
        return self.r.random()

    def g(self) -> float:
        return self.r.gauss(0.0, 1.0)

    def pick(self, seq: Sequence):
        return seq[min(len(seq) - 1, int(self.u() * len(seq)))]


def _workday(d: date, lo: date, hi: date) -> date:
    """Site reports are written on working days: slide a Sunday to the neighbouring Saturday/Monday inside [lo, hi]."""
    if d.weekday() != 6:
        return d
    if d - timedelta(days=1) >= lo:
        return d - timedelta(days=1)
    return d + timedelta(days=1) if d + timedelta(days=1) <= hi else d


def _fmt_qty(delta: float, uom: str) -> str:
    if delta < 0.5:
        return "a small quantity"
    if delta >= 1000:
        v = int(round(delta / 100.0) * 100)
    elif delta >= 100:
        v = int(round(delta / 10.0) * 10)
    elif delta >= 10:
        v = int(round(delta / 5.0) * 5) or int(round(delta))
    elif delta >= 3:
        v = int(round(delta))
    else:
        v = round(delta, 1)
    return f"about {v:,} {uom}"


_CUTS = (" of ", " for ", " at ", " from ", " by ", " to ", " in ", " under ", ",", ":")
_SINGULAR_WORDING = ("a further part of the scope", "the next portion of the work", "additional scope")


def short_name(name: str) -> str:
    """The head of an activity name as a site report would refer to it ("Pipe bending", "Hydrostatic testing")."""
    n = re.sub(r"\s*\([^)]*\)", "", name)
    cut = len(n)
    for c in _CUTS:
        k = n.find(c, 8)
        if k != -1:
            cut = min(cut, k)
    head = n[:cut].strip()
    return head if len(head.split()) >= 2 else n.strip()


def loc_phrases(location: str) -> Tuple[str, str]:
    """(phrase with its preposition, bare place): 'along the route' / 'the route', 'at Unit 5 pipe rack' / 'Unit 5 pipe rack'."""
    first = location.split(",")[0].strip()
    low = first.lower()
    if low.startswith("full route") or low.startswith("route chainage") or low.startswith("all pipeline") or low.startswith("districts"):
        return "along the route", "the route"
    if low.startswith("rig floor") or low.startswith("rig "):
        return "on the rig floor", "the rig floor"
    if low.startswith("kakinada"):
        return f"at {first}", first
    return f"at {first}", first


def _quantity_phrase(delta_pct: float, qty: float, uom: str, prev_pct: float = 0.0, cur_pct: float = 0.0, variant: int = 0) -> str:
    if qty <= 1.5 or uom.lower() in SINGULAR_UOM:
        return _SINGULAR_WORDING[variant % len(_SINGULAR_WORDING)]
    base = _fmt_qty(delta_pct / 100.0 * qty, uom)
    if uom.lower() == "km" and qty >= 100:   # linear work: say which stretch of the route was covered
        a, b = int(round(prev_pct / 100.0 * qty)), int(round(cur_pct / 100.0 * qty))
        return f"{base} (chainage {a}–{b} km)"
    return base


# --------------------------------------------------------------------------------------------------------------------
# the generator
# --------------------------------------------------------------------------------------------------------------------
JUMP_LIMIT = 40          # largest believable single reported step, in points of progress
JUMP_LIMIT_STEPS = 55    # approvals / deliveries legitimately move in bigger steps


def build_history(**kw) -> List["HClaim"]:
    """The accepted history of one activity (see _build_once); adds updates until no single step is implausibly large."""
    claims: List[HClaim] = []
    for boost in range(0, 5):
        claims = _build_once(boost=boost, **kw)
        acc = [c.pct for c in claims if c.accepted]
        steps = [b - a for a, b in zip([0] + acc, acc)]
        fam = family_of(kw["name"], kw.get("description", ""), kw.get("discipline", ""))
        limit = JUMP_LIMIT_STEPS if fam.shape == "steps" else JUMP_LIMIT
        span = max(0, (kw["final_day"] - kw["start"]).days)
        if not steps or max(steps) <= limit or len(acc) >= span // 2 + 1 or len(acc) >= 12:
            break
    return claims


def _build_once(
    *,
    boost: int = 0,
    project_code: str,
    activity_code: str,
    name: str,
    description: str,
    discipline: str,
    location: str,
    qty: float,
    uom: str,
    start: date,
    final_day: date,
    final_pct: int,
    data_date: date,
    issues: Sequence[IssueWindow] = (),
    final_edit: Optional[Tuple[float, str, str]] = None,
) -> List[HClaim]:
    """
    The accepted history of one activity: claims in chronological order, the last one at `final_day` with `final_pct`.
    `final_edit` = (claimed %, text, comment) when the supervisor's latest decision is an EDIT (claimed more than approved).
    """
    if final_pct <= 0:
        return []
    rng = _Rng(f"{project_code}|{activity_code}")
    fam = family_of(name, description, discipline)
    span = max(0, (final_day - start).days)

    # ---- how many updates, from the family cadence and the real execution window
    if span <= 1 or final_pct <= 6:
        n = 1
    else:
        gap = fam.gap * (0.8 + 0.5 * rng.u())
        n = max(fam.n_min, min(fam.n_max, round(span / gap)))
        n = min(n, span // 2 + 1)
        if final_pct < 25:
            n = min(n, 3)
        if final_pct < 12:
            n = min(n, 2)
        n = min(n, max(1, final_pct // 2))
        n = min(n + boost, span // 2 + 1, max(1, final_pct // 2))

    # ---- when: irregular gaps (idle periods, bursts), snapped to working days, strictly increasing
    offs = [0]
    for _ in range(1, n):
        offs.append(offs[-1] + math.exp(0.55 * rng.g()) * (2.6 if rng.u() < 0.14 else 1.0))
    if n > 1:
        scale = span / offs[-1]
        offs = [int(round(x * scale)) for x in offs]
        offs[0], offs[-1] = 0, span
    days = [start + timedelta(days=o) for o in offs]
    if n > 1:
        for i in range(1, n - 1):
            days[i] = _workday(days[i], days[0], days[-1])
    days = sorted(set(days))
    if n == 1:
        days = [final_day]
    n = len(days)

    # ---- issues: a gap in reporting while the issue is active; remember who resumes / who is constrained
    resume: Dict[int, str] = {}
    constrained: Dict[int, str] = {}
    for iw in issues:
        w_end = min(iw.end or min(data_date, iw.start + timedelta(days=14)), final_day - timedelta(days=2))
        if w_end < iw.start:
            continue
        keep: List[date] = []
        for i, d in enumerate(days):
            interior = 0 < i < len(days) - 1
            if interior and iw.start <= d <= w_end:
                nxt = w_end + timedelta(days=1 + (zlib.crc32(f"{activity_code}{iw.title}".encode()) % 3))
                if nxt < days[i + 1] and nxt not in keep and nxt not in days:
                    keep.append(nxt)          # the update slips to after the stoppage
                continue                      # otherwise the update simply does not happen in the stoppage
            keep.append(d)
        days = sorted(set(keep))
        n = len(days)
    for iw in issues:
        if iw.start > final_day:
            continue
        for i, d in enumerate(days):
            if iw.end is not None and d > iw.end and (i == 0 or days[i - 1] <= iw.end) and i > 0:
                resume[i] = iw.title
            if iw.end is None and d >= iw.start and iw.start >= days[0] and i not in constrained:
                constrained[i] = iw.title
                break

    # ---- how far: cumulative progress from the family's curve with activity-specific variation
    t = [((d - days[0]).days / max(1, (days[-1] - days[0]).days)) for d in days]
    w: List[float] = []
    for i in range(n):
        ti = t[i]
        if fam.shape == "ramp":
            v = math.exp(-(((ti - 0.5) / 0.38) ** 2)) + 0.18
            v *= math.exp(0.38 * rng.g())
        elif fam.shape == "steps":
            v = -math.log(max(1e-6, rng.u())) + 0.05
        elif fam.shape == "late":
            v = (0.3 + ti) ** 2 * math.exp(0.3 * rng.g())
        elif fam.shape == "front":
            v = (1.3 - ti) ** 2 * math.exp(0.3 * rng.g())
        elif fam.shape == "burst":
            v = (0.2 + ti) ** 1.5 * math.exp(0.45 * rng.g())
        else:  # steady
            v = 1.0 + 0.25 * abs(rng.g())
        w.append(max(0.02, v))
    total = sum(w)
    cum, run = [], 0.0
    for v in w:
        run += v
        cum.append(final_pct * run / total)
    pcts = [max(1, int(round(c))) for c in cum]
    pcts[-1] = final_pct
    if n > 1:
        pcts[0] = max(1, min(pcts[0], int(final_pct * (0.35 if fam.shape == "steps" else 0.28))))
    for i in range(n - 2, -1, -1):                       # leave room below the final value
        pcts[i] = min(pcts[i], final_pct - (n - 1 - i))
    for i in range(n):                                   # strictly increasing and >= 1
        floor = (pcts[i - 1] + 1) if i else 1
        pcts[i] = max(pcts[i], floor)
    pcts[-1] = final_pct

    # ---- sources, review outcomes and words
    claims: List[HClaim] = []
    prev = 0
    last_phrase = -1
    used: set = set()
    for i in range(n):
        p = pcts[i]
        late = p >= 75 or (final_pct >= 100 and p >= 60)
        phase = "start" if i == 0 and n > 1 else ("final" if i == n - 1 and final_pct >= 100 else ("late" if late else "mid"))
        if n == 1:
            phase = "final" if final_pct >= 100 else "mid"
        bank = PHRASES.get(fam.name, PHRASES["general"])
        options = bank["mid" if phase == "mid" else phase]
        idx = int(rng.u() * len(options))
        if idx == last_phrase and len(options) > 1:
            idx = (idx + 1) % len(options)
        last_phrase = idx
        label = name if phase in ("start", "final") else short_name(name)
        label = label if label[:2].isupper() or label.split()[0].isupper() else label[0].lower() + label[1:]
        loc_p, place = loc_phrases(location)
        sentence = options[idx].format(work=label, loc=loc_p, place=place, qty=_quantity_phrase(p - prev, qty, uom, prev, p, i + idx))
        sentence = sentence[0].upper() + sentence[1:]
        extra = ""
        if i in resume:
            extra = f" Work resumed after the stoppage ({resume[i][0].lower() + resume[i][1:]})."
        elif i in constrained:
            extra = f" Progress is being held back by: {constrained[i][0].lower() + constrained[i][1:]}."
        elif rng.u() < 0.28 and phase != "final":
            extra = f" Cumulative progress reported: {p}%."
        text = sentence + extra
        if text in used:                                   # never repeat a report verbatim within one activity
            for alt in range(len(options)):
                cand = options[(idx + 1 + alt) % len(options)].format(work=label, loc=loc_p, place=place, qty=_quantity_phrase(p - prev, qty, uom, prev, p, i + idx + alt + 1))
                cand = cand[0].upper() + cand[1:] + extra
                if cand not in used:
                    text = cand
                    break
            else:
                text = sentence + (extra or f" Cumulative progress reported: {p}%.")
        used.add(text)

        # who reported it
        r, acc, source = rng.u(), 0.0, "DSR"
        for key, wgt in SOURCE_WEIGHTS:
            acc += wgt
            if r <= acc:
                source = key
                break
        if source == "MBOOK" and not fam.measured:
            source = "DSR"
        corroborated = None
        if rng.u() < 0.12:
            other = [k for k, _ in SOURCE_WEIGHTS if k not in (source, "TYPED") and (k != "MBOOK" or fam.measured)]
            corroborated = rng.pick(other)

        action, claimed, comment = "APPROVE", float(p), rng.pick(APPROVE_COMMENTS["start" if phase == "start" else ("final" if phase == "final" else "mid")])
        if 0 < i < n - 1 and rng.u() < 0.09 and p + 3 < 100:
            claimed = float(min(99, p + 3 + int(rng.u() * 6)))
            action = "EDIT"
            comment = rng.pick(["Adjusted to the measured quantity; the remainder is not yet verified.",
                                "Reduced to the joint measurement; resubmit the balance with the next record."])
        ev_type = "ACTUAL_FINISH" if (i == n - 1 and final_pct >= 100) else ("ACTUAL_START" if i == 0 else "PROGRESS_UPDATE")
        claims.append(HClaim(day=days[i], pct=p, claimed=claimed, text=text, comment=comment, action=action, event_type=ev_type,
                             source=source, corroborated_by=corroborated, decision_lag=0 if i == n - 1 else int(rng.u() * 3), phase=phase))
        prev = p

    # the latest decision may itself be an EDIT (supervisor approved less than the engineer reported)
    if final_edit is not None:
        c = claims[-1]
        c.claimed, c.text, c.comment, c.action = final_edit[0], final_edit[1], final_edit[2], "EDIT"

    # a claim that was first rejected, then resubmitted, in some activities with enough history
    if n >= 4 and zlib.crc32(f"{project_code}{activity_code}rej".encode()) % 6 == 0:
        k = 1 + int(rng.u() * (n - 2))
        if (days[k] - days[k - 1]).days >= 3:
            rej_day = days[k] - timedelta(days=1 + int(rng.u() * min(4, (days[k] - days[k - 1]).days - 1)))
            if rej_day > days[k - 1]:
                over = float(min(100, pcts[k] + 4 + int(rng.u() * 7)))
                claims.append(HClaim(day=rej_day, pct=pcts[k - 1], claimed=over,
                                     text=sentence_for_rejected(short_name(name), loc_phrases(location)[0], over), comment=rng.pick([
                                         "Measurement record not attached; resubmit with the joint measurement sheet.",
                                         "Quantity not supported by the daily record; please resubmit after verification."]),
                                     action="REJECT", event_type="PROGRESS_UPDATE", source="DSR", decision_lag=1, phase="mid", accepted=False))
    claims.sort(key=lambda c: (c.day, c.accepted is False, c.pct))
    # decisions are recorded in the order the claims arrived: never decide a claim after the next one has been made
    for i, c in enumerate(claims):
        room = (claims[i + 1].day - c.day).days - 1 if i + 1 < len(claims) else 0
        c.decision_lag = max(0, min(c.decision_lag, room))
    return claims


def sentence_for_rejected(name: str, location: str, claimed: float) -> str:
    return f"{name} reported at {int(claimed)}% cumulative {location}; progress claimed ahead of the measured quantity."


def validate_history(claims: Sequence[HClaim], final_pct: int, final_day: date, start: date) -> List[str]:
    """Internal-consistency problems of one activity's history (empty == consistent)."""
    problems: List[str] = []
    acc = [c for c in claims if c.accepted]
    if not acc:
        return ["no accepted claim"]
    if acc[-1].pct != final_pct or acc[-1].day != final_day:
        problems.append(f"latest accepted claim {acc[-1].pct}% on {acc[-1].day} is not the final state {final_pct}% on {final_day}")
    days = [c.day for c in acc]
    if days != sorted(days) or len(set(days)) != len(days):
        problems.append("accepted claim dates are not strictly increasing")
    pcts = [c.pct for c in acc]
    if any(b <= a for a, b in zip(pcts, pcts[1:])):
        problems.append(f"accepted progress is not strictly increasing: {pcts}")
    if days and days[0] < start:
        problems.append("history starts before the actual start")
    if any(c.pct > 100 or c.pct < 1 for c in acc):
        problems.append("progress outside 1..100")
    for c in claims:
        if not c.accepted and (c.claimed <= c.pct):
            problems.append("a rejected claim must claim more than the approved progress at that time")
    return problems
