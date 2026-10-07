"""DB-free: the generated project knowledge is deterministic, honest about provenance, and states nothing that is not in the records it is given."""
from datetime import date

import pytest

from backend.v2.domain import knowledge as dk
from backend.v2.seed import knowledge_content as kc


def facts(code="NRL-EXPANSION", ptype="Refinery expansion", contract=None):
    return {"project": {"project_code": code, "project_name": "Numaligarh Refinery Expansion", "client_name": "Synthetic Refining Company (demo)", "project_type": ptype,
                        "location": "Golaghat, Assam, India", "latitude": None, "longitude": None, "planned_start": date(2025, 1, 6), "planned_finish": date(2027, 8, 17),
                        "contract_finish": contract, "lifecycle_status": "ONGOING", "description": "Crude distillation unit expansion with a new hydrotreater."},
            "settings": {"working_days_per_week": 6, "over_baseline_tolerance_pct": 10, "completion_threshold_pct": 95, "require_photo_evidence": False},
            "version": {"version_no": 1, "baseline_name": "Baseline", "data_date": None},
            "stages": [{"name": "Site Preparation", "first_start": date(2025, 1, 6), "last_finish": date(2025, 4, 1), "activities": 4}, {"name": "Hydrotreater", "first_start": date(2025, 5, 1), "last_finish": date(2027, 3, 24), "activities": 9}],
            "areas": [], "disciplines": [{"code": "PIPING", "activities": 5}, {"code": "HSE", "activities": 1}],
            "milestones": [{"activity_id": "NRE-4099", "name": "Hydrotreater mechanically complete", "planned_date": date(2027, 3, 24)}],
            "procurement": [{"activity_id": "NRE-4050", "name": "Hydrotreater - process piping fabrication and erection", "planned_start": date(2025, 9, 1), "planned_finish": date(2026, 12, 1)}],
            "activity_total": 31, "members": [{"role": "PROJECT_MANAGER", "full_name": "Anita Bora"}, {"role": "SUPERVISOR", "full_name": "Imran Hussain"}]}


def test_every_project_has_narrative_and_every_section_is_covered():
    e = kc.build_entries(facts())
    assert {x["section"] for x in e} == set(dk.SECTIONS)
    assert set(kc.NARRATIVE) == {"NNB-CRUDE", "AEC-OFFSHORE", "NRL-EXPANSION", "SMP-PIPE"}
    assert len({(x["section"], x["title"].lower()) for x in e}) == len(e)                     # titles are unique within a section (the table's rule)
    assert all(3 <= len(x["title"]) <= 160 and 3 <= len(x["body"]) <= 8000 for x in e)


def test_the_output_is_deterministic():
    assert kc.build_entries(facts()) == kc.build_entries(facts())


def test_provenance_is_honest():
    e = {x["title"]: x for x in kc.build_entries(facts())}
    assert e["Project at a glance"]["provenance"] == "FROM_RECORDS" and "Planned start 2025-01-06, planned finish 2027-08-17." in e["Project at a glance"]["body"]
    assert e["Milestones on record"]["provenance"] == "FROM_RECORDS" and "NRE-4099" in e["Milestones on record"]["body"] and "2027-03-24" in e["Milestones on record"]["body"]
    assert e["Contract value, type and payment terms"]["provenance"] == "NOT_SPECIFIED" and "Not specified" in e["Contract value, type and payment terms"]["body"]
    assert "not specified in the project record" in e["Key dates on record"]["body"]                                     # contract_finish is missing in the record
    assert kc.build_entries(facts(contract=date(2027, 12, 31)))[[x["title"] for x in kc.build_entries(facts())].index("Key dates on record")]["body"].count("2027-12-31") == 1
    assert e["Typical risks"]["provenance"] == "ILLUSTRATIVE" and e["Typical constraints"]["provenance"] == "ILLUSTRATIVE" and e["Vendors and purchase orders"]["provenance"] == "NOT_SPECIFIED"
    assert e["Risk register"]["provenance"] == "NOT_SPECIFIED"
    assert e["Rules on record"]["provenance"] == "FROM_RECORDS" and "Over-baseline tolerance: 10%." in e["Rules on record"]["body"] and "Completion threshold: 95%." in e["Rules on record"]["body"]


def test_no_progress_figure_and_no_invented_value_is_stored():
    import re
    for code in kc.NARRATIVE:
        for x in kc.build_entries(facts(code)):
            if x["provenance"] != "FROM_RECORDS":
                assert not re.search(r"\d+(\.\d+)?\s?%", x["body"]) and not re.search(r"\b(?:Rs|INR|USD|crore|lakh)\b", x["body"], re.I), (code, x["title"])
                assert not re.search(r"\b[A-Z]{2,4}-\d{3,4}\b", x["body"]), (code, x["title"])        # authored text never names an activity id: ids only come from the records


def test_missing_records_are_stated_not_filled_in():
    f = facts()
    f.update(stages=[], disciplines=[], milestones=[], procurement=[], version=None, members=[])
    e = {x["title"]: x for x in kc.build_entries(f)}
    assert e["Milestones on record"]["provenance"] == "NOT_SPECIFIED" and "no milestone" in e["Milestones on record"]["body"]
    assert "Work breakdown by stage" not in e and "Procurement, supply and mobilisation activities" not in e and "Project team" not in e


def test_retrieval_ranks_the_relevant_entry_and_returns_nothing_for_an_unknown_topic():
    es = [dict(x, knowledge_id=i, version=1) for i, x in enumerate(kc.build_entries(facts()))]
    top = dk.rank("What are the long-lead procurement items?", es, k=2)
    assert top and top[0]["section"] == "PROCUREMENT"
    assert dk.rank("What does the glossary say about float?", es, k=1)[0]["section"] == "GLOSSARY"
    assert dk.rank("zzzz qqqq", es) == [] and dk.rank("", es) == []
    c = dk.cite(top[0])
    assert c["kind"] == "PROJECT_KNOWLEDGE" and c["provenance"] in ("FROM_RECORDS", "ILLUSTRATIVE", "NOT_SPECIFIED", "AUTHORED") and c["section_label"] == "Procurement and long-lead items"
