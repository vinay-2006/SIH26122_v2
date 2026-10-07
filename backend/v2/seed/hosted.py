"""Additive HOSTED seed: the same four fictional demo projects, on a fresh Supabase project, with REAL Supabase Auth users.

How it differs from the local seed (which is untouched):
* Users are created ONLY through the supported Supabase Auth Admin API (never a direct write to the auth schema). The project's own trigger (migration 0001/0012)
  creates each profile; this module waits for it and refuses to continue if a profile does not appear.
* Everything after the users reuses the local seed's code unchanged (project creation, membership, schedule import/build/activate, claims, decisions,
  issues). The runner's "local only" guard is swapped, for the duration of the run, for a check that the target is the allow-listed hosted project.
* The three CREATE_PROJECT grants are the one privileged database write (same as the local bootstrap), done in one transaction.
* Idempotent and never partial: a marker row in public._setuai_env (key hosted_seed) goes 'started' -> 'complete'. 'complete' => only verify; 'started'
  without 'complete' (an interrupted run) => refuse, because the demo is written through many transactions and cannot be resumed safely. A database that
  already holds projects, or users other than the demo's, is refused. Nothing is ever deleted or reset on a hosted database.
* Evidence files (the schedule files, site reports, issue evidence) go to a DEDICATED directory: V2_EVIDENCE_DIR must be set explicitly and must not be, contain or
  sit inside the repository's default `.local/v2_evidence` (the local seed's directory, which `seed_v2.py --reset-local` empties for these same project ids).
* Secrets come from the environment only and are never printed: SUPABASE_URL (must be the SAME project as the database), SUPABASE_SERVICE_ROLE_KEY,
  V2_HOSTED_DEMO_PASSWORD (12+ characters, shared by the fictional demo users).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from db import target_guard as tg

from .. import storage
from ..db import close_pool, get_pool
from . import runner
from .projects_spec import DOMAIN, PEOPLE, all_projects, email

MARKER_KEY = "hosted_seed"
# where the LOCAL seed keeps its evidence when V2_EVIDENCE_DIR is not set (storage.default_root() without the environment)
LOCAL_EVIDENCE_DEFAULT = Path(storage.__file__).resolve().parents[2] / ".local" / "v2_evidence"
MIN_PASSWORD = 12


class HostedSeedError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ Supabase Auth Admin API
class AdminClient:
    """The only way users are created. `opener` is injectable so the logic is testable without a network."""

    def __init__(self, base_url: str, service_key: str, opener: Callable[..., Any] = urllib.request.urlopen, timeout: int = 20):
        self._base, self._key, self._open, self._timeout = base_url.rstrip("/"), service_key, opener, timeout

    def __repr__(self) -> str:                                   # never leaks the key
        return f"AdminClient({self._base})"

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        req = urllib.request.Request(f"{self._base}/auth/v1{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"apikey": self._key, "Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self._timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            raise HostedSeedError(f"Supabase Auth Admin API {method} {path.split('?')[0]} failed with HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise HostedSeedError(f"Supabase Auth Admin API unreachable ({e.reason.__class__.__name__})") from None

    def list_users(self) -> List[dict]:
        out: List[dict] = []
        page = 1
        while True:
            got = self._call("GET", f"/admin/users?page={page}&per_page=200")
            users = (got or {}).get("users", []) if isinstance(got, dict) else []
            out += users
            if len(users) < 200:
                return out
            page += 1

    def update_user_email(self, user_id: str, new_email: str) -> dict:
        """change only the sign-in e-mail (confirmed); the id, the password and every other attribute stay as they are. The database trigger sync_user_email copies it to profiles."""
        return self._call("PUT", f"/admin/users/{user_id}", {"email": new_email, "email_confirm": True})

    def create_user(self, email_: str, password: str, full_name: str) -> dict:
        return self._call("POST", "/admin/users", {"email": email_, "password": password, "email_confirm": True, "user_metadata": {"full_name": full_name}})


def ensure_auth_users(admin: AdminClient, password: str, log: Callable[[str], None] = print) -> Dict[str, str]:
    """handle -> auth user id. Existing users (matched by e-mail) are left exactly as they are; only missing ones are created. Safe to repeat."""
    if len(password or "") < MIN_PASSWORD:
        raise HostedSeedError(f"V2_HOSTED_DEMO_PASSWORD must be at least {MIN_PASSWORD} characters")
    existing = {(u.get("email") or "").lower(): u for u in admin.list_users()}
    foreign = sorted(e for e in existing if not e.endswith("@" + DOMAIN))
    if foreign:
        raise HostedSeedError(f"the Auth project already has {len(foreign)} user(s) outside the demo domain @{DOMAIN}; the hosted demo seed needs a project that holds only demo users")
    ids: Dict[str, str] = {}
    created = 0
    for handle, (name, _grants) in PEOPLE.items():
        e = email(handle).lower()
        u = existing.get(e)
        if u is None:
            u = admin.create_user(e, password, name)
            created += 1
        if not u or not u.get("id"):
            raise HostedSeedError(f"the Auth Admin API returned no user id for {handle}")
        ids[handle] = str(u["id"])
    log(f"auth users: {created} created, {len(PEOPLE) - created} already present")
    return ids


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def dedicated_evidence_dir(env: Dict[str, str]) -> Path:
    """V2_EVIDENCE_DIR, resolved; refused unless it is set and cannot collide with the local seed's evidence (which `--reset-local` deletes per project id)."""
    raw = (env.get("V2_EVIDENCE_DIR") or "").strip()
    if not raw:
        raise HostedSeedError("V2_EVIDENCE_DIR must be set explicitly to a directory used ONLY by the hosted seed (the default would be the local seed's evidence directory)")
    p = Path(raw).expanduser().resolve()
    local = LOCAL_EVIDENCE_DEFAULT.resolve()
    if _overlaps(p, local):
        raise HostedSeedError("V2_EVIDENCE_DIR overlaps the local seed's evidence directory (.local/v2_evidence): choose a separate, hosted-only directory outside it")
    if p.exists() and not p.is_dir():
        raise HostedSeedError("V2_EVIDENCE_DIR exists and is not a directory")
    return p


# ------------------------------------------------------------------------------------------------ preflight (no network, no database)
def preflight(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Every check that needs neither the network nor the database. Raises HostedSeedError; never prints values."""
    env = dict(os.environ) if env is None else env
    url = env.get("DB_V2_URL") or env.get("DATABASE_URL_V2")
    if not url:
        raise HostedSeedError("DB_V2_URL is not set")
    try:
        t = tg.check_target(url, env=env)
    except tg.GuardError as e:
        raise HostedSeedError(f"refusing database target: {e}") from None
    if t.kind != "supabase":
        raise HostedSeedError("the hosted seed only runs against a hosted Supabase target (use scripts/seed_v2.py for a local database)")
    api = env.get("SUPABASE_URL", "")
    host = (urlparse(api).hostname or "").lower()
    if urlparse(api).scheme != "https" or host != f"{t.ref}.supabase.co":
        raise HostedSeedError("SUPABASE_URL must be https://<ref>.supabase.co for the SAME project as the database target")
    if not env.get("SUPABASE_SERVICE_ROLE_KEY"):
        raise HostedSeedError("SUPABASE_SERVICE_ROLE_KEY is not set")
    if len(env.get("V2_HOSTED_DEMO_PASSWORD", "")) < MIN_PASSWORD:
        raise HostedSeedError(f"V2_HOSTED_DEMO_PASSWORD must be set (at least {MIN_PASSWORD} characters)")
    evidence = dedicated_evidence_dir(env)
    return {"project_ref": t.ref, "evidence_dir": str(evidence), "projects": [s.code for s in all_projects()], "users": len(PEOPLE),
            "grants": sum(len(g) for _, g in PEOPLE.values()), "email_domain": DOMAIN}


# ------------------------------------------------------------------------------------------------ database side
def _read_state(c) -> Optional[str]:
    r = c.execute("select value from public._setuai_env where key = %s", (MARKER_KEY,)).fetchone()
    return r["value"] if r else None


def _check_migrations(c) -> None:
    from db import migrate
    done = {r["version"]: r["checksum"] for r in c.execute("select version, checksum from public.schema_migrations").fetchall()}
    for f in sorted(migrate.MIGRATIONS.glob("*.sql")):
        if done.get(f.stem) != migrate.checksum(f):
            raise HostedSeedError(f"migration {f.stem} is not applied (or differs): run `python db/migrate.py apply` first")


def _wait_for_profiles(c, ids: Dict[str, str], seconds: int = 30) -> None:
    end = time.time() + seconds
    while True:
        have = {str(r["id"]): r["email"] for r in c.execute("select id, email from profiles where id = any(%s::uuid[])", (list(ids.values()),)).fetchall()}
        missing = [h for h, i in ids.items() if have.get(i, "").lower() != email(h).lower()]
        if not missing:
            return
        if time.time() > end:
            raise HostedSeedError(f"profiles were not created by the auth trigger for: {', '.join(missing)} (is migration 0001/0012 applied on this project?)")
        time.sleep(1)


def _grant(c, ids: Dict[str, str]) -> int:
    n = 0
    with c.transaction():
        c.execute("select set_config('app.system','on',true)")              # transaction-local: safe through a pooler
        for handle, (_name, grants) in PEOPLE.items():
            for g in grants:
                n += c.execute("insert into platform_grants (user_id, capability) values (%s,%s) on conflict do nothing", (ids[handle], g)).rowcount
    return n


class _HostedRunner:
    """swap the local seed's 'local only' guard and its fixed user ids for the hosted ones, for the duration of the run, then restore both"""

    def __init__(self, target: tg.Target, ids: Dict[str, str]):
        import uuid
        self._t, self._ids, self._uuid = target, ids, uuid
        self._saved: Dict[str, Any] = {}

    def __enter__(self):
        self._saved = {"local_target": runner.local_target, "user_id": runner.user_id}
        runner.local_target = lambda: self._t
        runner.user_id = lambda h: self._uuid.UUID(self._ids[h])
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(runner, k, v)


def _seed_projects(t: tg.Target, ids: Dict[str, str], evidence_dir: Path, log: Callable[[str], None]) -> Dict[str, Any]:
    """the shared seed code under the hosted substitutions; whatever happens, the pool is closed, the runner is restored and the evidence store is put back"""
    from ..auth import CurrentUser
    from . import history_plan as hp
    previous_store = storage._store
    storage.set_store(storage.LocalEvidenceStore(evidence_dir))
    try:
        with _HostedRunner(t, ids):
            try:
                get_pool()
                users = {h: CurrentUser(id=runner.user_id(h), email=email(h), full_name=PEOPLE[h][0], capabilities=set(PEOPLE[h][1])) for h in PEOPLE}
                for spec in all_projects():
                    ctx = runner.Ctx(spec, users)
                    ctx.plan = hp.build_plan(spec, runner.ANCHOR)
                    runner.create_baseline(ctx)
                    if spec.lifecycle != "UPCOMING":
                        runner.run_steps(ctx)
                        runner.run_issues(ctx)
                    else:
                        log(f"  {spec.code}: upcoming - no execution history by design")
                return runner.verify(strict=True)
            finally:
                close_pool()
    finally:
        storage.set_store(previous_store)


def run(env: Optional[Dict[str, str]] = None, admin: Optional[AdminClient] = None, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """preflight, then the seed; on every path (success, refusal, error) the connection pool is closed"""
    try:
        return _run(env, admin, log)
    finally:
        close_pool()


def _run(env: Optional[Dict[str, str]], admin: Optional[AdminClient], log: Callable[[str], None]) -> Dict[str, Any]:
    import psycopg
    import psycopg.rows
    from ..db import database_url

    env = dict(os.environ) if env is None else env
    info = preflight(env)
    t = tg.check_target(env.get("DB_V2_URL") or env["DATABASE_URL_V2"], env=env)
    admin = admin or AdminClient(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    with psycopg.connect(database_url(), autocommit=True, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        tg.verify_fingerprint(c, t)
        _check_migrations(c)
        state = _read_state(c)
        if state == "started":
            raise HostedSeedError("an earlier hosted seed run did not finish (marker 'started'); the demo cannot be resumed safely and nothing is deleted automatically. "
                                  "Use a fresh Supabase project, or have the project owner clean it up by hand")
        if state is None:
            n = c.execute("select (select count(*) from projects) p, (select count(*) from execution_events) e").fetchone()
            if n["p"] or n["e"]:
                raise HostedSeedError("the database already holds projects or claims that are not the hosted demo seed: refusing")
    ids = ensure_auth_users(admin, env["V2_HOSTED_DEMO_PASSWORD"], log)
    with psycopg.connect(database_url(), autocommit=True, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        _wait_for_profiles(c, ids)
        log("profiles: all demo users have a profile (created by the auth trigger)")
        if state == "complete":
            with _HostedRunner(t, ids):
                rep = runner.verify(strict=False)
            log("already seeded: verified only, nothing changed")
            return {**info, "state": "complete", "ok": rep.get("ok"), "problems": rep.get("problems", [])}
        if c.execute("insert into public._setuai_env (key, value) values (%s,'started') on conflict (key) do nothing", (MARKER_KEY,)).rowcount != 1:
            raise HostedSeedError("another hosted seed run is in progress or has run: refusing")
        log(f"grants: {_grant(c, ids)} CREATE_PROJECT grant(s) added")
    rep = _seed_projects(t, ids, Path(info["evidence_dir"]), log)
    with psycopg.connect(database_url(), autocommit=True, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        c.execute("update public._setuai_env set value = 'complete' where key = %s", (MARKER_KEY,))
    log("hosted demo seed complete and verified")
    return {**info, "state": "complete", "ok": rep.get("ok"), "problems": rep.get("problems", []), "digest": rep.get("digest")}
