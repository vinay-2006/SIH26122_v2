"""The four demo projects: people, project master data and activity templates. Everything is fictional and synthetic (emails end in
@anvyra.demo). Quantities are plausible engineering orders of magnitude, not real contract figures."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from .schedule_gen import Act, Res, r3, schedule

NAMESPACE = uuid.UUID("5e7a0a11-0000-4000-8000-5e7a0a110001")
DOMAIN = "anvyra.demo"


def user_id(handle: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"user:{handle}")


def project_uuid(code: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"project:{code}")


# handle -> (full name, platform grants)
PEOPLE: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "anita.bora": ("Anita Bora", ("CREATE_PROJECT",)), "rohit.menon": ("Rohit Menon", ("CREATE_PROJECT",)), "farah.khan": ("Farah Khan", ("CREATE_PROJECT",)),
    "kabir.sarma": ("Kabir Sarma", ()), "lakshmi.iyer": ("Lakshmi Iyer", ()), "imran.hussain": ("Imran Hussain", ()), "meera.das": ("Meera Das", ()),
    "debojit.gogoi": ("Debojit Gogoi", ()), "nirmali.saikia": ("Nirmali Saikia", ()), "pranav.rao": ("Pranav Rao", ()),
    "arun.nair": ("Arun Nair", ()), "sneha.pillai": ("Sneha Pillai", ()),
    "tenzin.bhutia": ("Tenzin Bhutia", ()), "ritu.baruah": ("Ritu Baruah", ()), "manoj.kalita": ("Manoj Kalita", ()),
}


def email(handle: str) -> str:
    return f"{handle}@{DOMAIN}"


@dataclass
class ProjectSpec:
    code: str
    name: str
    root: str
    client: str
    ptype: str
    location: str
    lat: float
    lon: float
    lifecycle: str                                  # COMPLETED | ONGOING | UPCOMING
    start: date
    data_date: date
    pm: str
    supervisors: List[str]
    engineers: List[str]
    acts: List[Act] = field(default_factory=list)
    finish: Optional[date] = None
    description: str = ""

    @property
    def project_id(self) -> uuid.UUID:
        return project_uuid(self.code)


def _mh(dur: int, crew: int) -> Res:
    return ("MANHOURS", "Manhours", "Labor", float(max(dur, 1) * crew * 8), "mh")


def _eq(code: str, name: str, dur: int, units: int) -> Res:
    return (code, name, "Equipment", float(max(dur, 1) * units * 8), "hr")


def _mat(code: str, name: str, qty: float, unit: str) -> Res:
    return (code, name, "Material", r3(qty), unit)


# ------------------------------------------------------------------------------------------------ cross-country pipelines (A completed, D upcoming)
def pipeline(prefix: str, spreads: List[Tuple[str, float, str]], stations: List[Tuple[str, str]], hdd: List[Tuple[str, float]], rail: int, road: int, dia_in: int,
             start: date, pre_days: int = 120, mh_everywhere: bool = True) -> List[Act]:
    n = lambda k: f"{prefix}-{k}"
    acts: List[Act] = []
    total_km = sum(s[1] for s in spreads)
    pipe_t_per_km = {12: 25, 16: 38, 18: 48, 24: 78}.get(dia_in, 45)

    def add(*a, **k) -> Act:
        x = Act(*a, **k)
        acts.append(x)
        return x
    add(n(1000), "Environmental clearance and statutory approvals", ("Pre-construction",), "Safety", pre_days, res=[_mh(pre_days, 3)])
    add(n(1010), "Land acquisition and ROW compensation", ("Pre-construction", "Land"), "Civil Works", pre_days + 40, [(n(1000), "SS", 10)],
        [_mat("LAND_PARCELS", "Land parcels settled", float(int(total_km * 28)), "nos"), _mh(pre_days + 40, 6)])
    add(n(1020), "Route survey and alignment sheets", ("Pre-construction", "Survey"), "Civil Works", 55, [(n(1000), "SS", 5)],
        [_mat("ROUTE_SURVEY_KM", "Route surveyed", total_km, "km"), _mh(55, 8)])
    add(n(1030), "Notice to proceed issued", ("Pre-construction",), "Civil Works", 0, [(n(1010), "SS", 60), (n(1020), "FS", 0)])
    add(n(2000), "Line pipe procurement and delivery", ("Procurement and Logistics",), "Procurement", 150, [(n(1030), "FS", 0)],
        [_mat("LINE_PIPE_T", "Line pipe delivered", total_km * pipe_t_per_km, "tonne"), _mh(150, 3)])
    add(n(2010), "Valves, fittings and pig traps delivery", ("Procurement and Logistics",), "Procurement", 120, [(n(1030), "FS", 10)],
        [_mat("VALVES_NOS", "Valves delivered", float(int(total_km / 6) + 12), "nos"), _mh(120, 2)])
    add(n(2020), "Stockyards and coating yard set-up", ("Procurement and Logistics",), "Civil Works", 40, [(n(1030), "FS", 0)], [_mh(40, 14), _eq("YARD_CRANE_HRS", "Yard crane hours", 40, 2)])
    prev_spread: Optional[str] = None
    km_from = 0.0
    for i, (sname, km, loc) in enumerate(spreads, 1):
        base = 3000 + i * 100
        w = (f"Mainline {sname}",)
        gate = [(n(2000), "SS", 30), (n(2020), "FS", 0)] if prev_spread is None else [(prev_spread, "SS", 55), (n(2000), "SS", 30 + 20 * i)]
        d_str, d_weld, d_ndt, d_fjc = max(6, round(km / 1.8)), max(10, round(km / 0.6)), max(10, round(km / 0.65)), max(10, round(km / 0.75))
        d_trn, d_low, d_bf = max(10, round(km / 0.9)), max(8, round(km / 1.1)), max(8, round(km / 1.3))
        joints = float(round(km * 1000 / 12.2))
        lc = f"{loc} (km {km_from:g}-{km_from + km:g})"
        km_from += km
        add(n(base + 10), f"Pipe stringing - {sname}", w, "Piping Works", d_str, gate, [_mat(f"S{i}_PIPE_STRUNG_KM", f"{sname}: pipe strung", km, "km"), _mh(d_str, 22), _eq(f"S{i}_SIDEBOOM_HRS", f"{sname}: sideboom hours", d_str, 4)], lc)
        add(n(base + 20), f"Pipe welding - {sname}", w, "Piping Works", d_weld, [(n(base + 10), "SS", 4)],
            [_mat(f"S{i}_WELD_JOINTS", f"{sname}: weld joints", joints, "joints"), _mat(f"S{i}_PIPE_WELDED_KM", f"{sname}: pipe welded", km, "km"), _mh(d_weld, 34)], lc)
        add(n(base + 30), f"NDT and radiography - {sname}", w, "Piping Works", d_ndt, [(n(base + 20), "SS", 3)],
            [_mat(f"S{i}_RT_JOINTS", f"{sname}: joints radiographed", joints, "joints"), _mh(d_ndt, 8)], lc)
        add(n(base + 40), f"Field joint coating - {sname}", w, "Piping Works", d_fjc, [(n(base + 30), "SS", 3)],
            [_mat(f"S{i}_FJC_JOINTS", f"{sname}: joints coated", joints, "joints"), _mh(d_fjc, 14)], lc)
        add(n(base + 50), f"Trenching - {sname}", w, "Civil Works", d_trn, [(n(base + 10), "SS", 3)],
            [_mat(f"S{i}_TRENCH_KM", f"{sname}: trench excavated", km, "km"), _mat(f"S{i}_EXCAVATION_M3", f"{sname}: excavation", r3(km * 1000 * 2.1), "m3"), _mh(d_trn, 20), _eq(f"S{i}_EXCAV_HRS", f"{sname}: excavator hours", d_trn, 5)], lc)
        add(n(base + 60), f"Lowering-in - {sname}", w, "Piping Works", d_low, [(n(base + 40), "SS", 5), (n(base + 50), "SS", 6)],
            [_mat(f"S{i}_LOWERED_KM", f"{sname}: pipe lowered", km, "km"), _mh(d_low, 26), _eq(f"S{i}_SIDEBOOM2_HRS", f"{sname}: lowering sideboom hours", d_low, 5)], lc)
        add(n(base + 70), f"Backfill and reinstatement - {sname}", w, "Civil Works", d_bf, [(n(base + 60), "SS", 3)],
            [_mat(f"S{i}_BACKFILL_M3", f"{sname}: backfill", r3(km * 1000 * 1.8), "m3"), _mh(d_bf, 18)], lc)
        add(n(base + 80), f"Hydrostatic testing - {sname}", w, "Process", 12, [(n(base + 70), "FS", 0)], [_mat(f"S{i}_TEST_SECTIONS", f"{sname}: test sections passed", float(max(1, round(km / 25))), "nos"), _mh(12, 10)], lc)
        add(n(base + 90), f"{sname} mechanically complete", w, "Piping Works", 0, [(n(base + 80), "FS", 0)])
        prev_spread = n(base + 10)
    last_spread = [a.id for a in acts if a.id.endswith("90")]
    for k, (hname, hlen) in enumerate(hdd, 1):
        b = 4000 + k * 10
        dd = max(25, round(hlen * 28))
        add(n(b), f"HDD crossing - {hname}", ("Special Crossings", hname), "Civil Works", dd, [(n(1030), "FS", 60 + 30 * k), (n(2000), "FS", 0)],
            [_mat(f"HDD{k}_PILOT_M", f"{hname}: pilot hole drilled", hlen * 1000, "m"), _mat(f"HDD{k}_PULLBACK_M", f"{hname}: pipe pulled back", hlen * 1000, "m"), _mh(dd, 24), _eq(f"HDD{k}_RIG_HRS", f"{hname}: HDD rig hours", dd, 2)],
            f"{hname} crossing")
    add(n(4100), "Railway crossings (cased)", ("Special Crossings", "Rail and Road"), "Civil Works", 70, [(n(1030), "FS", 80), (n(2010), "FS", 0)],
        [_mat("RAIL_XINGS", "Railway crossings completed", float(rail), "nos"), _mh(70, 16)])
    add(n(4110), "National and state highway crossings", ("Special Crossings", "Rail and Road"), "Civil Works", 80, [(n(1030), "FS", 90), (n(2010), "FS", 0)],
        [_mat("ROAD_XINGS", "Road crossings completed", float(road), "nos"), _mh(80, 14)])
    for j, (sname, kind) in enumerate(stations, 1):
        b = 5000 + j * 100
        w = (sname,)
        c = max(40, 70)
        add(n(b + 10), f"{sname} - civil works and foundations", w, "Civil Works", 90, [(n(1030), "FS", 100)],
            [_mat(f"ST{j}_CONCRETE_M3", f"{sname}: concrete", 2400.0 + 300 * j, "m3"), _mat(f"ST{j}_REBAR_T", f"{sname}: reinforcement", 210.0 + 20 * j, "tonne"), _mh(90, 30)])
        add(n(b + 20), f"{sname} - {kind} installation", w, "Static Rotating Equipment", 75, [(n(b + 10), "SS", 45)],
            [_mat(f"ST{j}_EQUIP_NOS", f"{sname}: equipment set", float(6 + 2 * j), "nos"), _mat(f"ST{j}_PIPING_JOINTS", f"{sname}: piping joints welded", 420.0 + 60 * j, "joints"), _mh(75, 26)])
        add(n(b + 30), f"{sname} - electrical installation", w, "Electrical", 60, [(n(b + 20), "SS", 30)],
            [_mat(f"ST{j}_CABLE_M", f"{sname}: cable laid", 14000.0 + 1500 * j, "m"), _mh(60, 16)])
        add(n(b + 40), f"{sname} - instrumentation and SCADA", w, "Instrumentation", 55, [(n(b + 30), "SS", 25)],
            [_mat(f"ST{j}_LOOPS", f"{sname}: loops checked", float(90 + 15 * j), "nos"), _mh(55, 12)])
    add(n(6000), "Pre-commissioning and cleaning, gauging and drying", ("Commissioning",), "Process", 30, [(a, "FS", 0) for a in last_spread], [_mh(30, 9)] if mh_everywhere else [])
    add(n(6010), "Pipeline line-fill", ("Commissioning",), "Process", 20, [(n(6000), "FS", 0)] + [(a.id, "FS", 0) for a in acts if a.id.endswith(("40",)) and a.id.startswith(prefix + "-5")],
        [_mat("LINEFILL_M3", "Product volume filled", r3(total_km * 1000 * 0.12), "m3"), _mh(20, 12)])
    add(n(6020), "As-built documentation and handover", ("Commissioning",), "Civil Works", 45, [(n(6000), "SS", 5)], [_mh(45, 4)])
    add(n(6030), "Pipeline commissioned", ("Commissioning",), "Process", 0, [(n(6010), "FS", 0), (n(6020), "FS", 0)])
    return acts


def project_a() -> ProjectSpec:
    p = ProjectSpec("NNB-CRUDE", "Naharkatiya-Noonmati-Barauni Crude Oil Pipeline", "Naharkatiya-Noonmati-Barauni Crude Pipeline", "Synthetic Pipelines Corporation (demo)",
                    "Cross-country crude oil pipeline", "Assam - Bihar, India", 26.30, 91.80, "COMPLETED", date(2023, 3, 1), date(2023, 2, 20), "anita.bora",
                    ["kabir.sarma"], ["debojit.gogoi", "nirmali.saikia", "pranav.rao"],
                    description="Completed 18 inch crude pipeline in four spreads with a trenchless Brahmaputra crossing, one pump station and one terminal.")
    p.acts = pipeline("NNB", [("Spread 1 Naharkatiya-Jorhat", 78.0, "Upper Assam"), ("Spread 2 Jorhat-Guwahati", 112.0, "Central Assam"),
                              ("Spread 3 Guwahati-Siliguri", 124.0, "North Bengal corridor"), ("Spread 4 Siliguri-Barauni", 96.0, "Seemanchal")],
                      [("Noonmati Pump Station", "pump skid and piping"), ("Barauni Receiving Terminal", "tank farm piping and pumps")],
                      [("Brahmaputra", 1.9), ("Kosi River", 1.2)], rail=7, road=11, dia_in=18, start=p.start)
    p.finish = schedule(p.acts, p.start)
    return p


def project_d() -> ProjectSpec:
    p = ProjectSpec("SMP-PIPE", "Siliguri-Mughalsarai Petroleum Products Pipeline", "Siliguri-Mughalsarai Pipeline", "Synthetic Pipelines Corporation (demo)",
                    "Cross-country petroleum products pipeline", "West Bengal - Bihar - Uttar Pradesh, India", 25.60, 86.90, "UPCOMING", date(2027, 2, 1), date(2026, 9, 15), "anita.bora",
                    ["kabir.sarma"], ["pranav.rao", "nirmali.saikia"],
                    description="Upcoming 16 inch products pipeline. Baseline approved and activated; no work has been reported yet.")
    p.acts = pipeline("SMP", [("Spread 1 Siliguri-Purnea", 140.0, "North Bengal - Seemanchal"), ("Spread 2 Purnea-Patna", 206.0, "Bihar plains"), ("Spread 3 Patna-Mughalsarai", 188.0, "Bhojpur - Chandauli")],
                      [("Siliguri Dispatch Terminal", "dispatch pumps and metering"), ("Patna Intermediate Station", "booster pumps and scrapers")],
                      [("Ganga at Patna", 2.4)], rail=9, road=14, dia_in=16, start=p.start, mh_everywhere=False)   # one activity without man-hours: the version falls back to the DURATION weight basis
    p.finish = schedule(p.acts, p.start)
    return p


# ------------------------------------------------------------------------------------------------ B: offshore drilling campaign
def project_b() -> ProjectSpec:
    p = ProjectSpec("AEC-OFFSHORE", "Andaman and East Coast Offshore Drilling Campaign", "Andaman and East Coast Offshore Drilling Campaign", "Synthetic Exploration Ltd (demo)",
                    "Offshore exploration drilling", "Andaman Sea and Bay of Bengal, India", 11.90, 93.20, "ONGOING", date(2025, 6, 2), date(2025, 5, 23), "rohit.menon",
                    ["lakshmi.iyer", "imran.hussain"], ["arun.nair", "sneha.pillai"],
                    description="Three-well exploration campaign with a jack-up rig, marine spread and shore base; wells W1 (Andaman) and W2/W3 (East Coast).")
    acts: List[Act] = []

    def add(*a, **k):
        x = Act(*a, **k)
        acts.append(x)
        return x
    add("OSD-1000", "Contract award and campaign kick-off", ("Mobilisation",), "Procurement", 0)
    add("OSD-1010", "Regulatory approvals, well permits and CRZ clearances", ("Mobilisation", "Approvals"), "Safety", 110, [("OSD-1000", "FS", 0)], [_mh(110, 3)])
    add("OSD-1020", "Shore base and supply base readiness (Port Blair, Kakinada)", ("Mobilisation", "Logistics"), "Procurement", 60, [("OSD-1000", "FS", 0)],
        [_mat("BASE_FITOUT_SETS", "Base facilities fitted out", 6.0, "set"), _mh(60, 10)])
    add("OSD-1030", "Long-lead procurement - casing, wellheads and BOP spares", ("Mobilisation", "Logistics"), "Procurement", 190, [("OSD-1000", "FS", 0)],
        [_mat("CASING_DELIVERED_T", "Casing delivered", 2650.0, "tonne"), _mat("WELLHEADS_NOS", "Wellheads delivered", 3.0, "nos"), _mh(190, 4)])
    add("OSD-1040", "Jack-up rig tow-in and sea-fastening", ("Mobilisation", "Rig"), "Drilling", 40, [("OSD-1010", "FS", 0), ("OSD-1020", "FS", 0), ("OSD-1030", "SS", 120)],
        [_mat("TOW_KM", "Tow distance covered", 1450.0, "km"), _mh(40, 30), _eq("TUG_HRS", "Tug hours", 40, 3)])
    add("OSD-1050", "Rig positioning, preload and jacking on location W1", ("Mobilisation", "Rig"), "Drilling", 8, [("OSD-1040", "FS", 0)], [_mh(8, 34), _eq("JACKING_HRS", "Jacking system hours", 8, 1)])
    add("OSD-1060", "BOP stack function and pressure testing", ("Mobilisation", "HSE and Well Control"), "Safety", 6, [("OSD-1050", "FS", 0)], [_mh(6, 18)])
    add("OSD-1070", "Marine spread: supply vessels and crew-boat mobilisation", ("Mobilisation", "Logistics"), "Procurement", 24, [("OSD-1020", "FS", 0)], [_mh(24, 12), _eq("PSV_HRS", "Supply vessel hours", 24, 2)])
    add("OSD-1080", "Campaign well-control and emergency response drills", ("Mobilisation", "HSE and Well Control"), "Safety", 10, [("OSD-1060", "FS", 0)], [_mh(10, 22)])
    prev = "OSD-1080"
    wells = [("W1", "Andaman", 3350.0, 28), ("W2", "East Coast", 3980.0, 36), ("W3", "East Coast", 3720.0, 34)]
    for wi, (w, basin, depth, dd) in enumerate(wells, 1):
        b = 2000 + wi * 100
        sec = lambda k: f"OSD-{b + k}"
        stage = (f"Well {w} ({basin})",)
        d26, d17, d12, d8 = max(4, round(depth * 0.12 / 70)), max(8, round(depth * 0.28 / 55)), max(14, round(depth * 0.34 / 32)), max(14, round(depth * 0.26 / 22))
        cs = lambda inch, n: max(8, round(n / 12))
        if wi > 1:
            add(sec(0), f"Skid rig to {w} location and preload", stage, "Drilling", 9, [(prev, "FS", 0)], [_mh(9, 30), _eq(f"{w}_JACK_HRS", f"{w}: jacking hours", 9, 1)])
            first = sec(0)
        else:
            first = prev
        add(sec(10), f"{w} - drive conductor and spud", stage, "Drilling", 4, [(first, "FS", 0)], [_mat(f"{w}_CONDUCTOR_M", f"{w}: conductor driven", 110.0, "m"), _mh(4, 34)])
        add(sec(20), f"{w} - drill 26 in hole section", stage, "Drilling", d26, [(sec(10), "FS", 0)],
            [_mat(f"{w}_DRILLED_26_M", f"{w}: 26 in section drilled", r3(depth * 0.12), "m"), _mat(f"{w}_MUD_26_M3", f"{w}: mud volume used 26 in", r3(depth * 0.12 * 0.9), "m3"), _mh(d26, 36), _eq(f"{w}_RIG26_HRS", f"{w}: rig hours 26 in", d26, 1)])
        add(sec(30), f"{w} - run and cement 20 in casing", stage, "Drilling", 6, [(sec(20), "FS", 0)],
            [_mat(f"{w}_CSG20_JOINTS", f"{w}: 20 in casing joints run", float(round(depth * 0.12 / 12)), "nos"), _mat(f"{w}_CEMENT20_T", f"{w}: cement pumped 20 in", 160.0, "tonne"), _mh(6, 38)])
        add(sec(40), f"{w} - drill 17.5 in hole section", stage, "Drilling", d17, [(sec(30), "FS", 0)],
            [_mat(f"{w}_DRILLED_17_M", f"{w}: 17.5 in section drilled", r3(depth * 0.28), "m"), _mat(f"{w}_MUD_17_M3", f"{w}: mud volume used 17.5 in", r3(depth * 0.28 * 0.75), "m3"), _mh(d17, 36), _eq(f"{w}_RIG17_HRS", f"{w}: rig hours 17.5 in", d17, 1)])
        add(sec(50), f"{w} - run and cement 13.375 in casing", stage, "Drilling", 7, [(sec(40), "FS", 0)],
            [_mat(f"{w}_CSG13_JOINTS", f"{w}: 13.375 in casing joints run", float(round(depth * 0.4 / 12)), "nos"), _mat(f"{w}_CEMENT13_T", f"{w}: cement pumped 13.375 in", 210.0, "tonne"), _mh(7, 38)])
        add(sec(60), f"{w} - drill 12.25 in hole section", stage, "Drilling", d12, [(sec(50), "FS", 0)],
            [_mat(f"{w}_DRILLED_12_M", f"{w}: 12.25 in section drilled", r3(depth * 0.34), "m"), _mat(f"{w}_MUD_12_M3", f"{w}: mud volume used 12.25 in", r3(depth * 0.34 * 0.6), "m3"), _mh(d12, 36), _eq(f"{w}_RIG12_HRS", f"{w}: rig hours 12.25 in", d12, 1)])
        add(sec(70), f"{w} - log, run and cement 9.625 in casing", stage, "Drilling", 9, [(sec(60), "FS", 0)],
            [_mat(f"{w}_CSG9_JOINTS", f"{w}: 9.625 in casing joints run", float(round(depth * 0.74 / 12)), "nos"), _mat(f"{w}_CEMENT9_T", f"{w}: cement pumped 9.625 in", 240.0, "tonne"), _mh(9, 38)])
        add(sec(80), f"{w} - drill 8.5 in reservoir section and core", stage, "Drilling", d8, [(sec(70), "FS", 0)],
            [_mat(f"{w}_DRILLED_8_M", f"{w}: 8.5 in section drilled", r3(depth * 0.26), "m"), _mat(f"{w}_MUD_8_M3", f"{w}: mud volume used 8.5 in", r3(depth * 0.26 * 0.5), "m3"), _mh(d8, 36), _eq(f"{w}_RIG8_HRS", f"{w}: rig hours 8.5 in", d8, 1)])
        add(sec(90), f"{w} - open-hole logging and well testing", stage, "Drilling", 14, [(sec(80), "FS", 0)],
            [_mat(f"{w}_TEST_STAGES", f"{w}: test stages completed", 4.0, "nos"), _mh(14, 28)])
        add(sec(95), f"{w} - plug, abandon or suspend and release rig", stage, "Drilling", 6, [(sec(90), "FS", 0)], [_mat(f"{w}_PLUGS", f"{w}: cement plugs set", 3.0, "nos"), _mh(6, 30)])
        dsum = d26 + d17 + d12 + d8 + 60
        add(sec(96), f"{w} - supply vessel runs and material transfers", ("Logistics Support", f"Well {w}"), "Procurement", dsum, [(sec(10), "SS", 0)],
            [_mat(f"{w}_PSV_TRIPS", f"{w}: supply vessel round trips", float(round(dsum / 4)), "nos"), _mh(dsum, 8), _eq(f"{w}_PSV2_HRS", f"{w}: supply vessel hours", dsum, 2)])
        add(sec(97), f"{w} - bulk cement, barite and mud chemical supply", ("Logistics Support", f"Well {w}"), "Procurement", dsum, [(sec(10), "SS", 0)],
            [_mat(f"{w}_BULK_T", f"{w}: bulk materials delivered to rig", r3(depth * 0.55), "tonne"), _mh(dsum, 5)])
        add(sec(98), f"{w} - well-control drills, safety audits and inspections", ("Logistics Support", f"Well {w}"), "Safety", dsum, [(sec(10), "SS", 0)], [_mh(dsum, 6)])
        prev = sec(95)
    add("OSD-9000", "Rig demobilisation and tow-out", ("Demobilisation",), "Drilling", 24, [(prev, "FS", 0)], [_mh(24, 26), _eq("TUG2_HRS", "Tug hours", 24, 3)])
    add("OSD-9010", "Marine spread release and base demobilisation", ("Demobilisation",), "Procurement", 15, [(prev, "SS", 5)], [_mh(15, 10)])
    add("OSD-9020", "Well data package and end-of-campaign report", ("Demobilisation",), "Drilling", 30, [(prev, "FS", 0)], [_mh(30, 6)])
    add("OSD-9030", "Campaign completed", ("Demobilisation",), "Drilling", 0, [("OSD-9000", "FS", 0), ("OSD-9010", "FS", 0), ("OSD-9020", "FS", 0)])
    p.acts = acts
    p.finish = schedule(p.acts, p.start)
    return p


# ------------------------------------------------------------------------------------------------ C: refinery expansion
def project_c() -> ProjectSpec:
    p = ProjectSpec("NRL-EXPANSION", "Numaligarh Refinery Expansion", "Numaligarh Refinery Expansion", "Synthetic Refining Company (demo)",
                    "Refinery expansion", "Golaghat, Assam, India", 26.63, 93.77, "ONGOING", date(2025, 1, 6), date(2024, 12, 20), "farah.khan",
                    ["imran.hussain", "meera.das"], ["tenzin.bhutia", "ritu.baruah", "manoj.kalita"],
                    description="Crude distillation unit expansion with a new hydrotreater, offsites tie-ins and utilities.")
    acts: List[Act] = []

    def add(*a, **k):
        x = Act(*a, **k)
        acts.append(x)
        return x
    add("NRE-1000", "Notice to proceed and mobilisation", ("Site Preparation",), "Civil Works", 20, res=[_mh(20, 25)])
    add("NRE-1010", "Site grading, drainage and roads", ("Site Preparation",), "Civil Works", 70, [("NRE-1000", "FS", 0)],
        [_mat("GRADING_M3", "Earthwork", 86000.0, "m3"), _mat("ROADS_M", "Road length formed", 5400.0, "m"), _mh(70, 40), _eq("EARTH_HRS", "Earthmoving equipment hours", 70, 8)])
    add("NRE-1020", "Temporary facilities, power and water", ("Site Preparation",), "Electrical", 45, [("NRE-1000", "FS", 0)], [_mh(45, 12)])
    units = [("CDU2", "Crude Distillation Unit 2", 1.0), ("HDT", "Hydrotreater", 0.7)]
    for ui, (ucode, uname, sc) in enumerate(units, 1):
        b = 2000 * ui
        K = 2.3
        sc_ = sc
        i = lambda k: f"NRE-{b + k}"
        w = (uname,)
        gate = [("NRE-1010", "SS", 60 + 90 * (ui - 1))]
        add(i(0), f"{uname} - bored piling", w, "Civil Works", round(75 * sc * K), gate, [_mat(f"{ucode}_PILES", f"{uname}: piles completed", float(round(640 * sc * K)), "nos"), _mh(round(75 * sc * K), 30), _eq(f"{ucode}_PILING_RIG_HRS", f"{uname}: piling rig hours", round(75 * sc * K), 4)])
        add(i(10), f"{uname} - pile caps and foundations", w, "Civil Works", round(95 * sc * K), [(i(0), "SS", 50)],
            [_mat(f"{ucode}_CONCRETE_M3", f"{uname}: foundation concrete", r3(9800 * sc), "m3"), _mat(f"{ucode}_REBAR_T", f"{uname}: reinforcement placed", r3(980 * sc), "tonne"), _mh(round(95 * sc * K), 55)])
        add(i(20), f"{uname} - structural steel fabrication and erection", w, "Structural Works", round(130 * sc * K), [(i(10), "SS", 60)],
            [_mat(f"{ucode}_STEEL_T", f"{uname}: structural steel erected", r3(3200 * sc), "tonne"), _mh(round(130 * sc * K), 48), _eq(f"{ucode}_CRANE_HRS", f"{uname}: crane hours", round(130 * sc * K), 3)])
        add(i(30), f"{uname} - columns, vessels and exchangers erection", w, "Static Rotating Equipment", round(110 * sc * K), [(i(10), "SS", 120)],
            [_mat(f"{ucode}_VESSELS_NOS", f"{uname}: vessels and columns set", float(round(18 * sc * K)), "nos"), _mat(f"{ucode}_EXCH_NOS", f"{uname}: exchangers set", float(round(26 * sc * K)), "nos"), _mh(round(110 * sc * K), 36), _eq(f"{ucode}_HEAVYCRANE_HRS", f"{uname}: heavy-lift crane hours", round(110 * sc * K), 2)])
        add(i(40), f"{uname} - pumps and compressors installation", w, "Static Rotating Equipment", round(80 * sc * K), [(i(30), "SS", 50)],
            [_mat(f"{ucode}_PUMPS_NOS", f"{uname}: pumps aligned", float(round(34 * sc * K)), "nos"), _mat(f"{ucode}_COMP_NOS", f"{uname}: compressors set", float(max(2, round(4 * sc * K))), "nos"), _mh(round(80 * sc * K), 28)])
        add(i(50), f"{uname} - process piping fabrication and erection", w, "Piping Works", round(170 * sc * K), [(i(20), "SS", 60)],
            [_mat(f"{ucode}_PIPING_JOINTS", f"{uname}: piping weld joints", float(round(11800 * sc * K)), "joints"), _mat(f"{ucode}_SPOOLS_NOS", f"{uname}: spools erected", float(round(2900 * sc * K)), "nos"), _mh(round(170 * sc * K), 90)])
        add(i(60), f"{uname} - electrical cabling and power distribution", w, "Electrical", round(120 * sc * K), [(i(40), "SS", 40)],
            [_mat(f"{ucode}_CABLE_M", f"{uname}: cable laid", float(round(185000 * sc * K)), "m"), _mat(f"{ucode}_TRAY_M", f"{uname}: cable tray installed", float(round(21000 * sc * K)), "m"), _mat(f"{ucode}_PANELS_NOS", f"{uname}: panels set", float(round(46 * sc * K)), "nos"), _mh(round(120 * sc * K), 42)])
        add(i(70), f"{uname} - instrumentation installation and loop checks", w, "Instrumentation", round(100 * sc * K), [(i(50), "SS", 120)],
            [_mat(f"{ucode}_LOOPS_NOS", f"{uname}: loops checked", float(round(1450 * sc * K)), "nos"), _mat(f"{ucode}_INSTR_CABLE_M", f"{uname}: instrument cable laid", float(round(240000 * sc * K)), "m"), _mh(round(100 * sc * K), 34)])
        add(i(80), f"{uname} - insulation and painting", w, "Piping Works", round(70 * sc * K), [(i(50), "SS", 180)],
            [_mat(f"{ucode}_INSUL_M2", f"{uname}: insulation applied", float(round(38000 * sc * K)), "m2"), _mat(f"{ucode}_PAINT_M2", f"{uname}: painting done", float(round(64000 * sc * K)), "m2"), _mh(round(70 * sc * K), 44)])
        add(i(90), f"{uname} - hydrotest and pre-commissioning", w, "Process", round(45 * sc * K), [(i(50), "FS", 5), (i(70), "FS", 0)],
            [_mat(f"{ucode}_TEST_PACKS", f"{uname}: test packs closed", float(round(210 * sc * K)), "nos"), _mh(round(45 * sc * K), 30)])
        add(i(99), f"{uname} mechanically complete", w, "Process", 0, [(i(90), "FS", 0), (i(60), "FS", 0), (i(80), "FS", 0)])
    add("NRE-7000", "Offsites tie-ins and utilities interconnection", ("Offsites and Utilities",), "Piping Works", 240, [("NRE-2000", "SS", 200)],
        [_mat("TIEIN_NOS", "Tie-ins completed", 38.0, "nos"), _mat("UTIL_PIPING_JOINTS", "Utility piping joints", 3600.0, "joints"), _mh(240, 40)])
    add("NRE-7010", "Flare and effluent treatment upgrades", ("Offsites and Utilities",), "Civil Works", 260, [("NRE-2000", "SS", 240)],
        [_mat("ETP_CONCRETE_M3", "Effluent plant concrete", 2800.0, "m3"), _mat("FLARE_STEEL_T", "Flare structure steel", 420.0, "tonne"), _mh(260, 36)])
    add("NRE-7020", "Control room, DCS and safety systems integration", ("Offsites and Utilities",), "Instrumentation", 200, [("NRE-2070", "SS", 100)], [_mat("DCS_CABINETS", "DCS cabinets installed", 24.0, "nos"), _mh(200, 22)])
    add("NRE-8000", "Plant-wide commissioning and performance test", ("Commissioning",), "Process", 70, [("NRE-2099", "FS", 0), ("NRE-4099", "FS", 0), ("NRE-7000", "FS", 0), ("NRE-7020", "FS", 0)], [_mh(70, 48)])
    add("NRE-8010", "Statutory inspections and safety audit", ("Commissioning",), "Safety", 30, [("NRE-8000", "SS", 20)], [_mh(30, 10)])
    add("NRE-8020", "Expansion handed over to operations", ("Commissioning",), "Process", 0, [("NRE-8000", "FS", 0), ("NRE-8010", "FS", 0)])
    p.acts = acts
    p.finish = schedule(p.acts, p.start)
    return p


def all_projects() -> List[ProjectSpec]:
    return [project_a(), project_b(), project_c(), project_d()]
