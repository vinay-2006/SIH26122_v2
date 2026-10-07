"""
Canonical free-text -> Discipline enum normalization, shared by every
intake path that has to interpret a human-written or loosely-formatted
discipline label: typed/file claim intake (routers/intake.py), structured
CSV/XLSX progress rows (shared/tabular_extraction.py), and XER WBS node
names (shared/xer_parser.py). Originally lived only in routers/intake.py;
factored out here so the newer intake paths don't reimplement (and
potentially drift from) the same alias table.
"""
from typing import Optional

_DISCIPLINE_ALIASES = {
    "civil": "CIVIL",
    "civil works": "CIVIL",
    "piping": "PIPING",
    "piping works": "PIPING",
    "static/rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "static rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "static and rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "mechanical": "STATIC_ROTATING_EQUIPMENT",
    "mechanical works": "STATIC_ROTATING_EQUIPMENT",
    "electrical": "ELECTRICAL",
    "electrical works": "ELECTRICAL",
    "instrumentation": "INSTRUMENTATION",
    "instrumentation works": "INSTRUMENTATION",
    "hse": "HSE",
    "health, safety and environment": "HSE",
    "health safety environment": "HSE",
    "health safety and environment": "HSE",
    # prototype disciplines (kept in step with the `disciplines` / `discipline_aliases` tables, migration 016)
    "structural": "STRUCTURAL",
    "structural works": "STRUCTURAL",
    "structure": "STRUCTURAL",
    "structures": "STRUCTURAL",
    "steel structure": "STRUCTURAL",
    "process": "PROCESS",
    "commissioning": "PROCESS",
    "process and commissioning": "PROCESS",
    "drilling": "DRILLING",
    "well engineering": "DRILLING",
    "drilling and well engineering": "DRILLING",
    "logistics": "LOGISTICS",
    "marine logistics": "LOGISTICS",
    "materials logistics": "LOGISTICS",
    # short forms used in field reports (previously a private map inside routers/matching.py)
    "mech": "STATIC_ROTATING_EQUIPMENT",
    "elec": "ELECTRICAL",
    "inst": "INSTRUMENTATION",
    "static equipment": "STATIC_ROTATING_EQUIPMENT",
    "rotating equipment": "STATIC_ROTATING_EQUIPMENT",
    "equipment": "STATIC_ROTATING_EQUIPMENT",
    "safety": "HSE",
}


def normalize_discipline(raw: Optional[str]) -> Optional[str]:
    """
    Map a free-text discipline label (any casing/spacing, e.g. "Civil",
    "static/rotating equipment", "Civil Works") to the canonical Discipline
    enum value. Falls back to an uppercased/underscored best-effort guess
    for an unrecognized label (e.g. "Landscaping" -> "LANDSCAPING") rather
    than discarding it -- callers that require a real Discipline enum member
    validate that separately; this function's job is normalization, not
    validation.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    if key in _DISCIPLINE_ALIASES:
        return _DISCIPLINE_ALIASES[key]
    upper = raw.strip().upper().replace(" ", "_").replace("/", "_")
    return upper or None
