"""Which database may this code touch?  One rule set shared by the migration runner and the v2 API.

LOCAL   loopback host and a database named setuai_v2_*  (no further opt-in; the marker row must say env=integration)
HOSTED  a Supabase project, ONLY when ALL of these hold:
          * V2_ALLOW_HOSTED=1                                  (explicit opt-in)
          * its project ref is listed in V2_ALLOWED_PROJECT_REFS (explicit allowlist; one or more refs)
          * its ref is not one of the refs found in the repo's legacy .env  (the old shared project can never be allowed)
          * TLS is required in the URL (sslmode=require|verify-ca|verify-full)
          * once connected, the database's own marker row says env=hosted, schema=sih-v2-baseline and project_ref=<this ref>
            (a fresh, EMPTY database may be stamped once by the migration runner; a non-empty database never is)
ANYTHING ELSE is refused. Nothing here prints a URL, password or key.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Set
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DB_PREFIX = "setuai_v2_"
SCHEMA_ID = "sih-v2-baseline"
_REF = r"[a-z0-9]{16,24}"


class GuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    kind: str                        # 'local' | 'supabase'
    host: str
    port: Optional[int]
    dbname: str
    user: str
    ref: Optional[str]               # Supabase project ref (hosted only)
    sslmode: Optional[str]


def _norm(url: str):
    u = urlparse(url)
    return u, (u.hostname or "").lower(), (u.path or "").lstrip("/")


def project_ref_of(host: str, user: str) -> Optional[str]:
    """db.<ref>.supabase.co  (direct)   or   user 'role.<ref>' on *.pooler.supabase.com  (Supavisor)"""
    m = re.fullmatch(rf"db\.({_REF})\.supabase\.co", host)
    if m:
        return m.group(1)
    if host.endswith(".pooler.supabase.com"):
        m = re.fullmatch(rf"[a-z_]+\.({_REF})", user or "")
        if m:
            return m.group(1)
    return None


def _legacy_env_values() -> Dict[str, str]:
    env = ROOT / ".env"
    out: Dict[str, str] = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def old_shared_host() -> Optional[str]:
    v = _legacy_env_values().get("DATABASE_URL")
    return ((urlparse(v).hostname or "").lower() or None) if v else None


def old_project_refs() -> Set[str]:
    """refs of the legacy shared Supabase project(s), read (never printed) from the repo .env"""
    refs: Set[str] = set()
    vals = _legacy_env_values()
    for key in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_JWKS_URL"):
        v = vals.get(key)
        if not v:
            continue
        u = urlparse(v)
        h = (u.hostname or "").lower()
        r = project_ref_of(h, u.username or "")
        if r:
            refs.add(r)
        m = re.fullmatch(rf"({_REF})\.supabase\.co", h)
        if m:
            refs.add(m.group(1))
    return refs


def allowed_refs(env: Dict[str, str]) -> Set[str]:
    return {r.strip().lower() for r in (env.get("V2_ALLOWED_PROJECT_REFS") or "").split(",") if r.strip()}


def check_target(url: str, env: Optional[Dict[str, str]] = None, old_host_fn: Callable[[], Optional[str]] = old_shared_host,
                 old_refs_fn: Callable[[], Set[str]] = old_project_refs) -> Target:
    env = dict(os.environ) if env is None else env
    u, host, db = _norm(url)
    if not host or not db:
        raise GuardError("database URL must include host and database name")
    old = old_host_fn()
    if old and host == old:
        raise GuardError("target host equals the old shared database host: refusing")
    user = u.username or ""
    if host in LOOPBACK:
        if not db.lower().startswith(DB_PREFIX):
            raise GuardError(f"database name {db!r} must start with {DB_PREFIX!r}")
        return Target("local", host, u.port, db, user, None, None)
    if not (host.endswith(".supabase.co") or host.endswith(".supabase.com")):
        raise GuardError(f"target host {host!r} is not loopback and not a Supabase host: refusing")
    # ---- hosted Supabase
    if env.get("V2_ALLOW_HOSTED") != "1":
        raise GuardError("hosted Supabase targets need V2_ALLOW_HOSTED=1 and an allow-listed project ref (not enabled)")
    ref = project_ref_of(host, user)
    if not ref:
        raise GuardError("cannot determine the Supabase project ref from the host / pooler user: refusing")
    if ref in old_refs_fn():
        raise GuardError("the target is the OLD shared Supabase project: refusing")
    if ref not in allowed_refs(env):
        raise GuardError("the target project ref is not in V2_ALLOWED_PROJECT_REFS: refusing")
    ssl = (parse_qs(u.query).get("sslmode") or [None])[0]
    if ssl not in ("require", "verify-ca", "verify-full"):
        raise GuardError("hosted targets must set sslmode=require (or verify-ca / verify-full) in the URL")
    return Target("supabase", host, u.port, db, user, ref, ssl)


# --------------------------------------------------------------------------------------------- fingerprint (on the live database)
def read_marker(conn) -> Dict[str, str]:
    try:
        return {r[0] if not isinstance(r, dict) else r["key"]: (r[1] if not isinstance(r, dict) else r["value"])
                for r in conn.execute("select key, value from public._setuai_env").fetchall()}
    except Exception as exc:                                  # table missing / unreadable
        try:
            conn.rollback()
        except Exception:
            pass
        raise GuardError(f"database fingerprint unreadable ({exc.__class__.__name__}): refusing") from exc


def verify_fingerprint(conn, target: Target) -> Dict[str, str]:
    """The database must say who it is, and it must be who this process was told it is."""
    m = read_marker(conn)
    if target.kind == "local":
        if m.get("env") != "integration":
            raise GuardError(f"local database marker env={m.get('env')!r}, expected 'integration'")
    else:
        if m.get("env") != "hosted" or m.get("schema") != SCHEMA_ID or m.get("project_ref") != target.ref:
            raise GuardError("hosted database fingerprint does not match the configured project: refusing")
    return m


def database_is_empty(conn) -> bool:
    """no user tables in public (a fresh Supabase project has none): the only state in which a marker may be stamped"""
    n = conn.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()
    return (n[0] if not isinstance(n, dict) else list(n.values())[0]) == 0
