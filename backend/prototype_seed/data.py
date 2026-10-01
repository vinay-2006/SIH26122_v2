"""
Prototype demo dataset: the four SIH projects, as pure data (no database access).

    COMPLETED  Naharkatiya-Noonmati-Barauni Crude Oil Pipeline
    ONGOING    The Andaman & East Coast Offshore Drilling Campaign
    ONGOING    Numaligarh Refinery Expansion Project (NRL)
    UPCOMING   The Siliguri-to-Mughalsarai Cross-Country Fuel Pipeline

Everything here is DEMONSTRATION DATA authored for the prototype; it is not an extract of any real project record.
Actual progress is expressed only as the approved percentage of each activity (`pct`). The seeder turns every
pct > 0 into a real field claim -> supervisor decision -> approved actual, so progress has one source of truth
(approved_actuals) exactly as in the running application. pct == 0 means "no approved actual exists".

Activity dates are derived deterministically from the stage window by `build_activities` (activities start spread over
the first ~60 % of the window, the last one finishing with the stage), so the file stays readable. `validate()`
checks the dataset's internal consistency (progress vs. dates, project lifecycle vs. progress, discipline codes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

DISCIPLINES = {
    "CIVIL", "STRUCTURAL", "PIPING", "STATIC_ROTATING_EQUIPMENT", "ELECTRICAL",
    "INSTRUMENTATION", "PROCESS", "DRILLING", "LOGISTICS", "HSE",
}


@dataclass
class Act:
    code: str
    name: str
    description: str
    discipline: str
    location: str
    qty: float
    uom: str
    pct: float = 0.0
    start_override: Optional[date] = None   # explicit planned start (level-of-effort / early-start activities)
    start: Optional[date] = None
    finish: Optional[date] = None


@dataclass
class Stage:
    code: str
    name: str
    start: date
    end: date
    acts: List[Act]


@dataclass
class IssueSeed:
    key: str
    activity: str
    category: str
    severity: str
    title: str
    description: str
    reported: date
    expected_days: Optional[float]
    blocks_work: bool
    resolved: Optional[date] = None
    resolution: Optional[str] = None
    outcome: Optional[str] = None
    root_cause: Optional[str] = None        # key into Project.root_causes
    share_org: bool = False                 # promoted to institutional memory visible to other projects


@dataclass
class ClaimSeed:
    """A decided claim that is NOT the activity's authoritative approved actual (rejected / on hold) or an EDIT."""
    key: str
    activity: str
    action: str                             # REJECT | HOLD | EDIT
    claimed_pct: float
    approved_pct: Optional[float]
    day: date
    text: str
    comment: str


@dataclass
class Project:
    code: str
    name: str
    lifecycle: str
    location: str
    project_type: str
    description: str
    start: date
    finish: date
    data_date: date
    schedule_id: str
    stages: List[Stage]
    issues: List[IssueSeed] = field(default_factory=list)
    root_causes: Dict[str, tuple] = field(default_factory=dict)   # key -> (category, title, summary)
    claims: List[ClaimSeed] = field(default_factory=list)


def d(s: str) -> date:
    return date.fromisoformat(s)


# --------------------------------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------------------------------
_DUR_PATTERN = [0.45, 0.35, 0.50, 0.30, 0.40, 0.55, 0.32]


def build_activities(stage: Stage) -> None:
    """Fill planned start/finish for each activity inside the stage window (deterministic)."""
    span = (stage.end - stage.start).days
    n = len(stage.acts)
    for i, a in enumerate(stage.acts):
        off = round(0.60 * span * i / max(n - 1, 1))
        a.start = stage.start + timedelta(days=off)
        dur = max(10, round(_DUR_PATTERN[i % len(_DUR_PATTERN)] * span))
        a.finish = min(stage.end, a.start + timedelta(days=dur))
        if a.start_override is not None:
            a.start = a.start_override
            a.finish = max(a.finish, a.start + timedelta(days=10))
    stage.acts[-1].finish = stage.end


# --------------------------------------------------------------------------------------------------------------------
# P1  Naharkatiya-Noonmati-Barauni Crude Oil Pipeline  (COMPLETED: every activity 100 %)
# --------------------------------------------------------------------------------------------------------------------
NNB = Project(
    code="NNB-COP-01",
    name="Naharkatiya–Noonmati–Barauni Crude Oil Pipeline",
    lifecycle="COMPLETED",
    location="Assam – Bihar corridor (Naharkatiya to Barauni)",
    project_type="Cross-country crude oil pipeline",
    description="Mainline crude oil pipeline with intermediate pumping stations and terminals. Demonstration dataset: delivered, commissioned and handed over.",
    start=d("2021-03-01"), finish=d("2023-06-30"), data_date=d("2023-07-15"),
    schedule_id="SCH-NNB-BL2",
    stages=[
        Stage("S1", "Route Survey, Land Acquisition & Permits", d("2021-03-01"), d("2021-09-30"), [
            Act("NNB-SUR-010", "Topographic and route alignment survey", "Centre-line survey and alignment sheets for the full pipeline corridor.", "CIVIL", "Full route, chainage 0–1160 km", 1160, "km", 100),
            Act("NNB-SUR-020", "Geotechnical investigation and soil boring", "Borehole drilling and soil resistivity testing along the route and at station sites.", "CIVIL", "Route chainage and station sites", 380, "boreholes", 100),
            Act("NNB-SUR-030", "Right-of-Use land acquisition and notification", "Notification, survey and compensation process for the pipeline right-of-use corridor.", "CIVIL", "Assam and Bihar districts", 1160, "km", 100),
            Act("NNB-SUR-040", "Forest and wildlife clearance for protected-area stretch", "Forest diversion proposal and wildlife board clearance for the protected-area crossing.", "HSE", "Kaziranga buffer corridor", 1, "approval", 100),
            Act("NNB-SUR-050", "Railway, highway and river-crossing permissions", "Statutory permissions for rail, NH and canal crossings.", "CIVIL", "Crossing locations along route", 64, "crossings", 100),
            Act("NNB-SUR-060", "Environmental and social impact assessment", "EIA study, public hearing and environmental management plan approval.", "HSE", "Full route", 1, "report", 100),
        ]),
        Stage("S2", "Procurement & Line Pipe Logistics", d("2021-07-01"), d("2022-04-30"), [
            Act("NNB-PRO-010", "Line pipe (API 5L X65) tender and award", "Bid evaluation and contract award for 24-inch line pipe.", "LOGISTICS", "Corporate office, Guwahati", 1, "contract", 100),
            Act("NNB-PRO-020", "Line pipe manufacture and mill inspection", "Pipe production at the mill with third-party inspection and release.", "PIPING", "Pipe mill", 98000, "tonnes", 100),
            Act("NNB-PRO-030", "Pipe coating and stockyard transfer", "Three-layer polyethylene coating and transport to regional stockyards.", "PIPING", "Coating yard and stockyards", 98000, "tonnes", 100),
            Act("NNB-PRO-040", "Mainline valve and pig-launcher procurement", "Supply of ball valves, scraper launcher and receiver traps.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 142, "sets", 100),
            Act("NNB-PRO-050", "Pump and driver procurement for pumping stations", "Mainline booster pumps with motor drivers for pumping stations.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 9, "units", 100),
            Act("NNB-PRO-060", "Power and instrumentation cable procurement", "HT/LT power cable, instrument cable and cable trays for all stations.", "ELECTRICAL", "Vendor works", 240, "km", 100),
            Act("NNB-PRO-070", "SCADA and telecom system procurement", "Supply of RTUs, flow computers, SCADA servers and optical fibre telecom.", "INSTRUMENTATION", "Vendor works", 1, "system", 100),
        ]),
        Stage("S3", "Pipeline Construction (Mainline)", d("2022-01-10"), d("2023-01-31"), [
            Act("NNB-PLC-010", "ROW clearing, grading and stringing", "Clear and grade the right-of-way and string coated pipe along the trench line.", "CIVIL", "Full route, chainage 0–1160 km", 1160, "km", 100),
            Act("NNB-PLC-020", "Trench excavation", "Excavate the pipeline trench to design depth including rock sections.", "CIVIL", "Full route, chainage 0–1160 km", 1160, "km", 100),
            Act("NNB-PLC-030", "Pipe bending, welding and NDT of girth welds", "Mainline welding, radiographic testing and weld repair.", "PIPING", "Full route, chainage 0–1160 km", 96000, "welds", 100),
            Act("NNB-PLC-040", "Field joint coating", "Apply and inspect heat-shrink field joint coating on all girth welds.", "PIPING", "Full route, chainage 0–1160 km", 96000, "joints", 100),
            Act("NNB-PLC-050", "Lowering-in and backfill", "Lower the welded pipeline into the trench, backfill and restore the surface.", "PIPING", "Full route, chainage 0–1160 km", 1160, "km", 100),
            Act("NNB-PLC-060", "Brahmaputra river crossing by horizontal directional drilling", "HDD crossing of the Brahmaputra including pullback of the pre-tested pipe string.", "CIVIL", "Brahmaputra crossing, chainage 212 km", 1, "crossing", 100),
            Act("NNB-PLC-070", "Cathodic protection system installation", "Impressed-current CP stations, test posts and anode beds along the route.", "ELECTRICAL", "Full route, CP stations", 38, "stations", 100),
        ]),
        Stage("S4", "Pumping & Terminal Stations", d("2022-06-01"), d("2023-03-31"), [
            Act("NNB-STN-010", "Station civil foundations and buildings", "Foundations, control buildings and drainage at pumping stations and terminals.", "CIVIL", "Pumping stations 1–9 and terminals", 9, "stations", 100),
            Act("NNB-STN-020", "Structural steel for pump shelters and pipe racks", "Fabrication and erection of pump shelters, pipe racks and platforms.", "STRUCTURAL", "Pumping stations 1–9", 1450, "tonnes", 100),
            Act("NNB-STN-030", "Pump and driver installation and alignment", "Setting, grouting and laser alignment of mainline pumps and motors.", "STATIC_ROTATING_EQUIPMENT", "Pumping stations 1–9", 9, "units", 100),
            Act("NNB-STN-040", "Station piping and mainline valve installation", "Station yard piping, manifolds and mainline block valve installation.", "PIPING", "Pumping stations 1–9", 9, "stations", 100),
            Act("NNB-STN-050", "Substation, MCC and cable laying", "Power substations, motor control centres and cable installation.", "ELECTRICAL", "Pumping stations 1–9", 9, "stations", 100),
            Act("NNB-STN-060", "Instrumentation, SCADA and telecom installation", "Field instruments, RTUs, SCADA and optical fibre termination.", "INSTRUMENTATION", "Pumping stations 1–9 and control centre", 9, "stations", 100),
            Act("NNB-STN-070", "Fire-fighting and safety systems", "Fire water network, foam systems and gas detection at stations.", "HSE", "Pumping stations 1–9", 9, "stations", 100),
        ]),
        Stage("S5", "Testing, Commissioning & Handover", d("2023-02-01"), d("2023-06-30"), [
            Act("NNB-COM-010", "Hydrostatic testing of line sections", "Section-wise hydrotest to 1.25 times design pressure with pressure-hold records.", "PIPING", "All pipeline test sections", 58, "sections", 100),
            Act("NNB-COM-020", "Pre-commissioning cleaning, gauging and drying", "Pigging, gauging and dewatering of tested sections.", "PIPING", "All pipeline test sections", 58, "sections", 100),
            Act("NNB-COM-030", "Loop checks and SCADA integration testing", "Instrument loop checks and end-to-end SCADA signal verification.", "INSTRUMENTATION", "Pumping stations and control centre", 1, "system", 100),
            Act("NNB-COM-040", "Electrical energisation and pump run tests", "Energise substations and run-test pumps and motors.", "ELECTRICAL", "Pumping stations 1–9", 9, "stations", 100),
            Act("NNB-COM-050", "Crude oil introduction and line fill", "Introduce crude oil and complete the line fill with leak monitoring.", "PROCESS", "Full route", 1, "line fill", 100),
            Act("NNB-COM-060", "Performance trial run and capacity test", "Demonstrate design throughput during the guaranteed trial run.", "PROCESS", "Full route", 1, "trial run", 100),
            Act("NNB-COM-070", "As-built documentation and handover", "Compile as-built drawings, test records and handover to operations.", "CIVIL", "Project office", 1, "dossier", 100),
        ]),
    ],
    root_causes={
        "logistics": ("MATERIAL_DELIVERY_DELAY", "Mill and transport logistics for line pipe",
                      "Pipe deliveries slipped repeatedly because mill dispatch was not coordinated with rail availability."),
    },
    issues=[
        IssueSeed("pipe", "NNB-PRO-020", "MATERIAL_DELIVERY_DELAY", "HIGH", "Line pipe delivery delayed by mill logistics",
                  "Second pipe lot missed the rail slot; 14 km of pipe was not available at the stockyard for stringing.",
                  d("2021-11-10"), 30, True, d("2021-12-18"),
                  "Alternate supplier engaged for 60 km of pipe; dispatch schedule revised with rail slots booked a month ahead.",
                  "Delay held to 28 days instead of the forecast 45; stringing resumed with no demobilisation.", "logistics", True),
        IssueSeed("flood", "NNB-PLC-020", "WEATHER", "HIGH", "Monsoon flooding of open trench",
                  "Heavy rain flooded 9 km of open trench in the Assam plains; side-wall collapse in two sections.",
                  d("2022-07-18"), 20, True, d("2022-08-09"),
                  "Dewatering pumps and trench breakers deployed; excavation sequenced so no more than 2 km stayed open.",
                  "No further collapses; productivity recovered within three weeks.", None, True),
        IssueSeed("forest", "NNB-SUR-040", "PERMIT_APPROVAL", "MEDIUM", "Forest clearance query pending with the ministry",
                  "State forest department raised queries on compensatory afforestation, holding the diversion approval.",
                  d("2021-06-02"), 60, False, d("2021-08-20"),
                  "Compensatory afforestation plan resubmitted with the state's concurrence and a joint site inspection.",
                  "Clearance granted before the start of RoU handover for that stretch.", None, True),
        IssueSeed("weld", "NNB-PLC-030", "TECHNICAL", "HIGH", "Girth weld radiography rejection above limit",
                  "Early production welds showed a repair rate above the 4 percent acceptance limit.",
                  d("2022-03-22"), 15, False, d("2022-04-12"),
                  "Welders requalified, welding procedure parameters tightened and a pre-heat check added at every joint.",
                  "Repair rate dropped below 2 percent for the rest of the mainline.", None, True),
        IssueSeed("hdd", "NNB-PLC-060", "SITE_ACCESS", "MEDIUM", "River bank erosion blocked HDD rig access",
                  "Bank erosion after flood season cut the access road to the HDD entry-side work area.",
                  d("2022-10-05"), 12, True, d("2022-10-24"),
                  "Temporary causeway and gabion bank protection built; rig moved in by barge for the final spread.",
                  "Pullback completed with a 16-day slip.", None, False),
        IssueSeed("pump", "NNB-COM-010", "EQUIPMENT_SHORTAGE", "MEDIUM", "Hydrotest pump set unavailable",
                  "High-pressure test pump scheduled for two sections was held at another site.",
                  d("2023-03-02"), 8, False, d("2023-03-09"),
                  "Second pump set hired locally; test sections re-sequenced to keep both crews busy.",
                  "Test programme recovered with no change to the commissioning date.", None, False),
        IssueSeed("sub", "NNB-STN-050", "CONTRACTOR_ISSUE", "MEDIUM", "Electrical subcontractor slow to mobilise",
                  "Electrical subcontractor reached 40 percent of the planned manpower in the first month.",
                  d("2022-08-16"), 14, False, d("2022-09-12"),
                  "Weekly manpower reconciliation introduced with the contractor and an additional gang was hired.",
                  "Station electrical works caught up within six weeks.", None, True),
    ],
)

# --------------------------------------------------------------------------------------------------------------------
# P2  The Andaman & East Coast Offshore Drilling Campaign  (ONGOING)
# --------------------------------------------------------------------------------------------------------------------
AND = Project(
    code="AND-ODC-01",
    name="The Andaman & East Coast Offshore Drilling Campaign",
    lifecycle="ONGOING",
    location="East Coast and Andaman offshore basins; supply base at Kakinada",
    project_type="Offshore exploration drilling campaign",
    description="Multi-well exploration campaign using a jack-up rig with marine logistics from Kakinada. Demonstration dataset.",
    start=d("2025-10-01"), finish=d("2027-03-31"), data_date=d("2026-09-30"),
    schedule_id="SCH-AND-BL1",
    stages=[
        Stage("S1", "Campaign Planning & Regulatory Approvals", d("2025-10-01"), d("2025-12-15"), [
            Act("AND-PLN-010", "Well location selection and prospect finalisation", "Final well coordinates and target depths from the integrated subsurface study.", "DRILLING", "Technical office, Kakinada", 3, "wells", 100),
            Act("AND-PLN-020", "Detailed well design and drilling programme", "Casing design, mud programme and drilling programme for each well.", "DRILLING", "Technical office, Kakinada", 3, "programmes", 100),
            Act("AND-PLN-030", "Environmental clearance and CRZ approval", "Environmental clearance and coastal regulation zone approval for offshore operations.", "HSE", "Regulatory offices", 1, "approval", 100),
            Act("AND-PLN-040", "Well permits and Coast Guard marine notices", "Regulator well permits, Navy and Coast Guard notices to mariners.", "HSE", "Regulatory offices", 3, "permits", 100),
            Act("AND-PLN-050", "Jack-up rig tender and contract award", "Technical and commercial evaluation and award of the drilling rig contract.", "LOGISTICS", "Corporate office", 1, "contract", 100),
            Act("AND-PLN-060", "Long-lead items order for casing, wellheads and BOP", "Order casing strings, wellhead systems and blowout preventer spares.", "LOGISTICS", "Corporate office", 1, "order", 100),
        ]),
        Stage("S2", "Mobilization & Marine Logistics", d("2025-12-01"), d("2026-05-31"), [
            Act("AND-MOB-010", "Jack-up rig inspection and acceptance at Kakinada yard", "Pre-hire rig inspection, class survey and acceptance.", "DRILLING", "Kakinada yard", 1, "rig", 100),
            Act("AND-MOB-020", "Rig tow-out and positioning at AN-OSN block", "Wet tow of the rig and positioning over the first well location.", "LOGISTICS", "AN-OSN block, offshore", 1, "tow", 100),
            Act("AND-MOB-030", "Casing and tubular delivery to supply base", "Receive, inspect and stack casing and tubulars at the supply base.", "LOGISTICS", "Kakinada supply base", 4200, "tonnes", 100),
            Act("AND-MOB-040", "Mud, cement and chemicals stocking", "Stock drilling fluid chemicals, barite and cement at the supply base and rig.", "LOGISTICS", "Kakinada supply base", 2600, "tonnes", 90),
            Act("AND-MOB-050", "Supply vessel and crew-boat charter", "Charter and mobilise two supply vessels and a crew boat.", "LOGISTICS", "Kakinada port", 3, "vessels", 100),
            Act("AND-MOB-060", "Blowout preventer installation and pressure test", "Install the BOP stack and run the full pressure and function test.", "DRILLING", "Rig floor, AN-OSN block", 1, "stack", 70),
            Act("AND-MOB-070", "Offshore crew induction and sea survival training", "Mandatory offshore induction, sea survival and well-control certification.", "HSE", "Kakinada training centre", 96, "personnel", 55),
        ]),
        Stage("S3", "Rig Preparation & Seabed Site Works", d("2026-03-01"), d("2026-08-31"), [
            Act("AND-SIT-010", "Seabed and shallow hazard survey", "Geophysical survey of the seabed and shallow hazards at the well location.", "DRILLING", "AN-OSN well location", 1, "survey", 100),
            Act("AND-SIT-020", "Rig leg preload and jacking operation", "Preload the legs and jack the hull to the operating air gap.", "STRUCTURAL", "AN-OSN well location", 1, "operation", 100),
            Act("AND-SIT-030", "Conductor pipe driving", "Drive the 30-inch conductor to the target penetration.", "DRILLING", "Rig floor, AN-OSN block", 1, "conductor", 85),
            Act("AND-SIT-040", "Mooring and anchor pattern for support vessels", "Lay the anchor pattern and moorings for stand-by and supply vessels.", "LOGISTICS", "AN-OSN well location", 6, "anchors", 70),
            Act("AND-SIT-050", "Rig power generation and distribution check", "Load test of generators and switchboards and rig electrical inspection.", "ELECTRICAL", "Rig machinery spaces", 1, "inspection", 60),
            Act("AND-SIT-060", "Mud-logging unit and rig instrumentation calibration", "Calibrate drilling sensors and commission the mud-logging unit.", "INSTRUMENTATION", "Rig instrument room", 1, "unit", 40),
            Act("AND-SIT-070", "Helideck and emergency evacuation certification", "Helideck inspection, lifeboat and evacuation system certification.", "HSE", "Rig helideck and lifeboats", 1, "certificate", 35),
        ]),
        Stage("S4", "Drilling Operations", d("2026-06-01"), d("2026-12-31"), [
            Act("AND-DRL-010", "Spud and drill 26-inch top-hole section", "Spud the well and drill the top-hole section to casing point.", "DRILLING", "Rig floor, AN-OSN block", 780, "m", 100),
            Act("AND-DRL-020", "Run and cement 20-inch surface casing", "Run the 20-inch casing and cement to the seabed.", "DRILLING", "Rig floor, AN-OSN block", 780, "m", 100),
            Act("AND-DRL-030", "Drill 17.5-inch intermediate section", "Drill the intermediate section through the transition zone.", "DRILLING", "Rig floor, AN-OSN block", 1650, "m", 55),
            Act("AND-DRL-040", "Run and cement 13-3/8 inch casing", "Run the 13-3/8 inch casing string and perform the cement job.", "DRILLING", "Rig floor, AN-OSN block", 2430, "m", 25),
            Act("AND-DRL-050", "Drill 12.25-inch reservoir section", "Drill into the reservoir interval with managed pressure control.", "DRILLING", "Rig floor, AN-OSN block", 1100, "m", 0),
            Act("AND-DRL-060", "Wireline logging and formation evaluation", "Open-hole wireline logs, formation pressure points and sampling.", "INSTRUMENTATION", "Rig floor, AN-OSN block", 1, "logging run", 0),
            Act("AND-DRL-070", "Drilling fluid management and waste handling", "Mud system maintenance, cuttings management and waste manifesting.", "HSE", "Rig mud pits", 1, "campaign", 45, d("2026-06-01")),
        ]),
        Stage("S5", "Well Testing & Completion", d("2026-09-01"), d("2027-03-31"), [
            Act("AND-TST-010", "Drill-stem test programme and procedure approval", "Prepare and approve the well test programme and safety procedures.", "PROCESS", "Technical office, Kakinada", 1, "procedure", 85),
            Act("AND-TST-020", "Surface test spread rig-up and pressure testing", "Rig up separators, flare boom and heater and pressure-test the spread.", "PROCESS", "Rig deck", 1, "spread", 30),
            Act("AND-TST-030", "Perforation and completion string running", "Perforate the pay zone and run the test completion string.", "DRILLING", "Rig floor, AN-OSN block", 1, "string", 0),
            Act("AND-TST-040", "Flow-back and production testing", "Clean-up flow, multi-rate flow test and fluid sampling.", "PROCESS", "Rig deck", 1, "test", 0),
            Act("AND-TST-050", "Well kill, suspension and plug-and-abandon", "Kill the well and install the suspension and abandonment plugs.", "DRILLING", "Rig floor, AN-OSN block", 1, "well", 0),
            Act("AND-TST-060", "Rig demobilisation and tow-out", "Jack down, retrieve conductor and tow the rig out of the block.", "LOGISTICS", "AN-OSN block, offshore", 1, "tow", 0),
            Act("AND-TST-070", "Well test data submission and campaign close-out report", "Submit well test data and the close-out report to the regulator.", "PROCESS", "Technical office, Kakinada", 1, "report", 0),
        ]),
    ],
    root_causes={
        "sea_state": ("WEATHER", "Monsoon-season sea-state downtime",
                      "Repeated swell and cyclonic disturbance interrupted supply-vessel transfers, anchor handling and drilling."),
    },
    issues=[
        IssueSeed("cyclone", "AND-DRL-030", "WEATHER", "HIGH", "Cyclonic disturbance — rig secured, operations suspended",
                  "Rig moved to storm configuration and crew reduced to essential personnel for six days.",
                  d("2026-08-12"), 6, True, d("2026-08-19"),
                  "Operations resumed after sea state fell below the operating limit; bit and BHA re-inspected before tripping in.",
                  "Six days lost; no equipment damage.", "sea_state", True),
        IssueSeed("swell", "AND-MOB-040", "WEATHER", "MEDIUM", "Swell above 3 m halted supply-vessel transfers",
                  "Cement and barite transfers could not be completed for five days because of monsoon swell.",
                  d("2026-06-20"), 5, False, d("2026-06-27"),
                  "Rig stock level raised to ten days of consumables before each forecast weather window.",
                  "No drilling stand-by caused afterwards.", "sea_state", False),
        IssueSeed("anchor", "AND-SIT-040", "WEATHER", "MEDIUM", "High sea state delayed anchor handling",
                  "Anchor-handling vessel could not deploy the south-west anchors in the planned window.",
                  d("2026-07-08"), 4, False, d("2026-07-14"),
                  "Anchor deployment moved to the next tide window; pattern reduced from eight to six anchors.",
                  "Completed with a four-day slip.", "sea_state", False),
        IssueSeed("bop", "AND-MOB-060", "EQUIPMENT_SHORTAGE", "CRITICAL", "BOP annular preventer failed pressure test",
                  "Annular preventer failed the 5,000 psi test and no spare element was held on the rig.",
                  d("2026-07-30"), 20, True, None, None, None, None, False),
        IssueSeed("casing", "AND-DRL-040", "MATERIAL_DELIVERY_DELAY", "HIGH", "13-3/8 inch casing shipment held at port",
                  "Casing shipment is held in customs; the cement job cannot start without the string.",
                  d("2026-09-05"), 15, True, None, None, None, None, False),
        IssueSeed("heli", "AND-SIT-070", "PERMIT_APPROVAL", "MEDIUM", "Helideck certification inspection slot delayed",
                  "Regulator inspection slot for helideck certification was rescheduled by three weeks.",
                  d("2026-08-20"), 14, False, None, None, None, None, False),
        IssueSeed("crew", "AND-MOB-070", "LABOUR_SHORTAGE", "LOW", "Certified well-control personnel not available for crew change",
                  "Two well-control certified drillers were unavailable for the scheduled crew change.",
                  d("2026-07-01"), 4, False, d("2026-07-06"),
                  "Standby drillers flown from the Mumbai pool; certification roster now reviewed 30 days ahead.",
                  "Crew change completed on the next rotation.", None, False),
    ],
    claims=[
        ClaimSeed("bop_rej", "AND-MOB-060", "REJECT", 100.0, None, d("2026-08-05"),
                  "BOP stack installation and pressure test 100 percent complete",
                  "Annular preventer test failed on 30 July; the stack cannot be reported complete until the retest passes."),
        ClaimSeed("cond_edit", "AND-SIT-030", "EDIT", 95.0, 85.0, d("2026-09-12"),
                  "Conductor pipe driving 95 percent complete",
                  "Penetration record supports 85 percent; final 15 percent still requires driving to refusal."),
        ClaimSeed("anchor_hold", "AND-SIT-040", "HOLD", 85.0, None, d("2026-09-20"),
                  "Anchor pattern 85 percent complete, five of six anchors set",
                  "Please attach the ROV anchor position survey before this can be approved."),
    ],
)

# --------------------------------------------------------------------------------------------------------------------
# P3  Numaligarh Refinery Expansion Project (NRL)  (ONGOING)
# --------------------------------------------------------------------------------------------------------------------
NRL = Project(
    code="NRL-EXP-01",
    name="Numaligarh Refinery Expansion Project (NRL)",
    lifecycle="ONGOING",
    location="Numaligarh, Golaghat district, Assam",
    project_type="Refinery capacity expansion",
    description="Crude distillation capacity expansion with new heater, columns, utilities and control systems. Demonstration dataset.",
    start=d("2025-04-01"), finish=d("2027-09-30"), data_date=d("2026-09-30"),
    schedule_id="SCH-NRL-BL3",
    stages=[
        Stage("S1", "Engineering, Design & Approvals", d("2025-04-01"), d("2025-11-30"), [
            Act("NRL-ENG-010", "Basic engineering package for crude unit expansion", "Process flow schemes, heat and material balance and unit design basis.", "PROCESS", "Engineering office", 1, "package", 100),
            Act("NRL-ENG-020", "Detailed piping engineering and stress analysis", "Piping isometrics, line list and flexibility analysis for new unit.", "PIPING", "Engineering office", 1850, "isometrics", 100),
            Act("NRL-ENG-030", "Plot plan, 3D model review and HAZOP", "3D model reviews and the HAZOP study for the new facilities.", "PROCESS", "Engineering office", 3, "reviews", 100),
            Act("NRL-ENG-040", "Statutory approvals from PESO, Factories and pollution board", "Licences and consent approvals for the expanded facilities.", "HSE", "Regulatory offices", 4, "approvals", 90),
            Act("NRL-ENG-050", "Soil investigation and foundation design", "Geotechnical investigation and foundation design for heavy equipment.", "CIVIL", "Unit plot area", 1, "report", 100),
            Act("NRL-ENG-060", "Electrical load study and single-line diagram approval", "Load flow and short-circuit studies and approved single-line diagrams.", "ELECTRICAL", "Engineering office", 1, "package", 100),
            Act("NRL-ENG-070", "Instrumentation design: P&IDs, datasheets and control philosophy", "Instrument index, datasheets and DCS/ESD control philosophy.", "INSTRUMENTATION", "Engineering office", 1, "package", 80),
        ]),
        Stage("S2", "Procurement & Material Management", d("2025-06-01"), d("2026-06-30"), [
            Act("NRL-PRC-010", "Fired heater and heat exchanger purchase", "Place orders for the crude heater and shell-and-tube exchangers.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 14, "units", 100),
            Act("NRL-PRC-020", "Pressure vessel and column fabrication", "Fabrication of the atmospheric and vacuum columns and drums.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 11, "vessels", 90),
            Act("NRL-PRC-030", "Structural steel supply", "Supply of pipe rack, platform and equipment structure steel.", "STRUCTURAL", "Vendor works to site stockyard", 3400, "tonnes", 70),
            Act("NRL-PRC-040", "Pumps, compressors and drivers supply", "Process pumps, compressors and drivers delivered to site.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 46, "units", 65),
            Act("NRL-PRC-050", "Large-bore piping and valves supply", "Carbon and alloy steel pipe, fittings and valves for the unit.", "PIPING", "Vendor works to site stockyard", 5200, "tonnes", 80),
            Act("NRL-PRC-060", "HT/LT switchgear and transformer supply", "Supply of 11 kV switchgear, MCCs and power transformers.", "ELECTRICAL", "Vendor works", 36, "panels", 70),
            Act("NRL-PRC-070", "DCS, ESD and field instrument supply", "Supply of DCS and ESD systems and field instruments.", "INSTRUMENTATION", "Vendor works", 1, "system", 55),
        ]),
        Stage("S3", "Site Preparation & Civil Works", d("2025-09-01"), d("2026-07-31"), [
            Act("NRL-CIV-010", "Site clearing, levelling and temporary roads", "Clear and level the expansion plot and build construction roads.", "CIVIL", "Expansion plot, Unit 5", 62, "acres", 100),
            Act("NRL-CIV-020", "Piling for heavy equipment foundations", "Bored cast-in-situ piles under columns, heater and compressors.", "CIVIL", "Unit 5 equipment area", 1480, "piles", 100),
            Act("NRL-CIV-030", "Pile caps and equipment foundations", "Reinforced concrete pile caps and equipment foundations.", "CIVIL", "Unit 5 equipment area", 9200, "m3", 90),
            Act("NRL-CIV-040", "Pipe rack and sleeper foundations", "Pier and strip foundations for the main pipe rack and sleepers.", "CIVIL", "Unit 5 pipe rack", 4100, "m3", 85),
            Act("NRL-CIV-050", "Underground drainage and oily water sewer", "Underground drains, sewers and chambers for the new unit.", "CIVIL", "Unit 5 plot", 8600, "m", 70),
            Act("NRL-CIV-060", "Control room and substation buildings", "RCC control room, substation and analyser shelters.", "CIVIL", "Unit 5 buildings area", 3, "buildings", 60),
        ]),
        Stage("S4", "Structural & Mechanical Erection", d("2026-02-01"), d("2027-02-28"), [
            Act("NRL-ERC-010", "Pipe rack structural steel erection", "Erect the pipe rack steel and cross-bracing.", "STRUCTURAL", "Unit 5 pipe rack", 1900, "tonnes", 75),
            Act("NRL-ERC-020", "Crude column and vacuum column erection", "Heavy lift and erection of the atmospheric and vacuum columns.", "STATIC_ROTATING_EQUIPMENT", "Unit 5 column area", 2, "columns", 60),
            Act("NRL-ERC-030", "Fired heater erection", "Erect the heater structure, tubes and refractory lining.", "STATIC_ROTATING_EQUIPMENT", "Unit 5 heater bay", 1, "heater", 45),
            Act("NRL-ERC-040", "Heat exchanger setting and bundle handling", "Set the exchangers on their saddles and align.", "STATIC_ROTATING_EQUIPMENT", "Unit 5 exchanger area", 14, "units", 40),
            Act("NRL-ERC-050", "Pump and compressor installation and grouting", "Install and grout pumps and compressors on their foundations.", "STATIC_ROTATING_EQUIPMENT", "Unit 5 pump area", 46, "units", 30),
            Act("NRL-ERC-060", "Platforms, ladders and access structures", "Fabricate and install platforms and ladders on columns and vessels.", "STRUCTURAL", "Unit 5 column area", 620, "tonnes", 35),
            Act("NRL-ERC-070", "Insulation and fireproofing", "Thermal insulation and passive fire protection on steel and vessels.", "STRUCTURAL", "Unit 5 all areas", 14500, "m2", 10),
        ]),
        Stage("S5", "Piping, Electrical & Instrumentation", d("2026-05-01"), d("2027-06-30"), [
            Act("NRL-PEI-010", "Process piping fabrication and spool preparation", "Prefabricate piping spools and prepare weld packs.", "PIPING", "Spool fabrication yard", 4200, "spools", 55),
            Act("NRL-PEI-020", "Piping erection and welding", "Erect and weld process piping in the unit.", "PIPING", "Unit 5 piping areas", 62000, "inch-dia", 35),
            Act("NRL-PEI-030", "Radiography and NDT of process welds", "Radiography, PMI and hardness testing of process welds.", "PIPING", "Unit 5 piping areas", 5800, "welds", 25),
            Act("NRL-PEI-040", "Cable tray, cable laying and termination", "Install cable trays, lay and terminate power and control cables.", "ELECTRICAL", "Unit 5 cable corridors", 210, "km", 30),
            Act("NRL-PEI-050", "Transformer and switchgear installation", "Install transformers and switchgear in the substation.", "ELECTRICAL", "Unit 5 substation", 36, "panels", 25, d("2026-08-17")),
            Act("NRL-PEI-060", "Field instrument installation and tubing", "Install field instruments, impulse tubing and junction boxes.", "INSTRUMENTATION", "Unit 5 plot", 2400, "instruments", 20, d("2026-09-07")),
            Act("NRL-PEI-070", "DCS and ESD panel installation", "Install and wire the DCS and ESD marshalling cabinets.", "INSTRUMENTATION", "Unit 5 control room", 28, "cabinets", 10, d("2026-09-21")),
        ]),
        Stage("S6", "Testing, Commissioning & Start-up", d("2026-09-01"), d("2027-09-30"), [
            Act("NRL-COM-010", "Commissioning and start-up procedure preparation", "Prepare precommissioning checklists and start-up procedures.", "PROCESS", "Engineering office", 1, "package", 15),
            Act("NRL-COM-020", "Hydrotest of piping systems", "Hydrostatic test of the new piping loops.", "PIPING", "Unit 5 plot", 310, "loops", 0),
            Act("NRL-COM-030", "Loop checks and DCS/ESD functional testing", "Instrument loop checks and cause-and-effect testing.", "INSTRUMENTATION", "Unit 5 control room", 2400, "loops", 0),
            Act("NRL-COM-040", "Electrical energisation and motor run-in", "Energise the substation and run-in motors.", "ELECTRICAL", "Unit 5 substation", 46, "motors", 0),
            Act("NRL-COM-050", "Safety valve calibration and fire system certification", "Calibrate relief valves and certify the fire-fighting system.", "HSE", "Unit 5 plot", 1, "certificate", 0),
            Act("NRL-COM-060", "Process commissioning and feed-in", "Introduce feed and stabilise the new crude unit.", "PROCESS", "Unit 5", 1, "unit", 0),
            Act("NRL-COM-070", "Performance guarantee test run", "Demonstrate guaranteed throughput and product yields.", "PROCESS", "Unit 5", 1, "test run", 0),
        ]),
    ],
    root_causes={
        "labour": ("LABOUR_SHORTAGE", "Skilled-labour shortage across civil, erection and piping",
                   "Welders, fitters, riggers and carpenters were short on several activities at once after mobilisation of the parallel expansion at the same site."),
        "steel": ("MATERIAL_DELIVERY_DELAY", "Structural steel supplier logistics",
                  "Steel consignments from the fabricator were delayed in transit, holding erection of the pipe rack."),
    },
    issues=[
        IssueSeed("weld", "NRL-PEI-010", "LABOUR_SHORTAGE", "HIGH", "Welder and fitter shortage on piping spools",
                  "Spool fabrication is running with 22 welders against 40 planned; fitters are also short.",
                  d("2026-07-06"), 14, False, None, None, None, "labour", False),
        IssueSeed("rigger", "NRL-ERC-020", "LABOUR_SHORTAGE", "HIGH", "Insufficient rigging crew for column erection",
                  "Certified riggers for the heavy lift are short; the lift sequence cannot be staffed in two shifts.",
                  d("2026-07-20"), 10, False, None, None, None, "labour", False),
        IssueSeed("carp", "NRL-CIV-060", "LABOUR_SHORTAGE", "MEDIUM", "Shuttering carpenters unavailable during festival attendance dip",
                  "Carpenters for building shuttering fell to 60 percent of plan over the festival period.",
                  d("2026-06-15"), 7, False, d("2026-07-02"),
                  "Additional crew mobilised from the Jorhat contractor pool and shuttering sequence staggered.",
                  "Slab pours resumed with a nine-day slip.", "labour", True),
        IssueSeed("pipeweld", "NRL-PEI-020", "LABOUR_SHORTAGE", "MEDIUM", "Piping erection gangs below plan",
                  "Erection gangs are at 70 percent of plan since the spool shortage reduced the work front.",
                  d("2026-08-24"), 12, False, None, None, None, "labour", False),
        IssueSeed("steel1", "NRL-PRC-030", "MATERIAL_DELIVERY_DELAY", "HIGH", "Structural steel consignment delayed in transit",
                  "Two steel consignments are held up between the fabricator and site; vendor logistics partner missed dispatch.",
                  d("2026-08-03"), 21, True, None, None, None, "steel", False),
        IssueSeed("steel2", "NRL-ERC-010", "MATERIAL_DELIVERY_DELAY", "HIGH", "Pipe rack erection waiting for steel members",
                  "Erection crew idle at two bays because members from the delayed consignment have not arrived.",
                  d("2026-08-18"), 18, True, None, None, None, "steel", False),
        IssueSeed("crane", "NRL-ERC-020", "EQUIPMENT_SHORTAGE", "MEDIUM", "300 t crawler crane shared with another unit",
                  "The heavy-lift crane is committed to another unit for two weeks, delaying the column lift preparation.",
                  d("2026-07-28"), 9, False, None, None, None, None, False),
        IssueSeed("rack", "NRL-CIV-040", "DESIGN_DOCUMENTATION", "MEDIUM", "Pipe rack loading drawing revised",
                  "Revised pipe rack loading drawing changed anchor bolt layout on 40 foundations.",
                  d("2026-05-12"), 10, False, d("2026-05-26"),
                  "Template re-issued and bolt layouts corrected before concrete pours; design office put a hold on late revisions.",
                  "No concrete rework needed.", None, True),
        IssueSeed("lift", "NRL-ERC-030", "SAFETY", "LOW", "Near-miss during heater module lift",
                  "Tag line slipped during a module lift; no injury, exclusion zone was intact.",
                  d("2026-09-02"), 1, False, d("2026-09-04"),
                  "Lift plan reviewed, tag line procedure briefed to all crews and a banksman was added to each lift.",
                  "Subsequent lifts completed with no further incident.", None, True),
    ],
    claims=[
        ClaimSeed("cap_rej", "NRL-CIV-030", "REJECT", 100.0, None, d("2026-09-14"),
                  "Pile caps and equipment foundations 100 percent complete",
                  "Cube test results for the last six pours are not attached and 14 caps are still being stripped."),
        ClaimSeed("rack_edit", "NRL-ERC-010", "EDIT", 85.0, 75.0, d("2026-09-18"),
                  "Pipe rack steel erection 85 percent complete",
                  "Erection records support 75 percent; two bays are waiting for the delayed consignment."),
        ClaimSeed("pump_hold", "NRL-ERC-050", "HOLD", 40.0, None, d("2026-09-22"),
                  "Pump and compressor installation 40 percent complete",
                  "Please attach alignment records for the pumps counted before this can be approved."),
    ],
)

# --------------------------------------------------------------------------------------------------------------------
# P4  Siliguri-to-Mughalsarai Cross-Country Fuel Pipeline  (UPCOMING: planned, nothing started)
# --------------------------------------------------------------------------------------------------------------------
SMP = Project(
    code="SMP-CCP-01",
    name="The Siliguri-to-Mughalsarai Cross-Country Fuel Pipeline",
    lifecycle="UPCOMING",
    location="Siliguri (West Bengal) – Bihar – Mughalsarai (Uttar Pradesh)",
    project_type="Cross-country petroleum products pipeline",
    description="Planned petroleum products pipeline with dispatch stations and SCADA. Demonstration dataset: approved and planned, not yet in execution.",
    start=d("2027-01-11"), finish=d("2028-12-15"), data_date=d("2026-09-30"),
    schedule_id="SCH-SMP-BL1",
    stages=[
        Stage("S1", "Pre-Project Studies & Statutory Approvals", d("2027-01-11"), d("2027-07-30"), [
            Act("SMP-PRE-010", "Feasibility report and route selection", "Techno-economic feasibility and preferred route selection.", "CIVIL", "Project office, Siliguri", 1, "report", 0),
            Act("SMP-PRE-020", "Detailed route survey from Siliguri to Mughalsarai", "Centre-line and topographic survey of the selected route.", "CIVIL", "Full route", 1000, "km", 0),
            Act("SMP-PRE-030", "Environmental and social impact assessment", "EIA study, public consultation and management plan.", "HSE", "Full route", 1, "report", 0),
            Act("SMP-PRE-040", "PNGRB authorisation and regulatory approvals", "Regulator authorisation and statutory approvals for the pipeline.", "HSE", "Regulatory offices", 3, "approvals", 0),
            Act("SMP-PRE-050", "Geotechnical and river-crossing studies for Kosi and Gandak", "Geotechnical studies and hydraulic scour assessment for major crossings.", "CIVIL", "Kosi and Gandak crossings", 6, "crossings", 0),
            Act("SMP-PRE-060", "Hydraulic simulation and capacity design", "Hydraulic model and capacity design of pipeline and stations.", "PROCESS", "Engineering office", 1, "model", 0),
        ]),
        Stage("S2", "Land Acquisition & Right-of-Use", d("2027-04-01"), d("2027-12-31"), [
            Act("SMP-LND-010", "Land requirement plan and RoU notification", "Survey plot-wise requirement and notify the right-of-use.", "CIVIL", "Districts along route", 1000, "km", 0),
            Act("SMP-LND-020", "Compensation assessment and disbursement", "Assess and disburse land and crop compensation.", "CIVIL", "Districts along route", 1000, "km", 0),
            Act("SMP-LND-030", "Forest clearance for Mahananda sanctuary stretch", "Forest diversion proposal and clearance for the sanctuary stretch.", "HSE", "Mahananda sanctuary stretch", 1, "approval", 0),
            Act("SMP-LND-040", "Railway and National Highway crossing permissions", "Statutory permissions for rail and highway crossings.", "CIVIL", "Crossing locations along route", 82, "crossings", 0),
            Act("SMP-LND-050", "Power line and utility crossing agreements", "Agreements with power utilities for overhead and underground crossings.", "ELECTRICAL", "Crossing locations along route", 140, "crossings", 0),
            Act("SMP-LND-060", "ROW demarcation and handover", "Demarcate the right-of-way and take over the corridor.", "CIVIL", "Full route", 1000, "km", 0),
        ]),
        Stage("S3", "Procurement & Contracting", d("2027-05-01"), d("2028-03-31"), [
            Act("SMP-PRO-010", "EPC contract bidding and award", "Bid evaluation and award of the EPC package.", "LOGISTICS", "Corporate office", 1, "contract", 0),
            Act("SMP-PRO-020", "Line pipe order (API 5L X70)", "Order for 18-inch line pipe.", "PIPING", "Pipe mill", 76000, "tonnes", 0),
            Act("SMP-PRO-030", "Pipe coating and mill inspection", "Coating and third-party inspection of line pipe.", "PIPING", "Coating yard", 76000, "tonnes", 0),
            Act("SMP-PRO-040", "Mainline valves, scraper traps and ESD valves", "Procurement of block valves, scraper traps and ESD valves.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 118, "sets", 0),
            Act("SMP-PRO-050", "Pumps and metering skids for dispatch stations", "Procurement of booster pumps and custody-transfer metering skids.", "STATIC_ROTATING_EQUIPMENT", "Vendor works", 7, "skids", 0),
            Act("SMP-PRO-060", "SCADA, telecom and leak-detection system", "Procure the SCADA, telecom and leak-detection systems.", "INSTRUMENTATION", "Vendor works", 1, "system", 0),
            Act("SMP-PRO-070", "Power supply and cathodic protection equipment", "Procure transformers, switchgear and CP equipment.", "ELECTRICAL", "Vendor works", 1, "package", 0),
        ]),
        Stage("S4", "Pipeline Construction", d("2027-10-01"), d("2028-09-30"), [
            Act("SMP-CON-010", "ROW clearing and grading", "Clear and grade the right-of-way for construction spreads.", "CIVIL", "Full route", 1000, "km", 0),
            Act("SMP-CON-020", "Stringing, bending and welding", "String, bend and weld the pipeline.", "PIPING", "Full route", 82000, "welds", 0),
            Act("SMP-CON-030", "NDT and field joint coating", "Radiography and field joint coating.", "PIPING", "Full route", 82000, "joints", 0),
            Act("SMP-CON-040", "Trenching, lowering-in and backfill", "Excavate trench, lower in and backfill.", "CIVIL", "Full route", 1000, "km", 0),
            Act("SMP-CON-050", "Major river crossings by HDD at Kosi and Gandak", "Horizontal directional drilling of the Kosi and Gandak crossings.", "CIVIL", "Kosi and Gandak crossings", 2, "crossings", 0),
            Act("SMP-CON-060", "Intermediate pumping and dispatch station construction", "Civil and structural works for stations.", "STRUCTURAL", "Station sites", 7, "stations", 0),
            Act("SMP-CON-070", "Station electrical and instrumentation installation", "Install electrical and instrumentation systems at stations.", "ELECTRICAL", "Station sites", 7, "stations", 0),
        ]),
        Stage("S5", "Testing, Commissioning & Handover", d("2028-07-01"), d("2028-12-15"), [
            Act("SMP-COM-010", "Hydrostatic testing", "Hydrotest of the pipeline sections.", "PIPING", "All test sections", 46, "sections", 0),
            Act("SMP-COM-020", "Pigging, dewatering and drying", "Pigging, dewatering and drying.", "PIPING", "All test sections", 46, "sections", 0),
            Act("SMP-COM-030", "SCADA and leak-detection integration testing", "Integration testing of SCADA and leak detection.", "INSTRUMENTATION", "Control centre and stations", 1, "system", 0),
            Act("SMP-COM-040", "Product fill and line-pack commissioning", "Fill the line with product and commission.", "PROCESS", "Full route", 1, "commissioning", 0),
            Act("SMP-COM-050", "Safety audit and emergency response drill", "Pre-start-up safety audit and an emergency drill.", "HSE", "Stations and route", 1, "audit", 0),
            Act("SMP-COM-060", "Handover and as-built documentation", "As-built drawings and handover to operations.", "CIVIL", "Project office", 1, "dossier", 0),
        ]),
    ],
    root_causes={},
    issues=[
        IssueSeed("forest", "SMP-LND-030", "PERMIT_APPROVAL", "MEDIUM", "Forest clearance proposal not yet submitted for sanctuary stretch",
                  "The sanctuary stretch needs a wildlife board recommendation; proposal preparation has not started.",
                  d("2026-09-10"), 90, False, None, None, None, None, False),
    ],
)

# Cause text for resolved issues that are not (yet) grouped under a root cause; used when the issue becomes memory.
CAUSES: Dict[tuple, str] = {
    ("NNB-COP-01", "flood"): "Trench left open through the monsoon without staged dewatering.",
    ("NNB-COP-01", "forest"): "First submission of the compensatory afforestation plan was incomplete.",
    ("NNB-COP-01", "weld"): "Welder qualification and pre-heat control were below procedure at the start of production.",
    ("NNB-COP-01", "hdd"): "Access road was built on an eroding river bank without protection.",
    ("NNB-COP-01", "pump"): "Test equipment was not reserved against the commissioning schedule.",
    ("NNB-COP-01", "sub"): "Subcontractor mobilisation was not tied to weekly manpower targets.",
    ("AND-ODC-01", "crew"): "Certification roster was not reviewed ahead of the crew change.",
    ("NRL-EXP-01", "rack"): "Late design revision issued after foundation templates were released.",
    ("NRL-EXP-01", "lift"): "Tag line procedure was not covered in the heavy-lift plan.",
}

PROJECTS: List[Project] = [NNB, AND, NRL, SMP]
for _p in PROJECTS:
    for _s in _p.stages:
        build_activities(_s)


# --------------------------------------------------------------------------------------------------------------------
# Derived helpers (used by the seeder and by tests)
# --------------------------------------------------------------------------------------------------------------------
def weight_of(a: Act) -> float:
    """weight_factor stored on the activity: planned duration in weeks (min 1.0)."""
    return max(1.0, round(((a.finish - a.start).days + 1) / 7.0, 1))


def stage_weights(p: Project) -> Dict[str, float]:
    """stage_code -> weight_pct summing to exactly 100.00 (proportional to the sum of activity weights)."""
    raw = {s.code: sum(weight_of(a) for a in s.acts) for s in p.stages}
    total = sum(raw.values())
    out = {k: round(100.0 * v / total, 2) for k, v in raw.items()}
    last = p.stages[-1].code
    out[last] = round(100.0 - sum(v for k, v in out.items() if k != last), 2)
    return out


def rollup(acts: List[Act]) -> float:
    """Weighted mean actual progress, the same weighting the progress engine applies."""
    w = sum(weight_of(a) for a in acts)
    return round(sum(weight_of(a) * a.pct for a in acts) / w, 2) if w else 0.0


def all_acts(p: Project) -> List[Act]:
    return [a for s in p.stages for a in s.acts]


def validate() -> List[str]:
    """Internal-consistency problems of the dataset (empty list == consistent)."""
    problems: List[str] = []
    seen = set()
    for p in PROJECTS:
        if p.lifecycle == "COMPLETED" and any(a.pct != 100 for a in all_acts(p)):
            problems.append(f"{p.code}: COMPLETED project has an activity below 100 percent")
        if p.lifecycle == "UPCOMING" and any(a.pct != 0 for a in all_acts(p)):
            problems.append(f"{p.code}: UPCOMING project has progress")
        if p.lifecycle == "ONGOING":
            vals = [a.pct for a in all_acts(p)]
            if len(set(vals)) < 8:
                problems.append(f"{p.code}: ONGOING progress values are not varied enough")
        if len(p.stages) < 5:
            problems.append(f"{p.code}: needs several stages")
        for s in p.stages:
            if len(s.acts) < 6:
                problems.append(f"{p.code}/{s.code}: needs several activities")
            for a in s.acts:
                if a.code in seen:
                    problems.append(f"duplicate activity code {a.code}")
                seen.add(a.code)
                if a.discipline not in DISCIPLINES:
                    problems.append(f"{a.code}: unknown discipline {a.discipline}")
                if a.start < s.start or a.finish > s.end or a.finish < a.start:
                    problems.append(f"{a.code}: dates outside its stage window")
                if a.pct > 0 and a.start > p.data_date:
                    problems.append(f"{a.code}: progress before planned start")
                if a.pct == 100 and a.finish > p.data_date + timedelta(days=0) and p.lifecycle != "COMPLETED":
                    problems.append(f"{a.code}: 100 percent complete but planned finish {a.finish} is after the data date")
        codes = {a.code for a in all_acts(p)}
        for i in p.issues:
            if i.activity not in codes:
                problems.append(f"{p.code}: issue {i.key} targets unknown activity {i.activity}")
            if i.root_cause and i.root_cause not in p.root_causes:
                problems.append(f"{p.code}: issue {i.key} references unknown root cause {i.root_cause}")
        for c in p.claims:
            if c.activity not in codes:
                problems.append(f"{p.code}: claim {c.key} targets unknown activity {c.activity}")
    return problems


if __name__ == "__main__":
    for p in PROJECTS:
        w = stage_weights(p)
        print(f"\n{p.code}  {p.lifecycle:9}  {p.name}   overall={rollup(all_acts(p))}%")
        for s in p.stages:
            print(f"   {s.code} {rollup(s.acts):6.2f}%  (w {w[s.code]:5.2f})  {s.name}  [{len(s.acts)} activities]")
    errs = validate()
    print("\nVALIDATION:", "OK" if not errs else "")
    for e in errs:
        print("  -", e)
