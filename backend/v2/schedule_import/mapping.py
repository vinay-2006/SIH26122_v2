"""Reference mapping: disciplines, units of measure, resource classes, WBS node types. Pure (RefData is injected; the service loads it
from the controlled reference tables, tests use RefData.builtin() which mirrors migration 0002)."""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import PWbs, ParsedSchedule

PHYSICAL_DIMENSIONS = {"LENGTH", "AREA", "VOLUME", "MASS", "COUNT", "WELD_JOINT"}


@dataclass
class RefData:
    discipline_codes: set
    discipline_aliases: Dict[str, str]
    uoms: Dict[str, str]                                # code -> dimension

    @classmethod
    def builtin(cls) -> "RefData":
        return cls(
            discipline_codes={"CIVIL", "STRUCTURAL", "PIPING", "STATIC_ROTATING_EQUIPMENT", "ELECTRICAL", "INSTRUMENTATION", "PROCESS",
                              "DRILLING", "LOGISTICS", "HSE", "OTHER"},
            discipline_aliases={
                "civil works": "CIVIL", "civil and structural": "CIVIL", "structural works": "STRUCTURAL", "structure": "STRUCTURAL",
                "steel structure": "STRUCTURAL", "piping works": "PIPING", "pipe": "PIPING", "pipeline": "PIPING",
                "static/rotating equipment": "STATIC_ROTATING_EQUIPMENT", "static rotating equipment": "STATIC_ROTATING_EQUIPMENT",
                "mechanical": "STATIC_ROTATING_EQUIPMENT", "mechanical works": "STATIC_ROTATING_EQUIPMENT", "equipment": "STATIC_ROTATING_EQUIPMENT",
                "electrical works": "ELECTRICAL", "power": "ELECTRICAL", "instrumentation works": "INSTRUMENTATION",
                "instrument": "INSTRUMENTATION", "control systems": "INSTRUMENTATION", "process": "PROCESS", "commissioning": "PROCESS",
                "drilling": "DRILLING", "well engineering": "DRILLING", "logistics": "LOGISTICS", "marine logistics": "LOGISTICS",
                "procurement": "LOGISTICS", "hse": "HSE", "safety": "HSE", "health safety environment": "HSE"},
            uoms={"M": "LENGTH", "KM": "LENGTH", "MM": "LENGTH", "M2": "AREA", "M3": "VOLUME", "KG": "MASS", "TONNE": "MASS",
                  "NOS": "COUNT", "SET": "COUNT", "JOINT": "WELD_JOINT", "MH": "EFFORT", "HR": "MACHINE_TIME", "LS": "LUMPSUM", "PCT": "PERCENT"})


_UOM_ALIASES = {
    "m3": "M3", "cum": "M3", "cu.m": "M3", "cu m": "M3", "cubic metre": "M3", "cubic meter": "M3", "cubic metres": "M3", "m³": "M3",
    "m2": "M2", "sqm": "M2", "sq.m": "M2", "sq m": "M2", "square metre": "M2", "m²": "M2",
    "m": "M", "mtr": "M", "meter": "M", "metre": "M", "metres": "M", "meters": "M", "rm": "M", "rmt": "M",
    "km": "KM", "kilometre": "KM", "kilometer": "KM", "kilometres": "KM", "kilometers": "KM", "mm": "MM",
    "t": "TONNE", "mt": "TONNE", "tonne": "TONNE", "tonnes": "TONNE", "ton": "TONNE", "tons": "TONNE", "metric ton": "TONNE",
    "kg": "KG", "kgs": "KG", "kilogram": "KG",
    "nos": "NOS", "no": "NOS", "no.": "NOS", "number": "NOS", "numbers": "NOS", "each": "NOS", "ea": "NOS", "pcs": "NOS", "unit": "NOS",
    "set": "SET", "sets": "SET", "joint": "JOINT", "joints": "JOINT", "weld joint": "JOINT", "weld joints": "JOINT", "wj": "JOINT",
    "mh": "MH", "man-hour": "MH", "man hour": "MH", "man hours": "MH", "man-hours": "MH", "manhours": "MH",
    "ls": "LS", "lump sum": "LS", "lumpsum": "LS", "%": "PCT", "pct": "PCT", "percent": "PCT",
}
_AMBIGUOUS_HOURS = {"h", "hr", "hrs", "hour", "hours"}


def resolve_uom(label: Optional[str], resource_class: Optional[str], ref: RefData, overrides: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Unit label -> controlled unit code, or None (needs a PM decision)."""
    if label is None or not str(label).strip():
        return {"LABOR": "MH", "EQUIPMENT": "HR"}.get(resource_class or "")
    key = re.sub(r"\s+", " ", str(label).strip().lower())
    if overrides and key in overrides:
        return overrides[key] if overrides[key] in ref.uoms else None
    if key.upper() in ref.uoms:
        return key.upper()
    if key in _AMBIGUOUS_HOURS:
        return "MH" if resource_class == "LABOR" else "HR"
    code = _UOM_ALIASES.get(key)
    return code if code in ref.uoms else None


def infer_resource_class(declared: Optional[str], uom_code: Optional[str], ref: RefData) -> str:
    if declared in ("MATERIAL", "LABOR", "EQUIPMENT", "OTHER"):
        return declared
    dim = ref.uoms.get(uom_code or "")
    return "LABOR" if dim == "EFFORT" else "EQUIPMENT" if dim == "MACHINE_TIME" else "MATERIAL"


def default_measures_progress(resource_class: str, uom_code: Optional[str], ref: RefData) -> bool:
    return resource_class == "MATERIAL" and ref.uoms.get(uom_code or "") in PHYSICAL_DIMENSIONS


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().replace("_", " "))


def wbs_ancestor_names(ps: ParsedSchedule, wbs_code: Optional[str]) -> List[str]:
    by = {w.code: w for w in ps.wbs}
    out, cur, guard = [], wbs_code, 0
    while cur and cur in by and guard < 60:
        out.append(by[cur].name)
        cur, guard = by[cur].parent_code, guard + 1
    return out


def resolve_discipline(label: Optional[str], ancestors: List[str], ref: RefData,
                       overrides: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], str]:
    """-> (code | None, how). `how`: LABEL | ALIAS | WBS | DECISION | UNMAPPED. A PM decision always wins.
    An explicit label that matches nothing is NOT guessed from the WBS: it is reported as unmapped for the PM to decide.
    Only an activity with no discipline at all is classified by a WBS ancestor named for a discipline (e.g. 'Civil Works')."""
    if label and label.strip():
        key = _norm_label(label)
        if overrides and key in overrides:
            return (overrides[key] if overrides[key] in ref.discipline_codes else None), "DECISION"
        if key.upper().replace(" ", "_") in ref.discipline_codes:
            return key.upper().replace(" ", "_"), "LABEL"
        if key in ref.discipline_aliases:
            return ref.discipline_aliases[key], "ALIAS"
        return None, "UNMAPPED"
    for name in ancestors:
        k = _norm_label(name)
        if k.upper().replace(" ", "_") in ref.discipline_codes and k.upper().replace(" ", "_") != "OTHER":
            return k.upper().replace(" ", "_"), "WBS"
        if k in ref.discipline_aliases:
            return ref.discipline_aliases[k], "WBS"
    return None, "UNMAPPED"


def normalize_wbs(ps: ParsedSchedule) -> ParsedSchedule:
    """Guarantee a single root. Several top-level nodes (or none) get a synthetic PROJECT root; activities without a WBS go to an
    'UNASSIGNED' package so every activity has a place in the hierarchy. Returns a copy."""
    out = deepcopy(ps)
    roots = [w for w in out.wbs if w.parent_code is None]
    need_root = len(roots) != 1
    if need_root:
        code = "ROOT"
        while any(w.code == code for w in out.wbs):
            code += "_"
        for w in roots:
            w.parent_code = code
        out.wbs.insert(0, PWbs(code=code, name=out.project.name or "Project", parent_code=None, sequence=0))
        root_code = code
    else:
        root_code = roots[0].code
    if any(a.wbs_code is None for a in out.activities):
        code = "UNASSIGNED"
        while any(w.code == code for w in out.wbs):
            code += "_"
        out.wbs.append(PWbs(code=code, name="Unassigned activities", parent_code=root_code, sequence=len(out.wbs) + 1))
        for a in out.activities:
            if a.wbs_code is None:
                a.wbs_code = code
    return out


def wbs_depths(ps: ParsedSchedule) -> Dict[str, int]:
    by = {w.code: w for w in ps.wbs}
    res: Dict[str, int] = {}

    def depth(c, g=0):
        if c in res:
            return res[c]
        w = by[c]
        d = 0 if w.parent_code is None or w.parent_code not in by or g > 60 else depth(w.parent_code, g + 1) + 1
        res[c] = d
        return d
    for c in by:
        depth(c)
    return res


def propose_wbs_types(ps: ParsedSchedule) -> Dict[str, str]:
    """Root => PROJECT, first level => STAGE, second => AREA, deeper => SUB_ASSET. The PM may override per code."""
    return {c: ("PROJECT" if d == 0 else "STAGE" if d == 1 else "AREA" if d == 2 else "SUB_ASSET") for c, d in wbs_depths(ps).items()}
