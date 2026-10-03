"""Fixtures for the schedule-import API tests. These COMMIT (the service opens its own connections), so the isolated test database is
emptied of all non-reference data before and after each test. Refuses to run against anything but a setuai_v2_* database."""
from __future__ import annotations

import datetime as dt
import os
import uuid
from types import SimpleNamespace

import jwt
import psycopg
import psycopg.rows
import pytest

SECRET = "unit-test-signing-secret-not-a-credential-0123456789"
KEEP = {"disciplines", "discipline_aliases", "units_of_measure", "issue_categories", "schema_migrations", "_setuai_env"}


def connect(system: bool = False):
    c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=psycopg.rows.dict_row)
    if system:
        c.execute("select set_config('app.system','on',false)")       # test SETUP only; the API code under test never does this
    return c


def _truncate():
    with connect() as c:
        assert c.execute("select current_database() d").fetchone()["d"].startswith("setuai_v2_"), "refusing to truncate a non-v2 database"
        names = [r["tablename"] for r in c.execute("select tablename from pg_tables where schemaname = 'public'").fetchall() if r["tablename"] not in KEEP]
        c.execute("truncate " + ", ".join(f"public.{n}" for n in names) + ", auth.users restart identity cascade")


@pytest.fixture
def clean_db(monkeypatch, tmp_path):
    monkeypatch.setenv("V2_EVIDENCE_DIR", str(tmp_path / "evidence"))        # uploaded test files never land in the working tree
    from backend.v2 import storage
    storage.set_store(None)
    monkeypatch.setenv("DB_V2_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    for k in ("SUPABASE_URL", "V2_JWT_ISSUER", "V2_JWKS_URL", "SUPABASE_JWKS_URL", "V2_ALLOW_HOSTED"):
        monkeypatch.delenv(k, raising=False)
    from backend.v2 import jwt_verify
    jwt_verify.reset_verifier()
    _truncate()
    yield
    from backend.v2 import db as v2db, jwt_verify
    v2db.close_pool()                                               # no pooled connection may outlive the truncate
    jwt_verify.reset_verifier()
    storage.set_store(None)
    _truncate()


@pytest.fixture
def client(clean_db):
    from fastapi.testclient import TestClient
    from backend.v2.app import create_app
    return TestClient(create_app())


@pytest.fixture
def client_500(clean_db):
    from fastapi.testclient import TestClient
    from backend.v2.app import create_app
    return TestClient(create_app(), raise_server_exceptions=False)


def make_user(name: str, grants=()):
    uid = uuid.uuid4()
    email = f"{name}@test.local"
    with connect(system=True) as c:
        c.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s,%s,%s::jsonb)", (uid, email, f'{{"full_name":"{name.title()}"}}'))
        for g in grants:
            c.execute("insert into platform_grants (user_id, capability) values (%s,%s)", (uid, g))
    return SimpleNamespace(id=uid, email=email, name=name)


def token(user) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode({"sub": str(user.id), "email": user.email, "aud": "authenticated", "role": "authenticated", "iat": now,
                       "exp": now + dt.timedelta(hours=1)}, SECRET, algorithm="HS256")


class Api:
    def __init__(self, client):
        self.c = client

    def call(self, method, path, user=None, **kw):
        h = dict(kw.pop("headers", {}))
        if user is not None:
            h["Authorization"] = f"Bearer {token(user)}"
        return self.c.request(method, path, headers=h, **kw)

    def get(self, path, user=None, **kw): return self.call("GET", path, user, **kw)
    def post(self, path, user=None, **kw): return self.call("POST", path, user, **kw)
    def put(self, path, user=None, **kw): return self.call("PUT", path, user, **kw)
    def patch(self, path, user=None, **kw): return self.call("PATCH", path, user, **kw)
    def delete(self, path, user=None, **kw): return self.call("DELETE", path, user, **kw)


@pytest.fixture
def api(client):
    return Api(client)


@pytest.fixture
def world(api):
    """admin (PLATFORM_ADMIN), pm (CREATE_PROJECT, creates the project through the API), supervisor + site engineer (added through the API),
    pm2 (PM of another project), outsider (member of nothing)."""
    w = SimpleNamespace()
    w.admin = make_user("admin", ["PLATFORM_ADMIN"])
    w.pm = make_user("pm", ["CREATE_PROJECT"])
    w.pm2 = make_user("pm2", ["CREATE_PROJECT"])
    w.sup, w.se, w.se2, w.outsider = (make_user(n) for n in ("sup", "se", "se2", "outsider"))
    r = api.post("/api/v2/projects", w.pm, json={"project_code": "NSP-TEST", "project_name": "Northern Spur Test Pipeline", "location": "Assam"})
    assert r.status_code == 201, r.text
    w.project = r.json()["project_id"]
    r2 = api.post("/api/v2/projects", w.pm2, json={"project_code": "OTHER-1", "project_name": "Someone else's project"})
    assert r2.status_code == 201, r2.text
    w.project2 = r2.json()["project_id"]
    for u, role in ((w.sup, "SUPERVISOR"), (w.se, "SITE_ENGINEER"), (w.se2, "SITE_ENGINEER")):
        rr = api.post(f"/api/v2/projects/{w.project}/members", w.pm, json={"email": u.email, "role": role})
        assert rr.status_code == 201, rr.text
    return w


def file_bytes(name: str) -> bytes:
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / name).read_bytes()


def upload(api, user, project, fmt="csv", tag="nsp", **data):
    """upload a fixture schedule as the given user"""
    if fmt == "csv":
        files = {"file": (f"{tag}.csv", file_bytes(f"{tag}.csv"), "text/csv"),
                 "resources_file": (f"{tag}_resources.csv", file_bytes(f"{tag}_resources.csv"), "text/csv")}
        data = {"data_date": "2026-01-05", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "project_name": "Northern Spur Test Pipeline", **data}
    elif fmt == "xer":
        files = {"file": (f"{tag}.xer", file_bytes(f"{tag}.xer"), "application/octet-stream")}
    else:
        files = {"file": (f"{tag}.xml", file_bytes(f"{tag}_mspdi.xml"), "application/xml")}
    return api.post(f"/api/v2/projects/{project}/schedule-imports", user, files=files, data=data)


def build_and_activate(api, user, project, fmt="csv", tag="nsp", decisions=None):
    r = upload(api, user, project, fmt, tag)
    assert r.status_code == 201, r.text
    iid = r.json()["import_id"]
    if decisions:
        assert api.put(f"/api/v2/projects/{project}/schedule-imports/{iid}/decisions", user, json=decisions).status_code == 200
    b = api.post(f"/api/v2/projects/{project}/schedule-imports/{iid}/build", user)
    assert b.status_code == 201, b.text
    vid = b.json()["version_id"]
    a = api.post(f"/api/v2/projects/{project}/schedule-versions/{vid}/activate", user)
    assert a.status_code == 200, a.text
    return iid, vid


def seed_progress(project, ext_id, quantities: dict, supervisor, engineer, start_days_ago=30, pct=None, finish=False):
    """TEST SETUP: put approved progress into the ledgers the way a supervisor's decision would (claim -> decision -> ledger rows),
    in ONE transaction together with the decision's notification and audit record (the database refuses a decision without them)."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row) as c:
        c.execute("select set_config('app.system','on',true)")
        a = c.execute("select ba.activity_uid, ba.version_id from baseline_activities ba join schedule_versions v on v.version_id = ba.version_id "
                      "where v.project_id = %s and v.status = 'ACTIVE' and ba.external_activity_id = %s", (project, ext_id)).fetchone()
        ev = c.execute("insert into execution_events (project_id, filed_in_version_id, event_date, raw_claim_text, input_channel, filed_by, matched_activity_uid, status) "
                       "values (%s,%s,current_date,%s,'TYPED',%s,%s,'MATCHED') returning event_id", (project, a["version_id"], f"progress on {ext_id}", engineer.id, a["activity_uid"])).fetchone()
        dec = c.execute("insert into planner_decisions (project_id, event_id, selected_activity_uid, action, method, justification, decided_by) "
                        "values (%s,%s,%s,'APPROVE','QUANTITIES_AS_CLAIMED','Verified against measurement book',%s) returning decision_id", (project, ev["event_id"], a["activity_uid"], supervisor.id)).fetchone()
        for res, qty in quantities.items():
            asg = c.execute("select br.assignment_uid from baseline_resources br join project_resources pr on pr.resource_id = br.resource_id "
                            "where br.version_id = %s and br.activity_uid = %s and pr.resource_code = %s", (a["version_id"], a["activity_uid"], res)).fetchone()
            c.execute("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty) values (%s,%s,%s,%s,current_date,%s)",
                      (project, a["activity_uid"], asg["assignment_uid"], dec["decision_id"], qty))
        c.execute("insert into approved_activity_progress (project_id, activity_uid, decision_id, as_of_date, actual_start, actual_finish, reported_pct) values (%s,%s,%s,current_date,current_date - %s,%s,%s)",
                  (project, a["activity_uid"], dec["decision_id"], start_days_ago, dt.date.today() - dt.timedelta(days=1) if finish else None, pct))
        c.execute("insert into notifications (project_id, recipient_id, notification_type, decision_id, event_id, title) values (%s,%s,'CLAIM_DECISION',%s,%s,'Approved')",
                  (project, engineer.id, dec["decision_id"], ev["event_id"]))
        from backend.v2 import audit
        audit.log(c, project_id=project, actor_id=supervisor.id, role="SUPERVISOR", action="CLAIM_APPROVED", entity_type="PLANNER_DECISION",
                  entity_id=dec["decision_id"], after={"seeded_for_test": True})
    return a["activity_uid"]


def ledger_fingerprint():
    with connect() as c:
        return (c.execute("select entry_id, assignment_uid, cumulative_qty, incremental_qty, baseline_qty_at_entry, decision_id from approved_resource_progress order by entry_seq").fetchall(),
                c.execute("select entry_id, activity_uid, actual_start, actual_finish, reported_pct from approved_activity_progress order by entry_seq").fetchall(),
                c.execute("select decision_id, event_id, action from planner_decisions order by decision_id").fetchall())
