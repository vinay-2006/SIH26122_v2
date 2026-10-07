"""Local development sign-in, CORS and the permissions list: what a browser client needs, and the ways they must stay closed."""
import pytest
from fastapi.testclient import TestClient

from apikit import url
from v2api import Api, connect

PW = "local-dev-password-123"


@pytest.fixture
def enabled(monkeypatch):
    from backend.v2 import local_login
    monkeypatch.setenv("V2_LOCAL_LOGIN_PASSWORD", PW)
    monkeypatch.setenv("V2_LOCAL_LOGIN_DOMAIN", "test.local")
    local_login.reset_throttle()
    yield
    local_login.reset_throttle()


def login(client, email, password=PW):
    return client.post("/api/v2/auth/local-login", json={"email": email, "password": password})


def test_local_login_is_off_unless_the_operator_enables_it(world, client, monkeypatch):
    monkeypatch.delenv("V2_LOCAL_LOGIN_PASSWORD", raising=False)
    assert client.get("/api/v2/auth/config").json() == {"local_login": False}
    r = login(client, world.pm.email)
    assert r.status_code == 404 and r.json()["error"]["code"] == "LOCAL_LOGIN_DISABLED"
    monkeypatch.setenv("V2_LOCAL_LOGIN_PASSWORD", "short")                       # a weak password does not enable it either
    assert login(client, world.pm.email, "short").status_code == 404


def test_a_seeded_person_can_sign_in_and_the_token_is_an_ordinary_token(world, client, enabled):
    assert client.get("/api/v2/auth/config").json() == {"local_login": True}
    r = login(client, world.pm.email)
    assert r.status_code == 200 and r.json()["token_type"] == "bearer" and r.json()["expires_in"] == 8 * 3600
    tok = r.json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["email"] == world.pm.email and "CREATE_PROJECT" in me["capabilities"] and me["projects"][0]["my_role"] == "PROJECT_MANAGER"
    assert login(client, world.pm.email.upper()).status_code == 200                # e-mail is case-insensitive
    assert PW not in r.text


def test_failures_look_identical_and_do_not_reveal_who_exists(world, client, enabled):
    wrong = login(client, world.pm.email, "wrong-password-1234")
    unknown = login(client, "nobody@test.local")
    elsewhere = login(client, "someone@example.com")
    for r in (wrong, unknown, elsewhere):
        assert r.status_code == 401 and r.json() == {"error": {"code": "INVALID_CREDENTIALS", "message": "Incorrect email or password", "details": None}}
    assert "access_token" not in wrong.text


def test_the_domain_and_active_profile_restrictions_hold(world, client, enabled, monkeypatch):
    monkeypatch.setenv("V2_LOCAL_LOGIN_DOMAIN", "anvyra.demo")               # the test people are @test.local: not eligible
    assert login(client, world.pm.email).status_code == 401
    monkeypatch.setenv("V2_LOCAL_LOGIN_DOMAIN", "test.local")
    with connect(system=True) as c:
        c.execute("update profiles set is_active = false where id = %s", (world.se.id,))
    assert login(client, world.se.email).status_code == 401


def test_repeated_failures_are_throttled_per_email(world, client, enabled):
    for _ in range(6):
        assert login(client, world.pm.email, "wrong-password-1234").status_code == 401
    blocked = login(client, world.pm.email)                                         # even the right password is refused while throttled
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_a_hosted_target_always_disables_it(world, client, enabled, monkeypatch):
    monkeypatch.setenv("DB_V2_URL", "postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres")
    assert client.get("/api/v2/auth/config").json() == {"local_login": False}
    assert login(client, world.pm.email).status_code == 404


def test_the_project_response_lists_the_callers_server_side_permissions(world, api):
    w = world
    got = {n: api.get(f"/api/v2/projects/{w.project}", u).json() for n, u in (("pm", w.pm), ("sup", w.sup), ("se", w.se))}
    assert got["pm"]["my_role"] == "PROJECT_MANAGER" and {"MANAGE_SCHEDULE", "MANAGE_MEMBERS", "VIEW_CLAIM_COUNTS"} <= set(got["pm"]["my_permissions"])
    assert "REVIEW_CLAIMS" not in got["pm"]["my_permissions"] and "SUBMIT_CLAIM" not in got["pm"]["my_permissions"]
    assert {"REVIEW_CLAIMS", "RESOLVE_ISSUE"} <= set(got["sup"]["my_permissions"]) and "MANAGE_SCHEDULE" not in got["sup"]["my_permissions"]
    assert {"SUBMIT_CLAIM", "REPORT_ISSUE"} <= set(got["se"]["my_permissions"]) and not {"REVIEW_CLAIMS", "MANAGE_SCHEDULE", "VIEW_AUDIT"} & set(got["se"]["my_permissions"])


# ------------------------------------------------------------------ CORS
def app_with(monkeypatch, origins):
    from backend.v2.app import create_app
    if origins is None:
        monkeypatch.delenv("V2_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("V2_CORS_ORIGINS", origins)
    return TestClient(create_app())


def preflight(c, origin):
    return c.options("/api/v2/projects", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"})


def test_cors_is_closed_by_default_and_open_only_to_listed_origins(clean_db, monkeypatch):
    assert "access-control-allow-origin" not in preflight(app_with(monkeypatch, None), "http://localhost:5190").headers
    c = app_with(monkeypatch, "http://localhost:5190, http://127.0.0.1:5190")
    ok = preflight(c, "http://localhost:5190")
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5190"
    assert {"authorization", "content-type", "idempotency-key"} <= set(ok.headers["access-control-allow-headers"].lower().replace(" ", "").split(","))
    assert "access-control-allow-credentials" not in ok.headers
    assert "access-control-allow-origin" not in preflight(c, "https://evil.example").headers
    get = c.get("/health", headers={"Origin": "http://127.0.0.1:5190"})
    assert get.headers["access-control-allow-origin"] == "http://127.0.0.1:5190"


def test_a_wildcard_origin_is_refused_at_startup(monkeypatch):
    from backend.v2.app import create_app
    monkeypatch.setenv("V2_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="explicit origins"):
        create_app()
