"""Project knowledge over HTTP: every member reads, only the Project Manager writes, changes are versioned and audited, nothing is deleted."""
from apikit import url
from v2api import connect


def body(**kw):
    d = {"section": "SCOPE", "title": "Pipeline spread", "body": "Authored description of the work.", "provenance": "AUTHORED", "tags": ["pipeline"], "sort_order": 10}
    d.update(kw)
    return d


def test_the_project_manager_writes_and_every_member_reads(kit, api):
    r = api.post(url(kit, "/knowledge"), kit.world.pm, json=body())
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["version"] == 1 and e["status"] == "ACTIVE" and e["created_by"] == str(kit.world.pm.id)
    for who in (kit.world.pm, kit.world.sup, kit.world.se):
        got = api.get(url(kit, "/knowledge"), who)
        assert got.status_code == 200 and [x["title"] for x in got.json()["items"]] == ["Pipeline spread"], who.name
        assert got.json()["sections"][0]["section"] == "OVERVIEW"
    assert api.get(url(kit, "/knowledge"), kit.world.outsider).status_code == 403


def test_nobody_but_the_project_manager_can_write(kit, api):
    for who in (kit.world.sup, kit.world.se, kit.world.outsider):
        assert api.post(url(kit, "/knowledge"), who, json=body()).status_code == 403, who.name
    e = api.post(url(kit, "/knowledge"), kit.world.pm, json=body()).json()
    for who in (kit.world.sup, kit.world.se):
        assert api.put(url(kit, f"/knowledge/{e['knowledge_id']}"), who, json=body(body="changed")).status_code == 403
        assert api.post(url(kit, f"/knowledge/{e['knowledge_id']}/retire"), who).status_code == 403
    assert api.get(url(kit, "/knowledge"), kit.world.se).json()["items"][0]["body"] == "Authored description of the work."


def test_an_edit_advances_the_version_and_is_audited_and_retiring_keeps_the_row(kit, api):
    e = api.post(url(kit, "/knowledge"), kit.world.pm, json=body()).json()
    u = api.put(url(kit, f"/knowledge/{e['knowledge_id']}"), kit.world.pm, json=body(body="Revised description.", provenance="ILLUSTRATIVE"))
    assert u.status_code == 200 and u.json()["version"] == 2 and u.json()["provenance"] == "ILLUSTRATIVE" and u.json()["updated_by"] == str(kit.world.pm.id)
    r = api.post(url(kit, f"/knowledge/{e['knowledge_id']}/retire"), kit.world.pm)
    assert r.status_code == 200 and r.json()["status"] == "RETIRED" and r.json()["version"] == 3
    assert api.get(url(kit, "/knowledge"), kit.world.se).json()["items"] == []                                  # retired entries are no longer used
    assert [x["status"] for x in api.get(url(kit, "/knowledge?include_retired=true"), kit.world.pm).json()["items"]] == ["RETIRED"]
    assert api.get(url(kit, "/knowledge?include_retired=true"), kit.world.se).json()["items"] == []            # only the Project Manager sees retired entries
    assert api.put(url(kit, f"/knowledge/{e['knowledge_id']}"), kit.world.pm, json=body()).status_code == 409   # a retired entry is not edited
    with connect() as c:
        assert [x["action"] for x in c.execute("select action from audit_logs where entity_type = 'PROJECT_KNOWLEDGE' order by log_id").fetchall()] == ["KNOWLEDGE_CREATED", "KNOWLEDGE_UPDATED", "KNOWLEDGE_RETIRED"]
        assert c.execute("select count(*) n from project_knowledge").fetchone()["n"] == 1                       # never deleted


def test_validation_and_duplicates(kit, api):
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body(section="NOPE")).status_code == 422
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body(provenance="FROM_RECORDS")).status_code == 422          # generated content is not typed in
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body(title="ab")).status_code == 422
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body(**{"extra": 1})).status_code == 422
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body()).status_code == 201
    assert api.post(url(kit, "/knowledge"), kit.world.pm, json=body(title="PIPELINE SPREAD")).status_code == 409


def test_knowledge_is_isolated_between_projects(kit, api):
    e = api.post(url(kit, "/knowledge"), kit.world.pm, json=body()).json()
    other = f"/api/v2/projects/{kit.world.project2}/knowledge"
    assert api.get(other, kit.world.pm).status_code in (403, 404)
    assert api.put(f"{other}/{e['knowledge_id']}", kit.world.pm2, json=body()).status_code in (403, 404)


def test_the_database_refuses_writes_by_anyone_but_a_project_manager_even_around_the_api(kit):
    import psycopg, pytest
    with connect() as c:
        with pytest.raises(psycopg.Error):
            with c.transaction():
                c.execute("select set_config('app.actor_id', %s, true)", (str(kit.world.sup.id),))
                c.execute("insert into project_knowledge (project_id, section, title, body, provenance, created_by, updated_by) values (%s,'SCOPE','Sneaky entry','x x x','AUTHORED',%s,%s)",
                          (kit.project, kit.world.sup.id, kit.world.sup.id))
