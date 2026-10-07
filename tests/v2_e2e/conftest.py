"""End-to-end workflows over HTTP only (plus read-only SQL to prove what the database holds). Each test starts from a project the Project Manager
creates, imports, maps, builds and activates through the API."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

for d in ("schedule_import", "v2_domain", "v2_api"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / d))
from v2api import api, clean_db, client, client_500, world, build_and_activate, connect, make_user  # noqa: E402,F401
import apikit  # noqa: E402


@pytest.fixture(autouse=True)
def evidence_dir(tmp_path, monkeypatch):
    from backend.v2 import storage
    monkeypatch.setenv("V2_EVIDENCE_DIR", str(tmp_path / "evidence"))
    storage.set_store(None)
    yield
    storage.set_store(None)


class Story(SimpleNamespace):
    def url(self, tail=""):
        return f"/api/v2/projects/{self.project}{tail}"

    def uid(self, ext):
        return self.acts[ext]["activity_uid"]

    def asg(self, ext, resource):
        return next(a for a in self.acts[ext]["measured_assignments"] if a["resource_code"] == resource)["assignment_uid"]

    def pct(self, ext, who=None, **params):
        r = self.api.get(self.url("/dashboard/activities"), who or self.pm, params={"as_of": "2100-01-01", "limit": 200, **params})
        return {a["external_activity_id"]: a["physical_pct"] for a in r.json()["items"]}[ext]

    def summary(self, who=None, **params):
        return self.api.get(self.url("/dashboard/summary"), who or self.pm, params={"as_of": "2100-01-01", **params}).json()

    def count(self, table, where="true", *params):
        with connect() as c:
            return c.execute(f"select count(*) n from {table} where project_id = %s and ({where})", (self.project, *params)).fetchone()["n"]

    def claim(self, ext="A2010", qty=None, who=None, expect=201, **kw):
        body = apikit.claim_body(SimpleNamespace(uid=self.uid), ext, qty, **kw)
        r = self.api.post(self.url("/claims"), who or self.se, json=body)
        assert r.status_code == expect, r.text
        return r.json()

    def decide(self, claim_id, who=None, expect=201, **body):
        body.setdefault("action", "APPROVE")
        r = self.api.post(self.url(f"/claims/{claim_id}/decision"), who or self.sup, json=body)
        assert r.status_code == expect, r.text
        return r.json()


@pytest.fixture
def story(world, api):
    """workflow 1 (quick form): the PM creates a project, adds people, imports + builds + activates the NSP baseline"""
    r = api.post("/api/v2/projects", world.pm, json={"project_code": "E2E-PIPE", "project_name": "E2E Northern Spur Pipeline", "location": "Assam", "lifecycle_status": "ONGOING"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    for u, role in ((world.sup, "SUPERVISOR"), (world.se, "SITE_ENGINEER"), (world.se2, "SITE_ENGINEER")):
        assert api.post(f"/api/v2/projects/{pid}/members", world.pm, json={"email": u.email, "role": role}).status_code == 201
    _, vid = build_and_activate(api, world.pm, pid, "csv")
    s = Story(project=pid, version=vid, world=world, api=api, pm=world.pm, sup=world.sup, se=world.se, se2=world.se2, outsider=world.outsider)
    items = api.get(s.url("/activities"), world.se, params={"limit": 200}).json()["items"]
    s.acts = {a["external_activity_id"]: a for a in items}
    return s
