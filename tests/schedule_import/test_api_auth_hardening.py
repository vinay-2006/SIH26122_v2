"""Authentication through the real request path with asymmetric (ES256) tokens, a live JWKS endpoint, session revocation and startup checks."""
import datetime as dt
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from v2api import connect, make_user

P = "/api/v2/projects"
REF = "abcdefghijklmnopqrst"
SUPA = f"https://{REF}.supabase.co"
ISS = f"{SUPA}/auth/v1"


def new_key(kid):
    k = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(k.public_key())); jwk.update(kid=kid, alg="ES256", use="sig")
    return k, jwk


class Jwks(BaseHTTPRequestHandler):
    doc = {"keys": []}
    fail = False
    hits = 0
    def do_GET(self):
        type(self).hits += 1
        if type(self).fail:
            self.send_response(503); self.end_headers(); return
        body = json.dumps(type(self).doc).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


@pytest.fixture
def asym(world, monkeypatch):
    """the world is built with HS256 tokens, then the API is switched to JWKS-only verification (as a Supabase project with asymmetric keys)"""
    from backend.v2 import jwt_verify
    srv = HTTPServer(("127.0.0.1", 0), Jwks)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Jwks.fail, Jwks.hits = False, 0
    key, jwk = new_key("k1")
    Jwks.doc = {"keys": [jwk]}
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("V2_JWKS_URL", f"http://127.0.0.1:{srv.server_port}/jwks.json")
    monkeypatch.setenv("SUPABASE_URL", SUPA)
    monkeypatch.setenv("V2_JWKS_MIN_REFETCH_S", "0")
    jwt_verify.reset_verifier()
    world.key, world.jwk = key, jwk
    yield world
    srv.shutdown()
    jwt_verify.reset_verifier()


def tok(w, user, key=None, kid="k1", **over):
    now = int(time.time())
    c = {"sub": str(user.id), "aud": "authenticated", "role": "authenticated", "iss": ISS, "iat": now, "exp": now + 3600, "email": user.email}
    c.update(over)
    return jwt.encode({k: v for k, v in c.items() if v is not None}, key or w.key, algorithm="ES256", headers={"kid": kid})


def get(api, token, path=None, w=None):
    return api.c.get(path or f"{P}", headers={"Authorization": f"Bearer {token}"})


def test_asymmetric_tokens_work_end_to_end_and_the_old_shared_secret_no_longer_does(asym, api):
    w = asym
    r = get(api, tok(w, w.se), f"{P}/{w.project}")
    assert r.status_code == 200 and r.json()["my_role"] == "SITE_ENGINEER"
    import v2api
    hs = jwt.encode({"sub": str(w.se.id), "aud": "authenticated", "role": "authenticated", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 3600},
                    v2api.SECRET, algorithm="HS256")
    r = get(api, hs, f"{P}/{w.project}")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_ALGORITHM"           # secret is not configured => HS256 is not accepted at all


@pytest.mark.parametrize("over,code", [
    ({"role": "anon"}, "TOKEN_ROLE"), ({"role": "service_role"}, "TOKEN_ROLE"), ({"aud": "other"}, "TOKEN_AUDIENCE"),
    ({"iss": "https://evilevilevilevilevil.supabase.co/auth/v1"}, "TOKEN_ISSUER"), ({"is_anonymous": True}, "TOKEN_ANONYMOUS"),
    ({"exp": int(time.time()) - 120}, "TOKEN_EXPIRED"), ({"sub": "nope"}, "TOKEN_INVALID"),
])
def test_bad_claims_are_401_with_a_specific_code(asym, api, over, code):
    r = get(api, tok(asym, asym.pm, **over), f"{P}/{asym.project}")
    assert r.status_code == 401 and r.json()["error"]["code"] == code
    assert "traceback" not in r.text.lower()


def test_a_token_signed_by_an_unpublished_key_is_refused(asym, api):
    other, _ = new_key("k1")
    r = get(api, tok(asym, asym.pm, key=other), f"{P}/{asym.project}")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_INVALID"


def test_key_rotation_is_picked_up_without_a_restart(asym, api):
    w = asym
    k2, jwk2 = new_key("k2")
    t2 = tok(w, w.pm, key=k2, kid="k2")
    assert get(api, t2, f"{P}/{w.project}").status_code == 401                              # k2 not published yet
    Jwks.doc = {"keys": [w.jwk, jwk2]}                                                       # Supabase rotates: both keys published
    assert get(api, t2, f"{P}/{w.project}").status_code == 200
    assert get(api, tok(w, w.pm), f"{P}/{w.project}").status_code == 200                    # the previous key still verifies while published


def test_a_key_endpoint_outage_is_a_503_for_unknown_keys_and_harmless_for_known_ones(asym, api):
    w = asym
    assert get(api, tok(w, w.pm), f"{P}/{w.project}").status_code == 200                    # primes the cache
    Jwks.fail = True
    assert get(api, tok(w, w.pm), f"{P}/{w.project}").status_code == 200                    # known key: served from cache
    k9, _ = new_key("k9")
    r = get(api, tok(w, w.pm, key=k9, kid="k9"), f"{P}/{w.project}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUTH_UNAVAILABLE"         # cannot say; fails closed, never open


def test_the_legacy_dev_mode_and_unsigned_tokens_do_not_exist_in_v2(asym, api, monkeypatch):
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    unsigned = jwt.encode({"sub": str(asym.pm.id), "aud": "authenticated", "role": "authenticated", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 600},
                          key="", algorithm="none")
    r = get(api, unsigned, f"{P}/{asym.project}")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_ALGORITHM"


def test_a_user_without_a_profile_such_as_a_phone_signup_has_no_access(asym, api):
    with connect(system=True) as c:
        uid = uuid.uuid4()
        c.execute("insert into auth.users (id, email) values (%s, null)", (uid,))             # no email => the trigger creates no profile
    ghost = type("U", (), {"id": uid, "email": None})()
    r = get(api, tok(asym, ghost), P)
    assert r.status_code == 401 and r.json()["error"]["code"] == "NO_PROFILE"


def test_the_email_shown_follows_the_auth_email(asym, api):
    with connect(system=True) as c:
        c.execute("update auth.users set email = 'renamed.pm@test.local' where id = %s", (asym.pm.id,))
    assert api.c.get("/api/v2/me", headers={"Authorization": f"Bearer {tok(asym, asym.pm)}"}).json()["email"] == "renamed.pm@test.local"


# ------------------------------------------------------------------------------------------------ revocation
def test_an_admin_can_revoke_sessions_and_only_older_tokens_die(asym, api):
    w = asym
    old = tok(w, w.se)
    assert get(api, old, f"{P}/{w.project}").status_code == 200
    r = api.c.post(f"/api/v2/platform/users/{w.se.id}/revoke-sessions", headers={"Authorization": f"Bearer {tok(w, w.se)}"})
    assert r.status_code == 403                                                              # not even the user can do this to themselves via the API
    r = api.c.post(f"/api/v2/platform/users/{w.se.id}/revoke-sessions", headers={"Authorization": f"Bearer {tok(w, w.admin)}"})
    assert r.status_code == 200
    denied = get(api, old, f"{P}/{w.project}")
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "TOKEN_REVOKED"
    time.sleep(1.2)
    assert get(api, tok(w, w.se), f"{P}/{w.project}").status_code == 200                    # a fresh sign-in works
    assert get(api, tok(w, w.pm), f"{P}/{w.project}").status_code == 200                    # other users untouched
    assert api.c.post(f"/api/v2/platform/users/{uuid.uuid4()}/revoke-sessions", headers={"Authorization": f"Bearer {tok(w, w.admin)}"}).status_code == 404
    with connect() as c:
        assert c.execute("select action from audit_logs where action = 'SESSIONS_REVOKED'").fetchone()


def test_deleting_the_auth_user_locks_the_person_out(asym, api):
    w = asym
    t = tok(w, w.se)
    with connect(system=True) as c:
        c.execute("delete from auth.users where id = %s", (w.se.id,))
    r = get(api, t, f"{P}/{w.project}")
    assert r.status_code in (401, 403) and r.json()["error"]["code"] in ("TOKEN_REVOKED", "ACCOUNT_DISABLED")


# ------------------------------------------------------------------------------------------------ startup
def test_the_app_refuses_to_start_with_unsafe_or_missing_auth_configuration(world, monkeypatch):
    from backend.v2.app import create_app
    from backend.v2 import jwt_verify
    for k in ("SUPABASE_JWT_SECRET", "V2_JWKS_URL", "SUPABASE_JWKS_URL", "V2_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    jwt_verify.reset_verifier()
    with pytest.raises(RuntimeError, match="no JWT verification key"):
        with TestClient(create_app()):
            pass
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-secret-that-is-long-enough-0123456789")
    monkeypatch.setenv("V2_ALLOW_HOSTED", "1")                                               # hosted mode without an issuer
    jwt_verify.reset_verifier()
    with pytest.raises(RuntimeError, match="issuer"):
        with TestClient(create_app()):
            pass
    monkeypatch.delenv("V2_ALLOW_HOSTED")
    monkeypatch.setenv("DB_V2_URL", "postgresql://postgres@127.0.0.1:54329/setuai_integ_demo")   # the OLD demo database
    jwt_verify.reset_verifier()
    with pytest.raises(RuntimeError, match="refusing database target"):
        with TestClient(create_app()):
            pass
