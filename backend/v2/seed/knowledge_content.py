"""Project knowledge for the four fictional demo projects.

Facts come from the projects' OWN records (project row, active schedule, settings, memberships) and are labelled FROM_RECORDS. The narrative around them is written here and
labelled honestly: ILLUSTRATIVE (a plausible description of a project of this kind, not a contractual or measured fact), AUTHORED (definitions and rules) or NOT_SPECIFIED
(an explicit statement that the record does not say). Nothing below states a contract value, a quantity, an activity id, a date or a progress figure that is not read from the
records; and nothing about actual progress is stored here at all (progress is live data, never copied into knowledge).

`collect_facts(conn, project_id)` reads the records; `build_entries(facts)` is pure and deterministic, so the same records always produce the same entries."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

PROCUREMENT_PATTERN = r"procure|deliver|long-lead|supply|fabricat|mobili|clearance|approval|permit"
DISC_LABEL = {"CIVIL": "Civil", "DRILLING": "Drilling and well engineering", "ELECTRICAL": "Electrical", "HSE": "Health, safety and environment", "INSTRUMENTATION": "Instrumentation",
              "LOGISTICS": "Marine and materials logistics", "PIPING": "Piping", "PROCESS": "Process and commissioning", "STATIC_ROTATING_EQUIPMENT": "Static and rotating equipment",
              "STRUCTURAL": "Structural", "OTHER": "Other"}


def _d(v: Any) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)


def _num(v: Any) -> str:
    """12.00 -> 12, 7.50 -> 7.5 (Decimal columns come back with their scale)"""
    t = f"{v:f}" if hasattr(v, "as_tuple") else str(v)
    return t.rstrip("0").rstrip(".") if "." in t else t


def _acts(n: int) -> str:
    return f"{n} activity" if n == 1 else f"{n} activities"


# ------------------------------------------------------------------------------------------------ facts (reads the records)
def collect_facts(c, project_id) -> Dict[str, Any]:
    p = c.execute("select * from projects where project_id = %s", (project_id,)).fetchone()
    if p is None:
        raise LookupError("no such project")
    s = c.execute("select * from project_settings where project_id = %s", (project_id,)).fetchone()
    v = c.execute("select version_id, version_no, baseline_name, data_date from schedule_versions where project_id = %s and status = 'ACTIVE'", (project_id,)).fetchone()
    f: Dict[str, Any] = {"project": dict(p), "settings": dict(s) if s else {}, "version": dict(v) if v else None, "stages": [], "areas": [], "disciplines": [], "milestones": [],
                         "procurement": [], "activity_total": 0, "members": []}
    if v:
        vid = v["version_id"]
        f["stages"] = [dict(r) for r in c.execute(
            "select st.wbs_name as name, min(ba.baseline_start) as first_start, max(ba.baseline_finish) as last_finish, count(*) as activities from schedule_wbs st "
            "join schedule_wbs w on w.version_id = st.version_id and w.wbs_path like st.wbs_path || '%%' join baseline_activities ba on ba.wbs_id = w.wbs_id "
            "where st.version_id = %s and st.node_type = 'STAGE' group by st.wbs_id, st.wbs_name, st.wbs_path order by min(ba.baseline_start), st.wbs_name", (vid,)).fetchall()]
        f["areas"] = [r["wbs_name"] for r in c.execute("select wbs_name from schedule_wbs where version_id = %s and node_type = 'AREA' order by wbs_path", (vid,)).fetchall()]
        f["disciplines"] = [dict(r) for r in c.execute("select discipline_code as code, count(*) as activities from baseline_activities where version_id = %s group by 1 order by 2 desc, 1", (vid,)).fetchall()]
        f["milestones"] = [dict(r) for r in c.execute("select external_activity_id as activity_id, activity_name as name, baseline_start as planned_date from baseline_activities "
                                                      "where version_id = %s and activity_type = 'MILESTONE' order by baseline_start, external_activity_id", (vid,)).fetchall()]
        f["procurement"] = [dict(r) for r in c.execute("select external_activity_id as activity_id, activity_name as name, baseline_start as planned_start, baseline_finish as planned_finish "
                                                       "from baseline_activities where version_id = %s and activity_type <> 'MILESTONE' and activity_name ~* %s order by baseline_start, external_activity_id",
                                                       (vid, PROCUREMENT_PATTERN)).fetchall()]
        f["activity_total"] = c.execute("select count(*) n from baseline_activities where version_id = %s", (vid,)).fetchone()["n"]
    f["members"] = [dict(r) for r in c.execute(
        "select m.role, pr.full_name from project_memberships m join profiles pr on pr.id = m.user_id where m.project_id = %s and m.status = 'ACTIVE' "
        "order by case m.role when 'PROJECT_MANAGER' then 0 when 'SUPERVISOR' then 1 else 2 end, pr.full_name", (project_id,)).fetchall()]
    return f


# ------------------------------------------------------------------------------------------------ authored narrative per project (illustrative unless stated)
KINDS = {"Cross-country crude oil pipeline": "pipeline", "Cross-country petroleum products pipeline": "pipeline", "Offshore exploration drilling": "offshore", "Refinery expansion": "refinery"}

NARRATIVE: Dict[str, Dict[str, Any]] = {
    "NNB-CRUDE": {
        "purpose": "A cross-country crude oil pipeline built in sequential mainline spreads, with a trenchless river crossing, a pump station and a receiving terminal, taking crude from the Naharkatiya end of the line to Barauni as the project name records. The project is complete: its record is the reference for how a finished pipeline project looks in ANVYRA.",
        "site": "The route crosses the Brahmaputra and Kosi floodplains and several rail and road corridors. Work in these reaches is paced by the monsoon, river levels and permissions from rail and road authorities.",
        "constraints": ["Monsoon restricts trenching, welding and hauling along the floodplain reaches; spreads can be sequenced to work the low-lying sections in the dry months.",
                        "Trenchless river crossings need low-water windows and are scheduled as their own stage.",
                        "Right-of-use access depends on land compensation and crop-season agreements, so spread starts follow land clearance.",
                        "Rail and road crossings need the owning authority's approval and a fixed possession window."],
        "risks": ["Flood or high river levels at crossings interrupting the trenchless works.", "Late delivery of line pipe, valves, fittings or pig traps holding up spread mobilisation.",
                  "Weld repair rates above plan after non-destructive testing, extending the welding stage.", "Land access disputes delaying clearing and grading on a spread."],
        "quality": ["Welds are inspected by non-destructive testing before coating; repairs are re-inspected.", "Coating is checked with holiday detection before lowering-in.",
                    "The line is hydrostatically tested in sections before commissioning.", "Permit-to-work and hot-work permits apply on every spread; trench safety and traffic control apply at crossings."],
        "procure_note": "Line pipe, valves, fittings and pig traps are long-lead supply items; the activities that track their delivery are listed from the schedule. Pump-station and terminal equipment follow the same pattern.",
        "externals": [("Mr. Pranjal Hazarika", "Client's Project Representative"), ("Ms. Sunita Verma", "Third-Party Inspection Lead"), ("Mr. Rakesh Oraon", "Land and Statutory Liaison")],
        "terms": [("Spread", "A self-contained section of pipeline construction with its own crew and equipment, built in sequence with the other spreads."),
                  ("Trenchless crossing", "Installing pipe under a river, road or railway by drilling instead of open trenching."),
                  ("Right of use (ROU)", "The corridor of land along the route in which the pipeline is built and operated."),
                  ("Pig trap", "A launcher or receiver used to send inspection and cleaning devices (pigs) through the pipeline."),
                  ("Hydrostatic test", "A pressure test of a pipeline section with water to prove its strength and tightness before commissioning.")],
    },
    "AEC-OFFSHORE": {
        "purpose": "An offshore exploration drilling campaign of three wells carried out with a jack-up rig, a marine support spread and shore bases. Well W1 is in the Andaman area; W2 and W3 are on the East Coast. The campaign is sequenced well by well, with mobilisation first and demobilisation last.",
        "site": "Operations are offshore, supported from shore bases at Port Blair and Kakinada. Access is by supply vessel and crew boat and depends on sea state and weather.",
        "constraints": ["Weather and sea-state windows govern rig moves, jacking, tow-in and personnel transfers.", "Well permits and coastal regulation clearances must be in place before each well starts.",
                        "The marine spread (supply vessels and crew boats) is shared across wells, so vessel availability paces the campaign.", "Well-control readiness (BOP tests and drills) is a precondition for drilling each hole section."],
        "risks": ["Weather downtime extending rig moves and drilling.", "Equipment failure causing non-productive time on the rig.", "Supply-vessel or bulk-material stock-outs interrupting cementing and mud operations.",
                  "Late long-lead casing, wellhead or BOP spares delaying a well start."],
        "quality": ["The blowout preventer is function- and pressure-tested before each well.", "Well-control drills are held on a fixed cycle and recorded.", "Casing is run and cemented to the programme and the cement job is verified before the next section.",
                    "Permit-to-work, simultaneous-operations and marine transfer procedures apply offshore."],
        "procure_note": "Casing, wellheads and BOP spares are long-lead items. Bulk cement, barite and mud chemicals, and supply-vessel runs, are repeated for each well; the activities that track them are listed from the schedule.",
        "externals": [("Capt. Arvind Menon", "Marine Superintendent"), ("Dr. Kavita Rao", "HSE and Well-Control Lead"), ("Mr. Joseph D'Souza", "Shore Base Manager, Port Blair")],
        "terms": [("Jack-up rig", "A mobile offshore drilling unit with legs that are lowered to the seabed to lift the platform out of the water."),
                  ("BOP", "Blowout preventer: the stack of valves on the wellhead that seals the well in an emergency."),
                  ("NPT", "Non-productive time: time on the rig not spent making progress on the well, for example waiting on weather."),
                  ("CRZ", "Coastal Regulation Zone: coastal land and waters where activity needs specific clearance."),
                  ("W1, W2, W3", "The three wells of the campaign, each tracked as its own stage."),
                  ("Marine spread", "The supply vessels, crew boats and related marine support serving the rig.")],
    },
    "NRL-EXPANSION": {
        "purpose": "An expansion of a crude refinery adding a second crude distillation unit and a hydrotreater, with offsites and utilities tie-ins, followed by commissioning and handover to operations. Work is organised into five stages: site preparation, the two process units, offsites and utilities, and commissioning.",
        "site": "The works are at an existing refinery site in Golaghat, Assam. Because the works are inside an existing refinery, construction areas can be close to operating units, so access, lifting and hot work are controlled by the plant's permit system.",
        "constraints": ["Tie-ins to operating units are only possible in planned shutdown windows.", "Heavy lifts need an approved lift plan and a weather and crane-availability window.",
                        "Hot work and excavation inside the operating area need plant permits and gas testing.", "Monsoon affects civil works and steel erection in the early stages."],
        "risks": ["Steel or piping fabrication running late and compressing erection and testing.", "A tie-in shutdown window slipping and pushing the dependent commissioning activities.",
                  "Late delivery of instruments, cables or rotating equipment.", "Rework from non-destructive testing or inspection findings."],
        "quality": ["Structural and piping fabrication follow approved inspection and test plans with witness and hold points.", "Welds are tested by non-destructive examination; pressure tests precede systems handover.",
                    "Instrument loops are checked before commissioning.", "Hot-work, lifting and confined-space permits apply throughout."],
        "procure_note": "Structural steel and process piping are fabricated and then erected for each unit; the activities that track fabrication and erection are listed from the schedule. Rotating and static equipment are long-lead items.",
        "externals": [("Mr. Dhiraj Bordoloi", "Owner's Engineer"), ("Ms. Anjali Kakoty", "Operations Interface Lead"), ("Mr. S. Thapa", "Heavy-Lift Coordinator")],
        "terms": [("CDU", "Crude distillation unit: the unit that separates crude oil into fractions by boiling range."), ("Hydrotreater", "A unit that removes sulphur and other impurities from refinery streams using hydrogen."),
                  ("Tie-in", "The connection of new piping to existing operating piping, usually during a shutdown."), ("Offsites", "Tankage, pipelines and other facilities outside the process units."),
                  ("Mechanical completion", "The point at which construction of a unit is finished and inspected, before commissioning begins.")],
    },
    "SMP-PIPE": {
        "purpose": "A planned cross-country petroleum products pipeline built in mainline spreads with a dispatch terminal, an intermediate station and special crossings. The baseline schedule is approved and active, but no work has been reported yet: everything here describes the plan, not progress.",
        "site": "The route runs through West Bengal, Bihar and Uttar Pradesh and includes a crossing of the Ganga at Patna and rail and road crossings. Route conditions are described as planned; no field condition has been reported.",
        "constraints": ["Statutory and environmental clearances come first and gate the notice to proceed.", "River and rail crossings depend on low-water windows and authority approvals.",
                        "Right-of-use access follows land acquisition and compensation.", "Monsoon affects trenching and hauling along the route."],
        "risks": ["Clearances or land access delaying the notice to proceed.", "Late delivery of line pipe, valves, fittings and pig traps.", "Weather and river conditions at the Ganga crossing.", "Interface delays at the dispatch terminal and intermediate station."],
        "quality": ["Welds are inspected by non-destructive testing and coating is checked before lowering-in.", "Each section is hydrostatically tested before commissioning.",
                    "Permit-to-work and traffic-management rules apply at crossings."],
        "procure_note": "Line pipe, valves, fittings and pig traps are long-lead supply items; the activities that track their procurement and delivery are listed from the schedule.",
        "externals": [("Mr. Anirban Sen", "Client's Project Representative"), ("Ms. Rekha Prasad", "Statutory Clearances Coordinator"), ("Mr. Imtiaz Ansari", "Third-Party Inspection Lead")],
        "terms": [("Spread", "A self-contained section of pipeline construction with its own crew and equipment, built in sequence with the other spreads."),
                  ("Notice to proceed", "The client's instruction that authorises the contractor to start the works."), ("Dispatch terminal", "The facility where products enter the pipeline for transport."),
                  ("Intermediate station", "A station along the route that maintains pressure or flow, or allows offtake."), ("Right of use (ROU)", "The corridor of land along the route in which the pipeline is built and operated.")],
    },
}

GENERAL_TERMS = [("Baseline", "The approved planned schedule that actual progress is measured against. A project can hold several versions; exactly one is active."),
                 ("WBS", "Work breakdown structure: the hierarchy that organises the activities into stages and areas."),
                 ("Stage", "A major phase of the work breakdown, for example a pipeline spread or a process unit."),
                 ("Activity", "A unit of planned work with dates, a discipline and, where measurable, a planned quantity."),
                 ("Claim", "A field report of progress. A claim never changes progress on its own: only a Supervisor's approval does."),
                 ("Candidate match", "A schedule activity the system proposes for a claim, ranked with reasons. A person chooses; the system proposes."),
                 ("Cumulative and incremental", "A cumulative claim reports the total done to date; an incremental claim reports the amount done since the last report."),
                 ("Critical path", "The chain of dependent activities that determines the earliest finish; delay on it delays the project."),
                 ("Float", "How far an activity can slip without delaying its successors or the finish."),
                 ("Hold point", "An inspection step that work may not pass until it is signed off.")]


# ------------------------------------------------------------------------------------------------ entries (pure)
def _e(section: str, title: str, body: str, provenance: str, order: int, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"section": section, "title": title, "body": body, "provenance": provenance, "sort_order": order, "tags": tags or []}


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def build_entries(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    p, s, v = f["project"], f["settings"], f["version"]
    code = p["project_code"]
    n = NARRATIVE[code]
    kind = KINDS.get(p.get("project_type") or "", "project")
    out: List[Dict[str, Any]] = []

    glance = [f"Project: {p['project_name']} ({code}).", f"Client as recorded: {p.get('client_name') or 'not specified'}.", f"Type: {p.get('project_type') or 'not specified'}.",
              f"Location as recorded: {p.get('location') or 'not specified'}.", f"Status: {p['lifecycle_status'].lower()}.",
              f"Planned start {_d(p['planned_start'])}, planned finish {_d(p['planned_finish'])}." if p.get("planned_start") and p.get("planned_finish") else "Planned dates are not specified."]
    if v:
        glance.append(f"The active baseline ({v['baseline_name'] or 'version ' + str(v['version_no'])}, version {v['version_no']}) has {_acts(f['activity_total'])} in {len(f['stages'])} stages "
                      f"across {len(f['disciplines'])} disciplines.")
    if p.get("description"):
        glance.append(f"Description on record: {p['description']}")
    out.append(_e("OVERVIEW", "Project at a glance", _bullets(glance), "FROM_RECORDS", 10, ["overview", "summary"]))
    out.append(_e("OVERVIEW", "Purpose and context", n["purpose"], "ILLUSTRATIVE", 20, ["purpose", "context"]))

    if f["stages"]:
        rows = [f"{st['name']}: planned {_d(st['first_start'])} to {_d(st['last_finish'])}, {_acts(st['activities'])}." for st in f["stages"]]
        out.append(_e("SCOPE", "Work breakdown by stage", "Stages in planned order, from the active baseline:\n" + _bullets(rows), "FROM_RECORDS", 10, ["stages", "wbs", "phases"]))
    if f["areas"]:
        out.append(_e("SCOPE", "Areas in the breakdown", "Areas recorded under the stages: " + ", ".join(f["areas"]) + ".", "FROM_RECORDS", 20, ["areas", "wbs"]))
    if f["disciplines"]:
        rows = [f"{DISC_LABEL.get(d['code'], d['code'].title())}: {_acts(d['activities'])}." for d in f["disciplines"]]
        out.append(_e("SCOPE", "Disciplines involved", "Disciplines and their activity counts in the active baseline:\n" + _bullets(rows), "FROM_RECORDS", 30, ["disciplines"]))
    out.append(_e("SCOPE", "Scope of work", f"This is a {kind} project. The scope is exactly what the stages and activities of the active baseline contain; this text adds no quantity or deliverable beyond them. "
                  "Where a stage's detailed scope matters, the activity names and planned quantities in the schedule are the reference.", "AUTHORED", 40, ["scope"]))

    dates = []
    if p.get("planned_start"):
        dates.append(f"Planned start: {_d(p['planned_start'])}.")
    if p.get("planned_finish"):
        dates.append(f"Planned finish: {_d(p['planned_finish'])}.")
    dates.append(f"Contract completion date: {_d(p['contract_finish'])}." if p.get("contract_finish") else "Contract completion date: not specified in the project record.")
    out.append(_e("CONTRACT", "Key dates on record", _bullets(dates), "FROM_RECORDS", 10, ["dates", "contract"]))
    out.append(_e("CONTRACT", "Contract value, type and payment terms", "Not specified. ANVYRA does not hold commercial terms for this project, so none are stated here.", "NOT_SPECIFIED", 20, ["contract", "value"]))
    out.append(_e("CONTRACT", "How to read the dates", "Planned dates come from the active baseline schedule. A revised schedule is imported as a new version; the previous version is kept and exactly one version is active. "
                  "Actual dates are never taken from the plan: they follow approved progress.", "AUTHORED", 30, ["baseline", "versions"]))

    loc = p.get("location") or "not specified"
    coords = f" Coordinates on record: {p['latitude']}, {p['longitude']}." if p.get("latitude") is not None and p.get("longitude") is not None else " Coordinates are not specified."
    out.append(_e("SITE", "Location on record", f"Location: {loc}.{coords}", "FROM_RECORDS", 10, ["location", "site"]))
    out.append(_e("SITE", "Access and site conditions", n["site"], "ILLUSTRATIVE", 20, ["access", "conditions"]))

    team = [f"{m['full_name']} ({m['role'].replace('_', ' ').title()})" for m in f["members"]]
    if team:
        out.append(_e("STAKEHOLDERS", "Project team", "Active members of this project in ANVYRA (fictional names):\n" + _bullets(team), "FROM_RECORDS", 10, ["team", "roles"]))
    out.append(_e("STAKEHOLDERS", "Other parties", "Illustrative external parties with fictional names:\n" + _bullets([f"{nm} - {role}" for nm, role in n["externals"]]), "ILLUSTRATIVE", 20, ["stakeholders", "external"]))

    if f["milestones"]:
        rows = [f"{m['activity_id']} - {m['name']}: planned {_d(m['planned_date'])}." for m in f["milestones"]]
        out.append(_e("MILESTONES", "Milestones on record", "Milestone activities of the active baseline:\n" + _bullets(rows), "FROM_RECORDS", 10, ["milestones"]))
    else:
        out.append(_e("MILESTONES", "Milestones on record", "The active baseline records no milestone activities.", "NOT_SPECIFIED", 10, ["milestones"]))
    if f["stages"]:
        out.append(_e("MILESTONES", "Phases in order", "Phases follow the stage order of the baseline: " + "; ".join(st["name"] for st in f["stages"]) + ".", "FROM_RECORDS", 20, ["phases", "stages"]))

    working = [f"Working days per week: {s.get('working_days_per_week')}." if s.get("working_days_per_week") else "Working week: not specified."]
    out.append(_e("CONSTRAINTS", "Working pattern on record", _bullets(working), "FROM_RECORDS", 10, ["working days"]))
    out.append(_e("CONSTRAINTS", "Typical constraints", "Constraints a project of this kind faces (illustrative, not a statement of contract terms):\n" + _bullets(n["constraints"]), "ILLUSTRATIVE", 20, ["constraints"]))
    out.append(_e("RISKS", "Typical risks", "Risks a project of this kind carries (illustrative, not a risk register):\n" + _bullets(n["risks"]), "ILLUSTRATIVE", 10, ["risks"]))
    out.append(_e("RISKS", "Risk register", "Not specified. ANVYRA does not hold a risk register for this project; live issues and delays are recorded as issues and are reported from the current data.", "NOT_SPECIFIED", 20, ["risks", "register"]))

    rules = [f"Over-baseline tolerance: {_num(s['over_baseline_tolerance_pct'])}%." if s.get("over_baseline_tolerance_pct") is not None else "Over-baseline tolerance: not specified.",
             f"Completion threshold: {_num(s['completion_threshold_pct'])}%." if s.get("completion_threshold_pct") is not None else "Completion threshold: not specified.",
             "Photo evidence is required with claims." if s.get("require_photo_evidence") else "Photo evidence is not required with every claim."]
    hse = next((d["activities"] for d in f["disciplines"] if d["code"] == "HSE"), 0)
    rules.append(f"The baseline contains {_acts(hse)} in the health, safety and environment discipline." if hse else "The baseline contains no activity in the health, safety and environment discipline.")
    out.append(_e("SAFETY_QUALITY", "Rules on record", _bullets(rules), "FROM_RECORDS", 10, ["tolerance", "threshold", "evidence", "hse"]))
    out.append(_e("SAFETY_QUALITY", "Typical safety and quality controls", "Controls a project of this kind applies (illustrative):\n" + _bullets(n["quality"]), "ILLUSTRATIVE", 20, ["safety", "quality", "inspection"]))

    if f["procurement"]:
        rows = [f"{a['activity_id']} - {a['name']}: planned {_d(a['planned_start'])} to {_d(a['planned_finish'])}." for a in f["procurement"]]
        out.append(_e("PROCUREMENT", "Procurement, supply and mobilisation activities", "Activities in the baseline whose names indicate procurement, supply, fabrication or mobilisation:\n" + _bullets(rows), "FROM_RECORDS", 10, ["procurement", "long-lead"]))
    out.append(_e("PROCUREMENT", "Long-lead items", n["procure_note"], "ILLUSTRATIVE", 20, ["long-lead", "supply"]))
    out.append(_e("PROCUREMENT", "Vendors and purchase orders", "Not specified. Vendor names, purchase orders and delivery dates are not held in ANVYRA for this project.", "NOT_SPECIFIED", 30, ["vendors", "purchase orders"]))

    out.append(_e("REPORTING", "How progress is reported and approved", "Site Engineers file claims (text, voice, photographs, scans, spreadsheets or documents). The system extracts the fields, proposes the matching activity "
                  "and validates the claim, but it never approves. A Supervisor reviews the evidence and approves, edits, holds or rejects. Only approved quantities change progress, and every step is recorded in the audit trail.", "AUTHORED", 10, ["claims", "approval", "workflow"]))
    out.append(_e("REPORTING", "Reporting conventions", "Report quantities against the activity they belong to, with the date they refer to and, where possible, a photograph. A report that is unclear is returned with a question; "
                  "the same item reported twice is kept once. Percentages are reported only for activities that are tracked by percentage.", "AUTHORED", 20, ["reporting", "evidence"]))

    for i, (term, text) in enumerate(GENERAL_TERMS + n["terms"]):
        out.append(_e("GLOSSARY", term, text, "AUTHORED", 10 + i, ["glossary", term.lower()]))
    return out
