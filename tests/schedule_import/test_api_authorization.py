"""Authorization matrix, enforced by the backend (not the UI): every project/schedule/member endpoint against every kind of caller,
plus the Site Engineer's report-only upload route."""
import uuid

import pytest

from v2api import connect, file_bytes, upload
from test_api_schedule_flow import counts

P = "/api/v2/projects"
ZERO = "00000000-0000-0000-0000-000000000000"


def endpoints(w, iid, vid):
    """(label, method, path, kwargs, who may call it)"""
    p = f"{P}/{w.project}"
    csv = {"file": ("s.csv", b"Activity ID,Activity Name,Start,Finish\nA,x,01-03-2026,02-03-2026\n", "text/csv")}
    PM = {"PM"}
    VIEW = {"PM", "SUP", "SE"}
    return [
        ("patch project", "PATCH", p, {"json": {"description": "x"}}, PM),
        ("archive", "POST", f"{p}/archive", {}, PM),
        ("restore", "POST", f"{p}/restore", {}, PM),
        ("get settings", "GET", f"{p}/settings", {}, PM),
        ("patch settings", "PATCH", f"{p}/settings", {"json": {"over_baseline_tolerance_pct": 11}}, PM),
        ("list members", "GET", f"{p}/members", {}, {"PM", "SUP"}),
        ("add member", "POST", f"{p}/members", {"json": {"email": "nobody@test.local", "role": "SITE_ENGINEER"}}, PM),
        ("patch member", "PATCH", f"{p}/members/{ZERO}", {"json": {"status": "SUSPENDED"}}, PM),
        ("remove member", "DELETE", f"{p}/members/{ZERO}", {}, PM),
        ("create invitation", "POST", f"{p}/invitations", {"json": {"email": "x@test.local", "role": "SITE_ENGINEER"}}, PM),
        ("list invitations", "GET", f"{p}/invitations", {}, PM),
        ("revoke invitation", "DELETE", f"{p}/invitations/{ZERO}", {}, PM),
        ("upload schedule", "POST", f"{p}/schedule-imports", {"files": csv, "data": {"data_date": "2026-03-01"}}, PM),
        ("view import", "GET", f"{p}/schedule-imports/{iid}", {}, PM),
        ("set decisions", "PUT", f"{p}/schedule-imports/{iid}/decisions", {"json": {}}, PM),
        ("build import", "POST", f"{p}/schedule-imports/{iid}/build", {}, PM),
        ("discard import", "DELETE", f"{p}/schedule-imports/{iid}", {}, PM),
        ("compare versions", "GET", f"{p}/schedule-versions/compare?old={vid}&new={vid}", {}, PM),
        ("activate version", "POST", f"{p}/schedule-versions/{vid}/activate", {"json": {"reason": "x"}}, PM),
        ("discard version", "DELETE", f"{p}/schedule-versions/{vid}", {}, PM),
        ("get project", "GET", p, {}, VIEW),
        ("list versions", "GET", f"{p}/schedule-versions", {}, VIEW),
        ("get version", "GET", f"{p}/schedule-versions/{vid}", {}, VIEW),
        ("version wbs", "GET", f"{p}/schedule-versions/{vid}/wbs", {}, VIEW),
        ("version activities", "GET", f"{p}/schedule-versions/{vid}/activities", {}, VIEW),
    ]


@pytest.fixture
def ready(world, api):
    r = upload(api, world.pm, world.project, "csv")
    iid = r.json()["import_id"]
    vid = api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm).json()["version_id"]
    return world, iid, vid


def test_unauthorised_callers_are_refused_on_every_endpoint_and_nothing_changes(ready, api):
    w, iid, vid = ready
    callers = {"SE": w.se, "SUP": w.sup, "OUTSIDER": w.outsider, "PM_OTHER_PROJECT": w.pm2, "ANON": None}
    before = counts()
    with connect() as c:
        proj_before = c.execute("select * from projects where project_id = %s", (w.project,)).fetchone()
        mem_before = c.execute("select count(*) n from project_memberships where project_id = %s", (w.project,)).fetchone()["n"]
    for label, method, path, kw, allowed in endpoints(w, iid, vid):
        for who, user in callers.items():
            role = {"SE": "SE", "SUP": "SUP"}.get(who)
            r = api.call(method, path, user, **{k: (dict(v) if isinstance(v, dict) else v) for k, v in kw.items()})
            if who == "ANON":
                assert r.status_code == 401, (label, who, r.status_code)
            elif role in allowed:
                assert r.status_code not in (401, 403), (label, who, r.status_code, r.text)
            else:
                assert r.status_code == 403, f"{who} must be refused on '{label}', got {r.status_code}: {r.text}"
                assert r.json()["error"]["code"] in ("PERMISSION_DENIED", "NOT_A_MEMBER")
    after = counts()
    # only the read-allowed calls ran; the refused write calls changed nothing
    assert after == before
    with connect() as c:
        assert c.execute("select * from projects where project_id = %s", (w.project,)).fetchone() == proj_before
        assert c.execute("select count(*) n from project_memberships where project_id = %s", (w.project,)).fetchone()["n"] == mem_before


def test_the_project_manager_passes_authorization_on_every_endpoint(ready, api):
    w, iid, vid = ready
    for label, method, path, kw, allowed in endpoints(w, iid, vid):
        r = api.call(method, path, w.pm, **{k: (dict(v) if isinstance(v, dict) else v) for k, v in kw.items()})
        assert r.status_code not in (401, 403), (label, r.status_code, r.text)


def test_a_site_engineer_has_no_route_to_any_schedule_or_baseline_operation(ready, api):
    w, iid, vid = ready
    p = f"{P}/{w.project}"
    csv = file_bytes("nsp.csv")
    for method, path, kw in [("POST", f"{p}/schedule-imports", {"files": {"file": ("nsp.csv", csv, "text/csv")}}),
                             ("POST", f"{p}/schedule-imports", {"files": {"file": ("x.xer", file_bytes("nsp.xer"), "text/plain")}}),
                             ("POST", f"{p}/schedule-imports", {"files": {"file": ("x.xml", file_bytes("nsp_mspdi.xml"), "text/xml")}}),
                             ("PUT", f"{p}/schedule-imports/{iid}/decisions", {"json": {"wbs_types": {"X": "STAGE"}}}),
                             ("POST", f"{p}/schedule-imports/{iid}/build", {}), ("POST", f"{p}/schedule-versions/{vid}/activate", {}),
                             ("DELETE", f"{p}/schedule-versions/{vid}", {}), ("PATCH", f"{p}/settings", {"json": {"over_baseline_tolerance_pct": 99}})]:
        r = api.call(method, path, w.se, **kw)
        assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED", (method, path, r.status_code)
    assert api.get(f"{p}/schedule-versions/{vid}", w.se).status_code == 200            # may READ the schedule they report against
    with connect() as c:
        assert c.execute("select status from schedule_versions where version_id = %s", (vid,)).fetchone()["status"] == "VALIDATED"


def test_project_ids_in_the_url_are_not_proof_of_anything(ready, api):
    w, iid, vid = ready
    # the PM of project 2 cannot use project 1's ids on project 2's URL, nor project 1's URL at all
    assert api.get(f"{P}/{w.project2}/schedule-imports/{iid}", w.pm2).status_code == 404
    assert api.get(f"{P}/{w.project}/schedule-imports/{iid}", w.pm2).status_code == 403
    # a header cannot select a project either
    r = api.get(f"{P}/{w.project}/schedule-versions", w.pm2, headers={"X-Project-ID": w.project2})
    assert r.status_code == 403


def test_expired_and_forged_tokens_are_rejected(world, api, monkeypatch):
    import jwt, datetime as dt
    from v2api import SECRET
    now = dt.datetime.now(dt.timezone.utc)
    expired = jwt.encode({"sub": str(world.pm.id), "exp": now - dt.timedelta(hours=1)}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": str(world.pm.id), "exp": now + dt.timedelta(hours=1)}, "a different secret entirely!!!!!!!!!!", algorithm="HS256")
    unsigned = jwt.encode({"sub": str(world.pm.id)}, key="", algorithm="none")
    for tok in (expired, forged, unsigned, "garbage"):
        r = api.c.get(f"{P}/{world.project}", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok[:20]
    ghost = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated", "iat": now, "exp": now + dt.timedelta(hours=1)},
                       SECRET, algorithm="HS256")
    assert api.c.get(f"{P}/{world.project}", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401      # valid token, no profile


def test_a_deactivated_account_is_locked_out(world, api):
    with connect(system=True) as c:
        c.execute("update profiles set is_active = false where id = %s", (world.se.id,))
    r = api.get(f"{P}/{world.project}", world.se)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"


# ---------------------------------------------------------------------------------------------------- site engineer: reports only
def doc(api, user, project, name, body, kind="DAILY_REPORT"):
    return api.post(f"{P}/{project}/documents", user, files={"file": (name, body, "application/octet-stream")}, data={"kind": kind})


def test_site_engineers_can_upload_reports_and_evidence(world, api):
    ok = [("daily.txt", b"3 Mar: 180 m of pipe laid at KM 12+400, 14 welds. Rain after 3 pm.", "DAILY_REPORT"),
          ("site_visit.pdf", b"%PDF-1.7 fake but harmless", "SITE_REPORT"), ("photo.jpg", b"\xff\xd8\xff\xe0" + b"1" * 50, "PHOTO"),
          ("measurements.csv", b"Date,Location,Item,Quantity,Unit\n2026-03-03,KM 12+400,Pipe laid,180,m\n", "EVIDENCE"),
          ("delay.txt", b"Excavator broke down; spare part awaited from Guwahati. Expect 4 days delay.", "ISSUE_REPORT")]
    for name, body, kind in ok:
        r = doc(api, world.se, world.project, name, body, kind)
        assert r.status_code == 201, (name, r.text)
    with connect() as c:
        rows = c.execute("select kind, uploaded_by, extraction_status from source_documents").fetchall()
        assert {r["kind"] for r in rows} == {"DAILY_REPORT", "SITE_REPORT", "PHOTO", "EVIDENCE", "ISSUE_REPORT"}
        assert all(r["uploaded_by"] == w_id for r in rows for w_id in [world.se.id])
    assert doc(api, world.se, world.project, "daily.txt", ok[0][1]).status_code == 409           # same file twice


def test_schedule_files_are_refused_on_the_site_engineer_upload_route(world, api):
    before = counts()
    sched = [("plan.xer", file_bytes("nsp.xer")), ("renamed_notes.txt", file_bytes("nsp.xer")), ("plan.xml", file_bytes("nsp_mspdi.xml")),
             ("schedule.csv", file_bytes("nsp.csv")), ("plan.mpp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100),
             ("progress.csv", b"Activity ID,Activity Name,Baseline Start,Baseline Finish,Predecessors\nA,x,01-03-2026,02-03-2026,\n")]
    for name, body in sched:
        for kind in ("DAILY_REPORT", "EVIDENCE"):
            r = doc(api, world.se, world.project, name, body, kind)
            assert r.status_code == 422 and r.json()["error"]["code"] == "SCHEDULE_FILE_NOT_ALLOWED", (name, kind, r.status_code, r.text)
    # a schedule cannot be smuggled in by claiming the SCHEDULE_FILE kind either
    r = doc(api, world.se, world.project, "plan.csv", file_bytes("nsp.csv"), "SCHEDULE_FILE")
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_KIND"
    assert counts() == before
    with connect() as c:
        assert c.execute("select count(*) n from source_documents").fetchone()["n"] == 0


def test_only_site_engineers_use_the_report_route(world, api):
    for who in (world.pm, world.sup, world.outsider, world.pm2):
        r = doc(api, who, world.project, "daily.txt", b"progress today")
        assert r.status_code == 403, who.name
    assert doc(api, None, world.project, "daily.txt", b"x").status_code == 401
    assert doc(api, world.se, world.project2, "daily.txt", b"progress").status_code == 403       # engineer of project 1 only
    r = doc(api, world.se, world.project, "daily.txt", b"progress", kind="NOT_A_KIND")
    assert r.status_code == 422
    assert doc(api, world.se, world.project, "empty.txt", b"").status_code == 422
    with connect() as c:
        assert c.execute("select count(*) n from source_documents").fetchone()["n"] == 0


def test_the_database_independently_refuses_a_bypass_of_the_api(world):
    """even a buggy backend path cannot write schedule data as a site engineer or without an actor"""
    import psycopg
    with connect() as c:
        c.execute("select set_config('app.actor_id', %s, false)", (str(world.se.id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into schedule_imports (project_id, source_document_id, format, uploaded_by) values (%s,%s,'CSV',%s)", (world.project, uuid.uuid4(), world.se.id))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into source_documents (project_id, kind, file_name, sha256, uploaded_by) values (%s,'SCHEDULE_FILE','x.xer',%s,%s)",
                      (world.project, "a" * 64, world.se.id))
        c.execute("select set_config('app.actor_id', '', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update projects set project_name = 'no actor' where project_id = %s", (world.project,))
