"""Validation report for a ParsedSchedule. Errors block the import (nothing is written); warnings are shown; `mapping` lists what the
PM must still resolve before the schedule can be built (unmapped discipline labels, unmapped units)."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import date
from typing import Any, Dict, List, Optional

from .mapping import RefData, default_measures_progress, infer_resource_class, resolve_discipline, resolve_uom, wbs_ancestor_names
from .models import Issue, ParsedSchedule, working_days

MAX_ACTIVITIES = 50_000


def validate(ps: ParsedSchedule, ref: RefData, decisions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    decisions = decisions or {}
    issues: List[Issue] = list(ps.issues)
    E = lambda code, msg, ref_=None: issues.append(Issue(code, msg, ref_, "ERROR"))
    W = lambda code, msg, ref_=None: issues.append(Issue(code, msg, ref_, "WARNING"))

    if not ps.activities:
        E("NO_ACTIVITIES", "The file contains no activities.")
    if len(ps.activities) > MAX_ACTIVITIES:
        E("TOO_LARGE", f"{len(ps.activities)} activities exceeds the {MAX_ACTIVITIES} limit.")

    # ---- WBS structure
    codes = [w.code for w in ps.wbs]
    for c, n in Counter(codes).items():
        if n > 1:
            E("DUPLICATE_WBS_CODE", f"WBS code {c!r} appears {n} times", c)
    wset = set(codes)
    for w in ps.wbs:
        if w.parent_code is not None and w.parent_code not in wset:
            E("ORPHAN_WBS", f"WBS {w.code!r} has an unknown parent {w.parent_code!r}", w.code)
    by = {w.code: w for w in ps.wbs}
    for w in ps.wbs:                                                   # cycles
        seen, cur = set(), w.code
        while cur in by and by[cur].parent_code:
            if cur in seen:
                E("WBS_CYCLE", f"WBS hierarchy loops at {cur!r}", cur)
                break
            seen.add(cur)
            cur = by[cur].parent_code

    # ---- activities
    ids = Counter(a.external_id for a in ps.activities)
    for i, n in ids.items():
        if n > 1:
            E("DUPLICATE_ACTIVITY_ID", f"Activity id {i!r} appears {n} times", i)
    pd = ps.project
    for a in ps.activities:
        ref_ = a.external_id
        if not a.external_id:
            E("MISSING_ACTIVITY_ID", f"An activity named {a.name!r} has no id")
        if not a.name:
            E("MISSING_NAME", f"Activity {ref_} has no name", ref_)
        if a.wbs_code is not None and a.wbs_code not in wset:
            E("UNKNOWN_WBS", f"Activity {ref_} refers to unknown WBS {a.wbs_code!r}", ref_)
        if a.start is None or a.finish is None:
            if a.start is None and a.finish is None:
                E("MISSING_DATES", f"Activity {ref_} has no start and no finish date", ref_)
            elif a.duration_days is None:
                E("MISSING_DATES", f"Activity {ref_} is missing a date and has no duration to derive it", ref_)
            else:
                E("MISSING_DATES", f"Activity {ref_} is missing its {'start' if a.start is None else 'finish'} date", ref_)
            continue
        if a.finish < a.start:
            E("FINISH_BEFORE_START", f"Activity {ref_} finishes ({a.finish}) before it starts ({a.start})", ref_)
        if a.duration_days is not None and a.duration_days < 0:
            E("NEGATIVE_DURATION", f"Activity {ref_} has a negative duration", ref_)
        if a.activity_type == "MILESTONE" and (a.duration_days or 0) > 0:
            W("MILESTONE_DURATION", f"Milestone {ref_} has a duration of {a.duration_days}; it will be stored as 0", ref_)
        elif a.duration_days is not None and a.activity_type != "MILESTONE" and a.finish >= a.start:
            derived = working_days(a.start, a.finish)
            if abs(derived - a.duration_days) > max(2.0, 0.25 * derived):
                W("DURATION_DATES_MISMATCH", f"Activity {ref_}: duration {a.duration_days} d differs from the {derived:g} working days between its dates", ref_)
        if a.duration_days is None and a.activity_type != "MILESTONE":
            W("DURATION_DERIVED", f"Activity {ref_} has no duration; derived from its dates", ref_)
        if a.total_float_days is not None and a.total_float_days < 0:
            W("NEGATIVE_FLOAT", f"Activity {ref_} has negative float ({a.total_float_days} d): the plan is behind its own constraints", ref_)
        if pd.planned_start and a.start < pd.planned_start or pd.planned_finish and a.finish > pd.planned_finish:
            W("OUTSIDE_PROJECT_WINDOW", f"Activity {ref_} lies outside the project's planned window", ref_)

    # ---- dependencies
    aset = set(ids)
    seen_dep, edges = set(), defaultdict(list)
    for d in ps.dependencies:
        if d.predecessor not in aset or d.successor not in aset:
            E("UNKNOWN_DEPENDENCY_ACTIVITY", f"Relationship {d.predecessor} -> {d.successor} refers to an unknown activity", d.successor)
            continue
        if d.predecessor == d.successor:
            E("SELF_DEPENDENCY", f"Activity {d.successor} depends on itself", d.successor)
            continue
        key = (d.predecessor, d.successor, d.type)
        if key in seen_dep:
            W("DUPLICATE_DEPENDENCY", f"Duplicate relationship {d.predecessor} -> {d.successor} ({d.type}) ignored", d.successor)
            continue
        seen_dep.add(key)
        edges[d.predecessor].append(d.successor)
    cyc = _cycle(aset, edges)
    if cyc:
        E("DEPENDENCY_CYCLE", "The logic network has a cycle: " + " -> ".join(cyc + [cyc[0]]), cyc[0])

    # ---- resources and units
    rmap = {r.code: r for r in ps.resources}
    uom_ov = decisions.get("uom_map") or {}
    unmapped_units: set = set()
    dims: Dict[str, set] = defaultdict(set)
    for r in ps.resources:
        if not r.code:
            E("MISSING_RESOURCE_CODE", "A resource has no code")
    per_activity = Counter()
    for x in ps.assignments:
        r = rmap.get(x.resource_code)
        if x.activity_external_id not in aset:
            E("ASSIGNMENT_UNKNOWN_ACTIVITY", f"Resource assignment refers to unknown activity {x.activity_external_id!r}", x.activity_external_id)
            continue
        if r is None:
            E("ASSIGNMENT_UNKNOWN_RESOURCE", f"Activity {x.activity_external_id} uses undefined resource {x.resource_code!r}", x.activity_external_id)
            continue
        per_activity[(x.activity_external_id, x.resource_code)] += 1
        if x.qty is None:
            E("MISSING_QUANTITY", f"{x.activity_external_id} / {x.resource_code}: quantity is missing", x.activity_external_id)
        elif x.qty <= 0:
            E("NON_POSITIVE_QUANTITY", f"{x.activity_external_id} / {x.resource_code}: quantity must be greater than 0 (got {x.qty:g})", x.activity_external_id)
        label = x.uom_label or r.uom_label
        code = resolve_uom(label, r.resource_class, ref, uom_ov)
        if code is None:
            unmapped_units.add((label or "").strip().lower() or "(blank)")
        else:
            dims[x.resource_code].add(ref.uoms[code])
            cls = infer_resource_class(r.resource_class, code, ref)
            if x.measures_progress and cls in ("LABOR", "EQUIPMENT"):
                W("MEASURES_PROGRESS_IGNORED", f"{x.activity_external_id} / {x.resource_code}: a {cls.lower()} resource cannot drive physical progress; ignored", x.activity_external_id)
    for rc, ds in dims.items():
        if len(ds) > 1:
            E("RESOURCE_UNIT_CONFLICT", f"Resource {rc} is used with units of different kinds ({', '.join(sorted(ds))}); one resource has one kind of unit", rc)
    for (a, r), n in per_activity.items():
        if n > 1:
            E("DUPLICATE_ASSIGNMENT", f"Activity {a} lists resource {r} {n} times", a)
    for a in ps.activities:
        if a.activity_type == "TASK" and not any(x.activity_external_id == a.external_id for x in ps.assignments):
            pass                                                           # activities without quantities are legitimate (approvals, etc.)

    # ---- discipline mapping
    disc_ov = decisions.get("discipline_map") or {}
    unmapped_disc: Dict[str, int] = defaultdict(int)
    how = Counter()
    for a in ps.activities:
        code, h = resolve_discipline(a.discipline_label, wbs_ancestor_names(ps, a.wbs_code), ref, disc_ov)
        how[h] += 1
        if code is None:
            unmapped_disc[(a.discipline_label or "").strip().lower() or "(blank)"] += 1
    for lbl, n in unmapped_disc.items():
        W("UNMAPPED_DISCIPLINE", f"Discipline {lbl!r} ({n} activities) matches no controlled discipline; map it before building", lbl)
    for u in sorted(unmapped_units):
        E("UNMAPPED_UNIT", f"Unit {u!r} is not a controlled unit of measure; map it (e.g. to M3, KM, TONNE) before building", u)

    errors = [asdict(i) for i in issues if i.severity == "ERROR" and i.code != "UNMAPPED_UNIT"]
    warnings = [asdict(i) for i in issues if i.severity == "WARNING"]
    info = [asdict(i) for i in issues if i.severity == "INFO"]
    mapping = {"unmapped_disciplines": sorted(unmapped_disc), "unmapped_units": sorted(unmapped_units)}
    return {
        "valid": not errors, "errors": errors, "warnings": warnings, "info": info, "mapping": mapping,
        "ready_to_build": not errors and not mapping["unmapped_disciplines"] and not mapping["unmapped_units"],
        "stats": {"activities": len(ps.activities), "wbs_nodes": len(ps.wbs), "dependencies": len(seen_dep),
                  "resources": len(ps.resources), "assignments": len(ps.assignments), "discipline_resolution": dict(how)},
    }


def _cycle(nodes, edges) -> Optional[List[str]]:
    indeg = {n: 0 for n in nodes}
    for a, outs in edges.items():
        for b in outs:
            indeg[b] += 1
    q = deque(n for n, d in indeg.items() if d == 0)
    done = 0
    while q:
        n = q.popleft()
        done += 1
        for m in edges.get(n, ()):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if done == len(nodes):
        return None
    remaining = {n for n, d in indeg.items() if d > 0}       # on a cycle, or merely downstream of one
    # prune nodes with no successor inside the set until only nodes that can reach a cycle again remain; walking forward then finds a real cycle
    out = {n: [m for m in edges.get(n, ()) if m in remaining] for n in remaining}
    changed = True
    while changed:
        changed = False
        for n in list(remaining):
            if not [m for m in out[n] if m in remaining]:
                remaining.discard(n)
                changed = True
    cur, seen, path = sorted(remaining)[0], {}, []
    while cur not in seen:
        seen[cur] = len(path)
        path.append(cur)
        cur = next(m for m in out[cur] if m in remaining)
    return path[seen[cur]:]
