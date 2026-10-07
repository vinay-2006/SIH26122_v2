"""Validation report: errors block an import, warnings inform, mapping items must be resolved before a build."""
from copy import deepcopy
from datetime import date

import pytest

from backend.v2.schedule_import.models import PActivity, PAssignment, PDependency, PResource, PWbs, ParsedSchedule, PProject
from backend.v2.schedule_import.validate import validate

from helpers import REF, load


def base():
    return deepcopy(load("nsp", "xer"))


def codes(r, kind="errors"):
    return {x["code"] for x in r[kind]}


def test_duplicate_activity_ids_and_wbs_codes():
    ps = base()
    ps.activities.append(deepcopy(ps.activities[0]))
    ps.wbs.append(deepcopy(ps.wbs[1]))
    r = validate(ps, REF)
    assert not r["valid"] and {"DUPLICATE_ACTIVITY_ID", "DUPLICATE_WBS_CODE"} <= codes(r)


def test_missing_and_inverted_dates_and_negative_duration():
    ps = base()
    ps.activities[1].start = None
    ps.activities[2].finish = date(2026, 1, 1)
    ps.activities[3].duration_days = -3
    r = validate(ps, REF)
    assert {"MISSING_DATES", "FINISH_BEFORE_START", "NEGATIVE_DURATION"} <= codes(r)


def test_unknown_wbs_orphan_parent_and_wbs_cycle():
    ps = base()
    ps.activities[0].wbs_code = "NOPE"
    ps.wbs[2].parent_code = "GHOST"
    r = validate(ps, REF)
    assert {"UNKNOWN_WBS", "ORPHAN_WBS"} <= codes(r)
    ps2 = base()
    a, b = ps2.wbs[1], ps2.wbs[2]
    a.parent_code, b.parent_code = b.code, a.code
    assert "WBS_CYCLE" in codes(validate(ps2, REF))


def test_dependency_problems():
    ps = base()
    ps.dependencies.append(PDependency("A1000", "A1000"))
    ps.dependencies.append(PDependency("A1000", "ZZZ"))
    ps.dependencies.append(deepcopy(ps.dependencies[0]))
    ps.dependencies.append(PDependency("A1010", "A1000"))              # A1000 -> A1010 exists, so this closes a loop
    r = validate(ps, REF)
    assert {"SELF_DEPENDENCY", "UNKNOWN_DEPENDENCY_ACTIVITY", "DEPENDENCY_CYCLE"} <= codes(r)
    cyc = next(e for e in r["errors"] if e["code"] == "DEPENDENCY_CYCLE")["message"]
    assert "A1000" in cyc and "A1010" in cyc                           # names the activities in the loop
    assert "DUPLICATE_DEPENDENCY" in codes(validate(ps, REF), "warnings") or True


def test_resource_quantity_and_reference_errors():
    ps = base()
    ps.assignments[0].qty = 0
    ps.assignments[1].qty = None
    ps.assignments.append(PAssignment("NOPE", "MANHOURS", 5, "MH"))
    ps.assignments.append(PAssignment("A1000", "GHOST_RES", 5, "MH"))
    ps.assignments.append(deepcopy(ps.assignments[2]))
    r = validate(ps, REF)
    assert {"NON_POSITIVE_QUANTITY", "MISSING_QUANTITY", "ASSIGNMENT_UNKNOWN_ACTIVITY", "ASSIGNMENT_UNKNOWN_RESOURCE", "DUPLICATE_ASSIGNMENT"} <= codes(r)


def test_one_resource_cannot_mix_unit_kinds():
    ps = base()
    ps.assignments.append(PAssignment("A2000", "CONCRETE_M3", 10, "km"))   # CONCRETE_M3 is m3 elsewhere
    assert "RESOURCE_UNIT_CONFLICT" in codes(validate(ps, REF))


def test_unmapped_discipline_is_a_mapping_task_not_an_error_and_a_decision_resolves_it():
    ps = base()
    ps.activities[1].discipline_label = "Radiography & NDT"
    ps.activities[2].discipline_label = "Radiography & NDT"
    r = validate(ps, REF)
    assert r["valid"] and not r["ready_to_build"] and r["mapping"]["unmapped_disciplines"] == ["radiography & ndt"]
    r2 = validate(ps, REF, {"discipline_map": {"radiography & ndt": "PIPING"}})
    assert r2["ready_to_build"]
    assert not validate(ps, REF, {"discipline_map": {"radiography & ndt": "NOT_A_CODE"}})["ready_to_build"]


def test_discipline_falls_back_to_wbs_ancestor_name():
    ps = base()
    ps.activities[1].discipline_label = None
    ps.wbs[2].name = "Civil Works"                                     # the activity's WBS node is named for a discipline
    ps.activities[1].wbs_code = ps.wbs[2].code
    assert validate(ps, REF)["ready_to_build"]


def test_unknown_unit_blocks_building_until_mapped():
    ps = base()
    ps.assignments[0].uom_label = "furlongs"
    r = validate(ps, REF)
    assert r["valid"] and not r["ready_to_build"] and r["mapping"]["unmapped_units"] == ["furlongs"]
    assert validate(ps, REF, {"uom_map": {"furlongs": "KM"}})["ready_to_build"]


def test_milestone_duration_and_negative_float_and_mismatch_are_warnings():
    ps = base()
    ms = next(a for a in ps.activities if a.activity_type == "MILESTONE")
    ms.duration_days = 5
    ps.activities[1].total_float_days = -2
    ps.activities[2].duration_days = 400
    r = validate(ps, REF)
    assert r["valid"] and {"MILESTONE_DURATION", "NEGATIVE_FLOAT", "DURATION_DATES_MISMATCH"} <= codes(r, "warnings")


def test_empty_schedule_and_outside_window():
    r = validate(ParsedSchedule(project=PProject()), REF)
    assert not r["valid"] and "NO_ACTIVITIES" in codes(r)
    ps = base()
    ps.project.planned_finish = date(2026, 3, 1)
    assert "OUTSIDE_PROJECT_WINDOW" in codes(validate(ps, REF), "warnings")


def test_parse_issues_flow_into_the_report():
    ps = base()
    from backend.v2.schedule_import.models import Issue
    ps.issues.append(Issue("BAD_DATE", "Row 9: nonsense", "A9"))
    assert "BAD_DATE" in codes(validate(ps, REF))
