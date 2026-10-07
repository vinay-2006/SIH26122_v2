"""Shared preparation: turn a ParsedSchedule + reference data + PM decisions into resolved records (controlled discipline, unit
codes, resource classes, progress flags). The review screen, reconciliation preview and the build all use this one function so they
cannot disagree."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .mapping import RefData, default_measures_progress, infer_resource_class, resolve_discipline, resolve_uom, wbs_ancestor_names
from .models import ParsedSchedule, PActivity, working_days


@dataclass
class PreparedAssignment:
    resource_code: str
    qty: float
    uom: Optional[str]
    resource_class: str
    measures_progress: bool
    weight: float


@dataclass
class PreparedActivity:
    a: PActivity
    discipline: Optional[str]
    discipline_how: str
    duration: float
    assignments: Dict[str, PreparedAssignment] = field(default_factory=dict)


def prepare(ps: ParsedSchedule, ref: RefData, decisions: Optional[Dict[str, Any]] = None) -> List[PreparedActivity]:
    d = decisions or {}
    disc_ov, uom_ov = d.get("discipline_map") or {}, d.get("uom_map") or {}
    res = {r.code: r for r in ps.resources}
    by_act: Dict[str, List] = {}
    for x in ps.assignments:
        by_act.setdefault(x.activity_external_id, []).append(x)
    out: List[PreparedActivity] = []
    for a in ps.activities:
        code, how = resolve_discipline(a.discipline_label, wbs_ancestor_names(ps, a.wbs_code), ref, disc_ov)
        if a.activity_type == "MILESTONE":
            dur = 0.0
        elif a.duration_days is not None:
            dur = float(a.duration_days)
        elif a.start and a.finish:
            dur = working_days(a.start, a.finish)
        else:
            dur = 0.0
        pa = PreparedActivity(a=a, discipline=code, discipline_how=how, duration=dur)
        for x in by_act.get(a.external_id, []):
            r = res.get(x.resource_code)
            if r is None or x.qty is None:
                continue
            uom = resolve_uom(x.uom_label or r.uom_label, r.resource_class, ref, uom_ov)
            cls = infer_resource_class(r.resource_class, uom, ref)
            meas = default_measures_progress(cls, uom, ref) if x.measures_progress is None else bool(x.measures_progress)
            if cls in ("LABOR", "EQUIPMENT"):
                meas = False
            pa.assignments[x.resource_code] = PreparedAssignment(x.resource_code, float(x.qty), uom, cls, meas, float(x.progress_weight or 1.0))
        out.append(pa)
    return out
