"""Strict access-token verification for the v2 API (Supabase-style JWTs). No dev mode, no unsigned tokens, no network fallback.

* The verification KEY is chosen by the token's algorithm, never the other way round, so a token cannot trick the verifier into
  using a public key as an HMAC secret:  HS256 -> the configured shared secret;  ES256 / RS256 -> a key from the JWKS, by `kid`.
  Anything else (alg=none, HS512, PS256, ...) is refused, as are headers that embed their own key (jwk / jku / x5u).
* Required claims: exp, iat, sub (a UUID).  Audience must match (default "authenticated").  Issuer must match when configured and is
  MANDATORY for hosted targets.  role must be "authenticated" (Supabase anon and service_role tokens are refused).  Anonymous
  sign-ins (is_anonymous) are refused.  A token may not live longer than max_lifetime_s.  5 s of clock leeway.
* JWKS: fetched over HTTPS (HTTP only for loopback), size-capped, cached; an unknown kid triggers at most one refetch per
  min_refetch seconds (key rotation without a refetch storm); if the endpoint is down, known keys keep working for stale_ttl,
  otherwise the request fails closed (503).
Which token is acceptable is decided here; whether the person may do anything is decided later by profile + project membership."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ASYMMETRIC = {"ES256": ("EC", ECAlgorithm), "RS256": ("RSA", RSAAlgorithm)}


class TokenError(Exception):
    def __init__(self, code: str, message: str = "The access token was rejected"):
        super().__init__(message)
        self.code, self.message = code, message


class JwksUnavailable(Exception):
    pass


@dataclass
class JwtConfig:
    hs256_secret: Optional[str] = None
    jwks_url: Optional[str] = None
    issuer: Optional[str] = None
    audience: str = "authenticated"
    leeway_s: int = 5
    max_lifetime_s: int = 24 * 3600
    require_issuer: bool = False
    jwks_ttl_s: float = 600
    jwks_min_refetch_s: float = 30
    jwks_stale_ttl_s: float = 3600
    jwks_provider: Optional[Callable[[], Dict[str, Any]]] = field(default=None, repr=False)   # test seam / custom transport

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "JwtConfig":
        e = dict(os.environ) if env is None else env
        sup = (e.get("SUPABASE_URL") or "").rstrip("/")
        issuer = e.get("V2_JWT_ISSUER") or (f"{sup}/auth/v1" if sup else None)
        hosted = e.get("V2_ALLOW_HOSTED") == "1"
        cfg = cls(hs256_secret=(e.get("V2_JWT_SECRET") or e.get("SUPABASE_JWT_SECRET") or None),
                  jwks_url=(e.get("V2_JWKS_URL") or e.get("SUPABASE_JWKS_URL") or None), issuer=issuer,
                  audience=e.get("V2_JWT_AUDIENCE") or "authenticated", require_issuer=hosted,
                  jwks_ttl_s=float(e.get("V2_JWKS_TTL_S") or 600), jwks_min_refetch_s=float(e.get("V2_JWKS_MIN_REFETCH_S") or 30),
                  jwks_stale_ttl_s=float(e.get("V2_JWKS_STALE_TTL_S") or 3600))
        if cfg.hs256_secret and cfg.hs256_secret.startswith(("http://", "https://")):
            raise RuntimeError("the HS256 secret looks like a URL: it is a shared secret, not a JWKS URL")
        if not cfg.hs256_secret and not cfg.jwks_url and cfg.jwks_provider is None:
            raise RuntimeError("no JWT verification key is configured (V2_JWT_SECRET or V2_JWKS_URL)")
        if cfg.require_issuer and not cfg.issuer:
            raise RuntimeError("hosted mode requires a JWT issuer (SUPABASE_URL or V2_JWT_ISSUER)")
        return cfg


@dataclass
class Claims:
    sub: uuid.UUID
    iat: int
    exp: int
    raw: Dict[str, Any]


class JwksCache:
    def __init__(self, cfg: JwtConfig, clock: Callable[[], float] = time.monotonic):
        self.cfg, self.clock = cfg, clock
        self._keys: Dict[str, Any] = {}
        self._fetched = float("-inf")
        self._attempted = float("-inf")
        self._failing = False                     # the last refresh attempt failed and nothing has succeeded since
        self._lock = threading.Lock()
        self.fetches = 0

    def _fetch(self) -> Dict[str, Any]:
        if self.cfg.jwks_provider is not None:
            doc = self.cfg.jwks_provider()
        else:
            u = urlparse(self.cfg.jwks_url or "")
            if u.scheme != "https" and not (u.scheme == "http" and (u.hostname or "") in ("127.0.0.1", "localhost", "::1")):
                raise JwksUnavailable("JWKS must be fetched over https")
            req = urllib.request.Request(self.cfg.jwks_url, headers={"Accept": "application/json", "User-Agent": "sih-v2-api"})
            with urllib.request.urlopen(req, timeout=3) as r:                       # noqa: S310 (scheme checked above)
                body = r.read(200_001)
            if len(body) > 200_000:
                raise JwksUnavailable("JWKS document too large")
            doc = json.loads(body)
        keys: Dict[str, Any] = {}
        for jwk in (doc.get("keys") or []):
            kty, kid = jwk.get("kty"), jwk.get("kid")
            if not kid or jwk.get("use", "sig") != "sig":
                continue
            for alg, (want_kty, impl) in ASYMMETRIC.items():
                if kty == want_kty and jwk.get("alg", alg) == alg:
                    keys[kid] = (alg, impl.from_jwk(json.dumps(jwk)))
        if not keys:
            raise JwksUnavailable("JWKS contains no usable signing key")
        return keys

    def get(self, kid: str, alg: str):
        now = self.clock()
        with self._lock:
            fresh = now - self._fetched < self.cfg.jwks_ttl_s
            if not (kid in self._keys and fresh) and now - self._attempted >= self.cfg.jwks_min_refetch_s:
                self._attempted = now
                try:
                    self._keys, self._fetched = self._fetch(), now
                    self.fetches += 1
                    self._failing = False
                except Exception as exc:                                              # endpoint down / bad document
                    self._failing = True
                    stale_ok = kid in self._keys and now - self._fetched < self.cfg.jwks_stale_ttl_s
                    if not stale_ok:
                        raise JwksUnavailable("signing keys are unavailable") from exc
            elif kid not in self._keys and now - self._fetched >= self.cfg.jwks_stale_ttl_s:
                raise JwksUnavailable("signing keys are unavailable")
            entry = self._keys.get(kid)
            outage = self._failing
        if entry is None:
            if outage:                                          # we could not refresh, so we cannot say the key is unknown: fail closed with 503
                raise JwksUnavailable("signing keys are unavailable")
            raise TokenError("TOKEN_KEY_UNKNOWN")
        if entry[0] != alg:
            raise TokenError("TOKEN_ALGORITHM")
        return entry[1]


class Verifier:
    def __init__(self, cfg: JwtConfig, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.jwks = JwksCache(cfg, clock) if (cfg.jwks_url or cfg.jwks_provider) else None
        self.allowed = set()
        if cfg.hs256_secret:
            self.allowed.add("HS256")
        if self.jwks is not None:
            self.allowed |= set(ASYMMETRIC)

    def verify(self, token: str) -> Claims:
        if not isinstance(token, str) or not token or len(token) > 8192 or token.count(".") != 2:
            raise TokenError("TOKEN_INVALID")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenError("TOKEN_INVALID")
        alg = header.get("alg")
        if alg not in self.allowed or any(h in header for h in ("jwk", "jku", "x5u", "x5c")):
            raise TokenError("TOKEN_ALGORITHM")
        key: Any = self.cfg.hs256_secret
        if alg != "HS256":
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise TokenError("TOKEN_INVALID")
            key = self.jwks.get(kid, alg)
        issuer = self.cfg.issuer if (self.cfg.issuer or self.cfg.require_issuer) else None
        try:
            c = jwt.decode(token, key, algorithms=[alg], audience=self.cfg.audience, issuer=issuer, leeway=self.cfg.leeway_s,
                           options={"require": ["exp", "iat", "sub"], "verify_aud": True, "verify_iss": issuer is not None})
        except jwt.ExpiredSignatureError:
            raise TokenError("TOKEN_EXPIRED", "The access token has expired")
        except jwt.InvalidAudienceError:
            raise TokenError("TOKEN_AUDIENCE")
        except jwt.InvalidIssuerError:
            raise TokenError("TOKEN_ISSUER")
        except jwt.MissingRequiredClaimError as e:
            raise TokenError({"aud": "TOKEN_AUDIENCE", "iss": "TOKEN_ISSUER"}.get(e.claim, "TOKEN_INVALID"))
        except (jwt.ImmatureSignatureError, jwt.InvalidIssuedAtError):
            raise TokenError("TOKEN_NOT_YET_VALID")
        except jwt.PyJWTError:
            raise TokenError("TOKEN_INVALID")
        if c.get("role") != "authenticated":                                          # refuses anon / service_role / custom roles
            raise TokenError("TOKEN_ROLE")
        if c.get("is_anonymous") in (True, "true", 1):
            raise TokenError("TOKEN_ANONYMOUS")
        if not isinstance(c["exp"], (int, float)) or not isinstance(c["iat"], (int, float)) or c["exp"] - c["iat"] > self.cfg.max_lifetime_s:
            raise TokenError("TOKEN_LIFETIME")
        try:
            sub = uuid.UUID(str(c["sub"]))
        except ValueError:
            raise TokenError("TOKEN_INVALID")
        return Claims(sub=sub, iat=int(c["iat"]), exp=int(c["exp"]), raw=c)


# ---------------------------------------------------------------------------------------------- process-wide verifier
_lock = threading.Lock()
_cached: Optional[tuple] = None


def get_verifier() -> Verifier:
    """One verifier per configuration (so the JWKS cache persists across requests); rebuilt if the environment changes."""
    global _cached
    cfg = JwtConfig.from_env()
    sig = (cfg.hs256_secret, cfg.jwks_url, cfg.issuer, cfg.audience, cfg.require_issuer, cfg.jwks_ttl_s, cfg.jwks_min_refetch_s, cfg.jwks_stale_ttl_s)
    with _lock:
        if _cached is None or _cached[0] != sig:
            _cached = (sig, Verifier(cfg))
        return _cached[1]


def reset_verifier() -> None:
    global _cached
    with _lock:
        _cached = None
