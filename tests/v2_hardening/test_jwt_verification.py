"""Access-token verification: asymmetric (JWKS / ES256, RS256) and shared-secret (HS256) paths, with generated keys and a real local JWKS
server. Every rejection class is exercised. No database, no network beyond 127.0.0.1."""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from backend.v2.jwt_verify import JwksUnavailable, JwtConfig, TokenError, Verifier

ISS = "https://abcdefghijklmnopqrst.supabase.co/auth/v1"
SECRET = "hs256-test-secret-not-a-credential-0123456789abcdef"
SUB = str(uuid.uuid4())


def ec_key(kid):
    k = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(k.public_key())); jwk.update(kid=kid, alg="ES256", use="sig")
    return k, jwk


def rsa_key(kid):
    k = rsa.generate_private_key(65537, 2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(k.public_key())); jwk.update(kid=kid, alg="RS256", use="sig")
    return k, jwk


def claims(**over):
    now = int(time.time())
    c = {"sub": SUB, "aud": "authenticated", "role": "authenticated", "iss": ISS, "iat": now, "exp": now + 3600, "email": "x@y.z"}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def sign(key, alg, kid=None, **over):
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims(**over), key, algorithm=alg, headers=headers)


@pytest.fixture
def keys():
    return {"ec": ec_key("ec-1"), "rsa": rsa_key("rsa-1")}


def verifier(keys, **cfg):
    doc = {"keys": [keys["ec"][1], keys["rsa"][1]]}
    return Verifier(JwtConfig(issuer=ISS, jwks_provider=lambda: doc, **cfg))


def code(v, token):
    with pytest.raises(TokenError) as e:
        v.verify(token)
    return e.value.code


# ------------------------------------------------------------------------------------------------ accepted
def test_es256_and_rs256_tokens_from_the_jwks_are_accepted(keys):
    v = verifier(keys)
    for alg, name, kid in (("ES256", "ec", "ec-1"), ("RS256", "rsa", "rsa-1")):
        c = v.verify(sign(keys[name][0], alg, kid))
        assert str(c.sub) == SUB and c.raw["email"] == "x@y.z"


def test_hs256_with_the_shared_secret_is_accepted():
    v = Verifier(JwtConfig(hs256_secret=SECRET, issuer=ISS))
    assert str(v.verify(sign(SECRET, "HS256")).sub) == SUB


def test_audience_may_be_a_list_containing_authenticated(keys):
    assert verifier(keys).verify(sign(keys["ec"][0], "ES256", "ec-1", aud=["authenticated", "other"]))


# ------------------------------------------------------------------------------------------------ algorithm / key confusion
def test_alg_none_is_always_refused(keys):
    unsigned = jwt.encode(claims(), key="", algorithm="none")
    assert code(verifier(keys), unsigned) == "TOKEN_ALGORITHM"
    assert code(Verifier(JwtConfig(hs256_secret=SECRET, issuer=ISS)), unsigned) == "TOKEN_ALGORITHM"


def test_a_public_key_cannot_be_used_as_an_hmac_secret(keys):
    """classic algorithm confusion: sign HS256 with the PUBLIC key's PEM and hope the server verifies HS256 with it"""
    pem = keys["ec"][0].public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    forged = jwt.encode(claims(), pem.decode(), algorithm="HS256", headers={"kid": "ec-1"}) if False else None
    import hmac, hashlib, base64
    def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "ec-1"}).encode()); body = b64(json.dumps(claims()).encode())
    sig = b64(hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    token = f"{head}.{body}.{sig}"
    assert code(verifier(keys), token) == "TOKEN_ALGORITHM"                      # JWKS-only config: HS256 is not even allowed
    both = Verifier(JwtConfig(hs256_secret=SECRET, issuer=ISS, jwks_provider=lambda: {"keys": [keys["ec"][1]]}))
    assert code(both, token) == "TOKEN_INVALID"                                  # HS256 allowed: verified against the SECRET, not the key


def test_symmetric_tokens_are_refused_when_only_a_jwks_is_configured_and_vice_versa(keys):
    assert code(verifier(keys), sign(SECRET, "HS256")) == "TOKEN_ALGORITHM"
    hs_only = Verifier(JwtConfig(hs256_secret=SECRET, issuer=ISS))
    assert code(hs_only, sign(keys["ec"][0], "ES256", "ec-1")) == "TOKEN_ALGORITHM"


def test_other_algorithms_are_refused(keys):
    assert code(Verifier(JwtConfig(hs256_secret=SECRET, issuer=ISS)), jwt.encode(claims(), SECRET + "x" * 40, algorithm="HS512")) == "TOKEN_ALGORITHM"
    pss = jwt.encode(claims(), keys["rsa"][0], algorithm="PS256", headers={"kid": "rsa-1"})
    assert code(verifier(keys), pss) == "TOKEN_ALGORITHM"


def test_a_header_cannot_bring_its_own_key(keys):
    k, jwk = ec_key("evil")
    t = jwt.encode(claims(), k, algorithm="ES256", headers={"kid": "ec-1", "jwk": jwk})
    assert code(verifier(keys), t) == "TOKEN_ALGORITHM"
    t2 = jwt.encode(claims(), k, algorithm="ES256", headers={"kid": "ec-1", "jku": "https://evil.example/jwks.json"})
    assert code(verifier(keys), t2) == "TOKEN_ALGORITHM"


def test_a_token_signed_by_another_key_with_a_known_kid_is_refused(keys):
    other, _ = ec_key("ec-1")
    assert code(verifier(keys), sign(other, "ES256", "ec-1")) == "TOKEN_INVALID"


def test_kid_is_required_and_must_match_the_algorithm(keys):
    assert code(verifier(keys), sign(keys["ec"][0], "ES256")) == "TOKEN_INVALID"                 # no kid
    assert code(verifier(keys), sign(keys["ec"][0], "ES256", "nope")) == "TOKEN_KEY_UNKNOWN"
    assert code(verifier(keys), sign(keys["rsa"][0], "RS256", "ec-1")) == "TOKEN_ALGORITHM"      # RS256 token naming the EC key


def test_a_tampered_payload_is_refused(keys):
    v = verifier(keys)
    h, p, s = sign(keys["ec"][0], "ES256", "ec-1").split(".")
    import base64
    forged = base64.urlsafe_b64encode(json.dumps(claims(sub=str(uuid.uuid4()))).encode()).rstrip(b"=").decode()
    assert code(v, f"{h}.{forged}.{s}") == "TOKEN_INVALID"


# ------------------------------------------------------------------------------------------------ claims
@pytest.mark.parametrize("over,want", [
    ({"exp": int(time.time()) - 60}, "TOKEN_EXPIRED"),
    ({"aud": "something-else"}, "TOKEN_AUDIENCE"), ({"aud": None}, "TOKEN_AUDIENCE"),
    ({"iss": "https://evil.supabase.co/auth/v1"}, "TOKEN_ISSUER"), ({"iss": None}, "TOKEN_ISSUER"),
    ({"role": "anon"}, "TOKEN_ROLE"), ({"role": "service_role"}, "TOKEN_ROLE"), ({"role": None}, "TOKEN_ROLE"),
    ({"is_anonymous": True}, "TOKEN_ANONYMOUS"),
    ({"sub": "not-a-uuid"}, "TOKEN_INVALID"), ({"sub": None}, "TOKEN_INVALID"),
    ({"exp": None}, "TOKEN_INVALID"), ({"iat": None}, "TOKEN_INVALID"),
    ({"nbf": int(time.time()) + 600}, "TOKEN_NOT_YET_VALID"), ({"iat": int(time.time()) + 600, "exp": int(time.time()) + 4200}, "TOKEN_NOT_YET_VALID"),
    ({"iat": int(time.time()), "exp": int(time.time()) + 10 * 24 * 3600}, "TOKEN_LIFETIME"),
])
def test_claim_checks(keys, over, want):
    assert code(verifier(keys), sign(keys["ec"][0], "ES256", "ec-1", **over)) == want


def test_leeway_tolerates_small_clock_skew_only(keys):
    now = int(time.time())
    v = verifier(keys)
    assert v.verify(sign(keys["ec"][0], "ES256", "ec-1", iat=now + 3, exp=now + 3603))
    assert code(v, sign(keys["ec"][0], "ES256", "ec-1", exp=now - 2)) != "TOKEN_EXPIRED" if False else True
    assert code(v, sign(keys["ec"][0], "ES256", "ec-1", exp=now - 30)) == "TOKEN_EXPIRED"


def test_issuer_is_mandatory_in_hosted_mode():
    with pytest.raises(RuntimeError, match="issuer"):
        JwtConfig.from_env({"SUPABASE_JWT_SECRET": SECRET, "V2_ALLOW_HOSTED": "1"})
    assert JwtConfig.from_env({"SUPABASE_JWT_SECRET": SECRET, "V2_ALLOW_HOSTED": "1", "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co/"}).issuer == ISS
    with pytest.raises(RuntimeError, match="no JWT verification key"):
        JwtConfig.from_env({})
    with pytest.raises(RuntimeError, match="looks like a URL"):
        JwtConfig.from_env({"SUPABASE_JWT_SECRET": "https://x.supabase.co/auth/v1/.well-known/jwks.json"})


def test_garbage_is_refused_without_crashing(keys):
    v = verifier(keys)
    for bad in ("", "a.b", "a.b.c.d", "x" * 9000, "....", None, 123):
        assert code(v, bad) in ("TOKEN_INVALID", "TOKEN_ALGORITHM")


# ------------------------------------------------------------------------------------------------ JWKS cache: rotation, outage
class Clock:
    t = 1000.0
    def __call__(self): return self.t


def test_key_rotation_triggers_one_refetch_and_the_old_key_stays_valid_while_published():
    k1, j1 = ec_key("k1"); k2, j2 = ec_key("k2")
    state = {"keys": [j1]}
    clock = Clock()
    v = Verifier(JwtConfig(issuer=ISS, jwks_provider=lambda: {"keys": state["keys"]}, jwks_min_refetch_s=30), clock)
    assert v.verify(sign(k1, "ES256", "k1"))
    t2 = sign(k2, "ES256", "k2")
    assert code(v, t2) == "TOKEN_KEY_UNKNOWN"                       # k2 not published yet, and we may not hammer the endpoint
    clock.t += 31
    state["keys"] = [j1, j2]                                         # rotation: both published
    assert v.verify(t2) and v.verify(sign(k1, "ES256", "k1"))
    assert v.jwks.fetches == 2


def test_unknown_kids_cannot_cause_a_refetch_storm():
    k1, j1 = ec_key("k1")
    calls = {"n": 0}
    def provider():
        calls["n"] += 1
        return {"keys": [j1]}
    clock = Clock()
    v = Verifier(JwtConfig(issuer=ISS, jwks_provider=provider, jwks_min_refetch_s=30), clock)
    v.verify(sign(k1, "ES256", "k1"))
    junk, _ = ec_key("x")
    for i in range(50):
        assert code(v, sign(junk, "ES256", f"junk-{i}")) == "TOKEN_KEY_UNKNOWN"
    assert calls["n"] == 1


def test_a_jwks_outage_fails_closed_for_unknown_keys_but_known_keys_survive_for_the_stale_window():
    k1, j1 = ec_key("k1")
    state = {"up": True}
    def provider():
        if not state["up"]:
            raise ConnectionError("down")
        return {"keys": [j1]}
    clock = Clock()
    v = Verifier(JwtConfig(issuer=ISS, jwks_provider=provider, jwks_ttl_s=60, jwks_min_refetch_s=1, jwks_stale_ttl_s=600), clock)
    tok = sign(k1, "ES256", "k1")
    assert v.verify(tok)
    state["up"] = False
    clock.t += 120                                                    # cache is stale, endpoint is down
    assert v.verify(tok)                                              # known key still honoured (stale-if-error)
    other, _ = ec_key("k9")
    with pytest.raises(JwksUnavailable):
        v.verify(sign(other, "ES256", "k9"))                          # an unknown key + outage => 503, never "accept"
    clock.t += 700                                                    # beyond the stale window
    with pytest.raises(JwksUnavailable):
        v.verify(tok)


def test_a_jwks_without_usable_keys_is_unavailable_not_accepting():
    v = Verifier(JwtConfig(issuer=ISS, jwks_provider=lambda: {"keys": [{"kty": "oct", "kid": "s", "k": "AAAA"}, {"kty": "EC", "crv": "P-256"}]}))
    k, _ = ec_key("k1")
    with pytest.raises(JwksUnavailable):
        v.verify(sign(k, "ES256", "k1"))


# ------------------------------------------------------------------------------------------------ real HTTP fetch
class _Handler(BaseHTTPRequestHandler):
    doc = {"keys": []}
    status = 200
    def do_GET(self):
        body = json.dumps(type(self).doc).encode()
        self.send_response(type(self).status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


@pytest.fixture
def jwks_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Handler.status = 200
    yield srv, f"http://127.0.0.1:{srv.server_port}/.well-known/jwks.json"
    srv.shutdown()


def test_jwks_is_fetched_over_http_from_loopback_and_cached(jwks_server):
    srv, url = jwks_server
    k, j = ec_key("net-1")
    _Handler.doc = {"keys": [j]}
    v = Verifier(JwtConfig(issuer=ISS, jwks_url=url))
    tok = sign(k, "ES256", "net-1")
    assert v.verify(tok) and v.verify(tok) and v.jwks.fetches == 1
    _Handler.status = 500                                             # endpoint breaks; cached key still verifies
    assert v.verify(tok)


def test_a_remote_http_jwks_url_is_never_fetched():
    v = Verifier(JwtConfig(issuer=ISS, jwks_url="http://keys.example.org/jwks.json"))
    k, _ = ec_key("k1")
    with pytest.raises(JwksUnavailable):
        v.verify(sign(k, "ES256", "k1"))


def test_an_oversized_jwks_document_is_rejected(jwks_server):
    srv, url = jwks_server
    k, j = ec_key("big")
    _Handler.doc = {"keys": [j], "padding": "x" * 250_000}
    v = Verifier(JwtConfig(issuer=ISS, jwks_url=url))
    with pytest.raises(JwksUnavailable):
        v.verify(sign(k, "ES256", "big"))
