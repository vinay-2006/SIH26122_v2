import json
from pathlib import Path

from backend.v2.schedule_import.csv_parser import parse_csv
from backend.v2.schedule_import.mapping import RefData, resolve_discipline, resolve_uom, wbs_ancestor_names
from backend.v2.schedule_import.mspdi_parser import parse_mspdi
from backend.v2.schedule_import.xer_parser import parse_xer

F = Path(__file__).parent / "fixtures"
REF = RefData.builtin()


def load(tag: str, fmt: str):
    if fmt == "xer":
        return parse_xer((F / f"{tag}.xer").read_bytes())
    if fmt == "msp":
        return parse_mspdi((F / f"{tag}_mspdi.xml").read_bytes())
    return parse_csv((F / f"{tag}.csv").read_bytes(), (F / f"{tag}_resources.csv").read_bytes())


def expected(tag: str):
    return json.loads((F / f"{tag}_expected.json").read_text())


def wbs_path(ps, code):
    by = {w.code: w for w in ps.wbs}
    names = []
    while code in by:
        names.append(by[code].name)
        code = by[code].parent_code
    return " > ".join(reversed(names))


def normalize(ps):
    """Parser output -> the same shape as the golden (controlled discipline / unit codes resolved with the reference data)."""
    res = {r.code: r for r in ps.resources}
    out = {}
    for a in ps.activities:
        disc, _ = resolve_discipline(a.discipline_label, wbs_ancestor_names(ps, a.wbs_code), REF)
        out[a.external_id] = {
            "name": a.name, "wbs_path": wbs_path(ps, a.wbs_code), "discipline": disc, "type": a.activity_type,
            "start": a.start.isoformat(), "finish": a.finish.isoformat(), "duration_days": a.duration_days, "total_float_days": a.total_float_days,
            "predecessors": sorted([d.predecessor, d.type, d.lag_days] for d in ps.dependencies if d.successor == a.external_id),
            "resources": sorted([x.resource_code, x.qty, resolve_uom(x.uom_label or res[x.resource_code].uom_label, res[x.resource_code].resource_class, REF)]
                                for x in ps.assignments if x.activity_external_id == a.external_id)}
    return out
