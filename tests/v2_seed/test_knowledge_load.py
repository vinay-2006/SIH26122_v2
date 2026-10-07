"""The generated project knowledge, loaded into the seeded four-project database: complete, idempotent, faithful to the records, and never overwriting a person's edit."""
import pytest

from seedkit import person
from v2api import connect

pytestmark = pytest.mark.db_write
CODES = ("NNB-CRUDE", "AEC-OFFSHORE", "NRL-EXPANSION", "SMP-PIPE")


def pid(code):
    from backend.v2.seed.projects_spec import project_uuid
    return project_uuid(code)


def url(code, tail):
    return f"/api/v2/projects/{pid(code)}{tail}"


def load():
    import psycopg
    import psycopg.rows
    from backend.v2.seed import knowledge_load
    import os
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        return knowledge_load.load_all(c)


def test_every_project_gets_every_section_and_a_second_load_changes_nothing(seeded):
    first = load()
    assert set(first) == set(CODES) and all(v["inserted"] > 30 and v["kept_edited"] == 0 for v in first.values())
    again = load()
    assert all(v["inserted"] == 0 and v["refreshed"] == 0 and v["unchanged"] > 30 for v in again.values())
    with connect() as c:
        for code in CODES:
            secs = {r["section"] for r in c.execute("select section from project_knowledge where project_id = %s and status = 'ACTIVE'", (pid(code),)).fetchall()}
            assert len(secs) == 12, (code, secs)
        assert c.execute("select count(*) n from audit_logs where entity_type = 'PROJECT_KNOWLEDGE' and action = 'KNOWLEDGE_CREATED'").fetchone()["n"] == sum(v["inserted"] for v in first.values())
        assert seeded.runner.verify()["audit_chain"]["valid"]                                                     # the loader's audit entries keep the chain valid


def test_records_derived_entries_match_the_database_exactly(seeded):
    load()
    with connect() as c:
        for code in CODES:
            p = pid(code)
            n = c.execute("select count(*) n from baseline_activities ba join schedule_versions v on v.version_id = ba.version_id where v.project_id = %s and v.status = 'ACTIVE'", (p,)).fetchone()["n"]
            glance = c.execute("select body from project_knowledge where project_id = %s and title = 'Project at a glance'", (p,)).fetchone()["body"]
            assert f"has {n} activities" in glance
            ms = c.execute("select external_activity_id from baseline_activities ba join schedule_versions v on v.version_id = ba.version_id where v.project_id = %s and v.status = 'ACTIVE' and activity_type = 'MILESTONE'", (p,)).fetchall()
            body = c.execute("select body from project_knowledge where project_id = %s and title = 'Milestones on record'", (p,)).fetchone()["body"]
            assert all(m["external_activity_id"] in body for m in ms)
        upcoming = c.execute("select body from project_knowledge k join projects pr using(project_id) where pr.project_code = 'SMP-PIPE' and title = 'Purpose and context'").fetchone()["body"]
        assert "no work has been reported yet" in upcoming


def test_a_project_managers_edit_is_never_overwritten_but_changed_records_refresh_generated_entries(seeded, http):
    load()
    pm = person("anita.bora")
    items = http.get(url("NNB-CRUDE", "/knowledge"), pm).json()["items"]
    edited = next(i for i in items if i["title"] == "Purpose and context")
    r = http.put(url("NNB-CRUDE", f"/knowledge/{edited['knowledge_id']}"), pm, json={"section": "OVERVIEW", "title": "Purpose and context", "body": "Edited by the project manager.", "provenance": "AUTHORED", "tags": [], "sort_order": 20})
    assert r.status_code == 200
    # the project's records change: the over-baseline tolerance
    assert http.patch(f"/api/v2/projects/{pid('NNB-CRUDE')}/settings", pm, json={"over_baseline_tolerance_pct": 12}).status_code == 200
    out = load()["NNB-CRUDE"]
    assert out["refreshed"] >= 1 and out["inserted"] == 0
    after = {i["title"]: i for i in http.get(url("NNB-CRUDE", "/knowledge"), pm).json()["items"]}
    assert after["Purpose and context"]["body"] == "Edited by the project manager." and after["Purpose and context"]["version"] == 2
    assert "Over-baseline tolerance: 12%." in after["Rules on record"]["body"] and after["Rules on record"]["version"] == 2


def test_the_loader_refuses_a_hosted_target(seeded, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("loadk", Path(__file__).resolve().parents[2] / "scripts" / "load_project_knowledge.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    monkeypatch.setenv("DB_V2_URL", "postgresql://postgres.abcdefghijklmnopqrst:pw@aws-0-x.pooler.supabase.com:5432/postgres?sslmode=require")
    monkeypatch.setenv("V2_ALLOW_HOSTED", "1"); monkeypatch.setenv("V2_ALLOWED_PROJECT_REFS", "abcdefghijklmnopqrst"); monkeypatch.setenv("V2_DENIED_PROJECT_REFS", "none")
    assert m.main([]) == 1 and "LOCAL" in capsys.readouterr().err
