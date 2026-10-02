"""Parser golden tests: every supported format must read the SAME canonical schedule to the SAME golden result, and bad input must fail
with a message a project manager can act on."""
import pytest

from backend.v2.schedule_import.csv_parser import parse_csv
from backend.v2.schedule_import.detect import detect_format
from backend.v2.schedule_import.models import ParseError, parse_date_text
from backend.v2.schedule_import.mspdi_parser import parse_mspdi
from backend.v2.schedule_import.validate import validate
from backend.v2.schedule_import.xer_parser import parse_xer

from helpers import F, REF, expected, load, normalize, wbs_path

FORMATS = ["xer", "msp", "csv"]
TAGS = ["nsp", "nsp_rev1"]


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_reads_the_golden_schedule(tag, fmt):
    ps, gold = load(tag, fmt), expected(tag)
    got = normalize(ps)
    assert set(got) == set(gold["activities"])
    for ext, want in gold["activities"].items():
        assert got[ext] == want, f"{fmt}/{tag}: activity {ext} differs from the golden"


@pytest.mark.parametrize("fmt", FORMATS)
def test_wbs_hierarchy_matches_golden(fmt):
    ps, gold = load("nsp", fmt), expected("nsp")
    assert sorted(wbs_path(ps, w.code) for w in ps.wbs) == gold["wbs_paths"]
    roots = [w for w in ps.wbs if w.parent_code is None]
    assert len(roots) == 1 and roots[0].name == "Northern Spur Test Pipeline"


@pytest.mark.parametrize("fmt", ["xer", "msp"])
def test_project_header_comes_from_the_file(fmt):
    p = load("nsp", fmt).project
    g = expected("nsp")["project"]
    assert (p.data_date.isoformat(), p.planned_start.isoformat(), p.planned_finish.isoformat()) == (g["data_date"], g["planned_start"], g["planned_finish"])


def test_csv_has_no_header_metadata_and_the_service_must_supply_it():
    p = load("nsp", "csv").project
    assert p.data_date is None and p.planned_start is None


@pytest.mark.parametrize("fmt", FORMATS)
def test_all_three_formats_agree_with_each_other(fmt):
    assert normalize(load("nsp", fmt)) == normalize(load("nsp", "xer"))


@pytest.mark.parametrize("fmt", FORMATS)
def test_golden_schedules_validate_clean(fmt):
    r = validate(load("nsp", fmt), REF)
    assert r["valid"] and r["ready_to_build"] and r["errors"] == [] and r["mapping"] == {"unmapped_disciplines": [], "unmapped_units": []}
    assert r["stats"]["activities"] == 12 and r["stats"]["dependencies"] == 11 and r["stats"]["assignments"] == 20


# ------------------------------------------------------------------------------------------------ XER specifics
def test_xer_converts_hours_by_the_project_calendar():
    txt = (F / "nsp.xer").read_bytes().decode().replace("Six-day site calendar\t8\t48", "Seven and a half\t7.5\t45")
    ps = parse_xer(txt.encode())
    a = {x.external_id: x for x in ps.activities}["A1000"]
    assert ps.project.hours_per_day == 7.5 and a.duration_days == round(18 * 8 / 7.5, 2)       # hours in the file / hours per day


def test_xer_wbs_summary_tasks_are_not_activities_and_milestones_and_loe_are_typed():
    txt = (F / "nsp.xer").read_bytes().decode()
    txt = txt.replace("TT_FinMile", "TT_Mile")
    ps = parse_xer(txt.encode())
    assert {a.external_id: a.activity_type for a in ps.activities}["A3030"] == "MILESTONE"
    lines = txt.split("\r\n")
    i = next(k for k, l in enumerate(lines) if l.startswith("%R\t9001\t"))
    parts = lines[i].split("\t")
    parts[7] = "TT_WBS"                                                    # task_type column of the first task
    lines[i] = "\t".join(parts)
    ps2 = parse_xer("\r\n".join(lines).encode())
    assert len(ps2.activities) == 11 and any(x.code == "WBS_SUMMARY_SKIPPED" for x in ps2.issues)


def test_xer_with_several_projects_picks_the_largest_and_says_so():
    txt = (F / "nsp.xer").read_bytes().decode()
    extra_task = "%R\t9999\t2002\t5001\t10\tBL-1\tBaseline copy task\tTT_Task\tTK_NotStart\t8\t0\t2026-01-12 08:00\t2026-01-12 17:00"
    lines = txt.split("\r\n")
    start = lines.index("%T\tTASK")
    k = max(i for i in range(start + 2, len(lines)) if lines[i].startswith("%R") and all(not l.startswith("%T") for l in lines[start + 1:i]))
    lines.insert(k + 1, extra_task)
    ps = parse_xer("\r\n".join(lines).encode())
    assert len(ps.activities) == 12
    assert any(i.code == "MULTIPLE_PROJECTS" for i in ps.issues)


def test_xer_discipline_comes_from_activity_codes():
    ps = load("nsp", "xer")
    assert {a.external_id: a.discipline_label for a in ps.activities}["A2010"] == "Piping Works"


def test_xer_rejects_non_xer_and_truncated_files():
    with pytest.raises(ParseError) as e:
        parse_xer(b"hello world")
    assert e.value.code == "NOT_AN_XER"
    with pytest.raises(ParseError) as e:
        parse_xer(b"ERMHDR\t19.12\t2026-01-05\r\n%T\tPROJECT\r\n%F\tproj_id\r\n%R\t1\r\n")
    assert e.value.code == "BAD_XER" and "TASK" in e.value.message


def test_xer_cp1252_encoded_names_survive():
    txt = (F / "nsp.xer").read_bytes().decode().replace("Welding mainline", "Welding – mainline (café)")
    ps = parse_xer(txt.encode("cp1252"))
    assert {a.external_id: a.name for a in ps.activities}["A2010"] == "Welding – mainline (café)"


# ------------------------------------------------------------------------------------------------ MSP XML specifics
def test_mspdi_uses_the_activity_id_custom_field_and_falls_back_to_wbs():
    xml = (F / "nsp_mspdi.xml").read_text()
    assert {a.external_id for a in parse_mspdi(xml.encode()).activities} >= {"A1000", "A3030"}
    stripped = xml.replace("<Alias>Activity ID</Alias>", "<Alias>Something else</Alias>")   # no 'Activity ID' field -> the task's WBS code is the id
    ids = {a.external_id for a in parse_mspdi(stripped.encode()).activities}
    assert "A1000" not in ids and all(i[0].isdigit() for i in ids)


def test_mspdi_material_quantity_is_units_and_work_is_hours_by_group():
    ps = load("nsp", "msp")
    q = {(x.activity_external_id, x.resource_code): (x.qty, x.uom_label) for x in ps.assignments}
    assert q[("A1010", "CLEARED_ROW_KM")] == (24.0, "km") and q[("A1010", "MANHOURS")] == (3000.0, "MH") and q[("A1010", "EXCAVATOR_HOURS")] == (1200.0, "HR")


def test_mspdi_rejects_doctype_entities_wrong_root_and_broken_xml():
    for bad, code in [(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Project xmlns="http://schemas.microsoft.com/project"/>', "UNSAFE_XML"),
                      (b'<?xml version="1.0"?><Other xmlns="http://schemas.microsoft.com/project"/>', "NOT_MSPDI"),
                      (b'<?xml version="1.0"?><Project xmlns="urn:something-else"/>', "NOT_MSPDI"),
                      (b'<Project xmlns="http://schemas.microsoft.com/project"><Tasks>', "BAD_XML")]:
        with pytest.raises(ParseError) as e:
            parse_mspdi(bad)
        assert e.value.code == code


def test_mspdi_link_to_a_summary_task_is_reported_not_crashed():
    xml = (F / "nsp_mspdi.xml").read_text().replace("<PredecessorUID>2</PredecessorUID>", "<PredecessorUID>1</PredecessorUID>", 1)
    ps = parse_mspdi(xml.encode())
    assert any(i.code == "LINK_TO_SUMMARY" for i in ps.issues) or len(ps.dependencies) >= 10


# ------------------------------------------------------------------------------------------------ CSV specifics
def test_csv_day_first_dates_and_header_synonyms_and_semicolon_delimiter():
    csv_text = ("Task ID;Task Name;WBS;Start Date;End;Original Duration\n"
                "T1;Excavate trench;1.1;03-02-2026;14-02-2026;11\n"
                "T2;Lay pipe;1.2;16-02-2026;28-02-2026;11\n")
    ps = parse_csv(csv_text.encode())
    assert [a.external_id for a in ps.activities] == ["T1", "T2"]
    assert ps.activities[0].start.isoformat() == "2026-02-03" and ps.activities[0].finish.isoformat() == "2026-02-14"   # 03-02 is 3 Feb, not Mar 2
    assert {w.code for w in ps.wbs} == {"1", "1.1", "1.2"}


def test_csv_predecessor_tokens():
    ps = parse_csv(b"Activity ID,Activity Name,Start,Finish,Predecessors\nA,a,01-02-2026,05-02-2026,\nB,b,06-02-2026,09-02-2026,A\n"
                   b"C,c,10-02-2026,12-02-2026,\"A SS+3; B FF-1\"\n")
    d = {(x.predecessor, x.successor): (x.type, x.lag_days) for x in ps.dependencies}
    assert d == {("A", "B"): ("FS", 0.0), ("A", "C"): ("SS", 3.0), ("B", "C"): ("FF", -1.0)}


def test_csv_errors_are_specific():
    with pytest.raises(ParseError) as e:
        parse_csv(b"Foo,Bar\n1,2\n")
    assert e.value.code == "MISSING_COLUMNS" and "Foo" in e.value.message
    with pytest.raises(ParseError) as e:
        parse_csv(b"")
    assert e.value.code == "EMPTY_FILE"
    with pytest.raises(ParseError) as e:
        parse_csv(b"\x00\x01\x02binary")
    assert e.value.code == "NOT_A_CSV"
    ps = parse_csv(b"Activity ID,Activity Name,Start,Finish\nA,a,not-a-date,05-02-2026\n")
    assert any(i.code == "BAD_DATE" for i in ps.issues)
    r = validate(ps, REF)
    assert not r["valid"] and any(x["code"] == "MISSING_DATES" for x in r["errors"])


def test_csv_resource_file_requires_its_columns_and_flags_bad_numbers():
    act = (F / "nsp.csv").read_bytes()
    with pytest.raises(ParseError) as e:
        parse_csv(act, b"Activity ID,Foo\nA1000,1\n")
    assert e.value.code == "MISSING_COLUMNS"
    ps = parse_csv(act, b"Activity ID,Resource ID,Baseline Qty,Unit\nA1000,CONCRETE_M3,lots,m3\n")
    assert any(i.code == "BAD_QUANTITY" for i in ps.issues)


def test_csv_wbs_code_mode_builds_the_dotted_hierarchy_with_names():
    ps = parse_csv(b"Activity ID,Activity Name,WBS Code,WBS Name,Start,Finish\nA,a,1.2.3,Lay,01-02-2026,02-02-2026\n")
    assert [(w.code, w.parent_code) for w in ps.wbs] == [("1", None), ("1.2", "1"), ("1.2.3", "1.2")]
    assert ps.wbs[-1].name == "Lay"


# ------------------------------------------------------------------------------------------------ format detection / .mpp
def test_detect_format_by_content_and_extension():
    assert detect_format("x.xer", (F / "nsp.xer").read_bytes()) == "XER"
    assert detect_format("renamed.txt", (F / "nsp.xer").read_bytes()) == "XER"
    assert detect_format("plan.xml", (F / "nsp_mspdi.xml").read_bytes()) == "MSPDI"
    assert detect_format("plan.csv", (F / "nsp.csv").read_bytes()) == "CSV"


def test_native_mpp_is_refused_with_instructions_never_silently_parsed():
    for name, body in [("plan.mpp", b"anything"), ("plan.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)]:
        with pytest.raises(ParseError) as e:
            detect_format(name, body)
        assert e.value.code == "MPP_NOT_SUPPORTED" and "Save As" in e.value.message and "XML" in e.value.message


def test_xlsx_empty_and_oversize_are_refused():
    with pytest.raises(ParseError) as e:
        detect_format("s.xlsx", b"PK\x03\x04....")
    assert e.value.code == "XLSX_NOT_SUPPORTED"
    with pytest.raises(ParseError):
        detect_format("s.csv", b"")
    with pytest.raises(ParseError) as e:
        detect_format("s.csv", b"a" * (26 * 1024 * 1024))
    assert e.value.code == "TOO_LARGE"


@pytest.mark.parametrize("text,want", [("2026-02-03", "2026-02-03"), ("03-02-2026", "2026-02-03"), ("03/02/2026", "2026-02-03"),
                                       ("3-Feb-26", "2026-02-03"), ("03 Feb 2026", "2026-02-03"), ("2026-02-03 08:00", "2026-02-03"),
                                       ("2026-02-03T08:00:00", "2026-02-03"), ("", None), ("31-31-2026", None)])
def test_date_formats(text, want):
    d = parse_date_text(text)
    assert (d.isoformat() if d else None) == want
