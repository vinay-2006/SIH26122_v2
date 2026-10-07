#!/usr/bin/env python3
"""Checksummed, rerunnable migration runner for the clean schema in db/migrations.

    python db/migrate.py apply  [--with-shim] [--url URL]
    python db/migrate.py status [--url URL]

Safety (enforced before any statement is sent; rules live in db/target_guard.py):
  * the target must be a local setuai_v2_* database, or an explicitly allow-listed Supabase project (V2_ALLOW_HOSTED=1,
    V2_ALLOWED_PROJECT_REFS, TLS) whose own marker row matches; the legacy shared project is never allowed;
  * a host equal to the DATABASE_URL host in the repo's .env (the old shared DB) is ALWAYS refused;
  * a migration that was already applied but whose file changed is an error (migrations are immutable);
  * the database must carry a matching environment marker; only a brand-new, EMPTY database may be stamped.
The URL comes from --url or the DB_V2_URL environment variable. It never reads DATABASE_URL, so the old stack's
configuration cannot be picked up by accident. Credentials are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
SHIM = ROOT / "db" / "shim" / "000_supabase_shim.sql"
if str(ROOT) not in sys.path:                                  # running as `python db/migrate.py`: put the repo root on the path
    sys.path.insert(0, str(ROOT))
from db import target_guard as _tg  # noqa: E402

GuardError = _tg.GuardError
LOOPBACK = _tg.LOOPBACK
DB_PREFIX = _tg.DB_PREFIX


def _old_shared_host() -> str | None:
    return _tg.old_shared_host()


def check_target(url: str, env=None) -> tuple[str, str]:
    """Return (host, dbname) or raise GuardError. Local setuai_v2_* databases always; hosted Supabase only under the explicit
    allow-list rules in db/target_guard.py. `_old_shared_host` is looked up at call time (tests patch it)."""
    t = _tg.check_target(url, env=env, old_host_fn=lambda: _old_shared_host())
    return t.host, t.dbname


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


MIN_PG = 150000          # security_invoker views and column-list ON DELETE SET NULL need PostgreSQL 15


def require_min_pg(conn) -> None:
    if conn.info.server_version < MIN_PG:
        raise GuardError(f"PostgreSQL 15 or newer is required (server is {conn.info.server_version // 10000}.x)")


def ensure_bookkeeping(conn, target: _tg.Target) -> None:
    """Create bookkeeping + the environment marker. A LOCAL database is stamped env=integration. A HOSTED database is stamped
    env=hosted/project_ref=<ref> only when it is brand new (no tables in public at all); otherwise it must already carry a matching marker."""
    has_marker = conn.execute("select to_regclass('public._setuai_env') is not null").fetchone()[0]
    if has_marker:
        _tg.verify_fingerprint(conn, target)                      # raises if it is not the database we were told it is
    elif target.kind == "supabase":
        if not _tg.database_is_empty(conn):
            raise GuardError("hosted database has tables but no fingerprint marker: refusing to stamp or modify it")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS public._setuai_env (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if not has_marker:
        rows = [("env", "integration")] if target.kind == "local" else [("env", "hosted"), ("project_ref", target.ref)]
        for k, v in rows + [("schema", _tg.SCHEMA_ID)]:
            conn.execute("INSERT INTO public._setuai_env VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (k, v))
    conn.execute("ALTER TABLE public._setuai_env ENABLE ROW LEVEL SECURITY")
    conn.execute("ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY")


def apply(url: str, with_shim: bool) -> int:
    t = _tg.check_target(url, old_host_fn=lambda: _old_shared_host())
    if with_shim and t.kind != "local":
        raise GuardError("the local Supabase shim must never be applied to a hosted database")
    files = sorted(MIGRATIONS.glob("*.sql"))
    with psycopg.connect(url, autocommit=False, prepare_threshold=None) as conn:
        require_min_pg(conn)
        if with_shim:
            has_auth = conn.execute("SELECT to_regclass('auth.users') IS NOT NULL").fetchone()[0]
            if not has_auth:
                conn.execute(SHIM.read_text())
                conn.commit()
                print("applied local Supabase shim")
        ensure_bookkeeping(conn, t)
        conn.commit()
        done = dict(conn.execute("SELECT version, checksum FROM public.schema_migrations").fetchall())
        applied = 0
        for f in files:
            ver, cs = f.stem, checksum(f)
            if ver in done:
                if done[ver] != cs:
                    raise GuardError(f"migration {ver} was already applied but the file has changed (migrations are immutable)")
                continue
            try:
                conn.execute(f.read_text())
                conn.execute("INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)", (ver, cs))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"FAILED {ver}: {exc}", file=sys.stderr)
                return 1
            applied += 1
            print(f"applied {ver}")
        print(f"{t.dbname}@{t.kind}: {applied} applied, {len(files) - applied} already current")
    return 0


def status(url: str) -> int:
    t = _tg.check_target(url, old_host_fn=lambda: _old_shared_host())
    with psycopg.connect(url, prepare_threshold=None) as conn:
        marker = _tg.verify_fingerprint(conn, t)
        rows = conn.execute("SELECT version, checksum FROM public.schema_migrations ORDER BY version").fetchall()
    on_disk = {f.stem: checksum(f) for f in sorted(MIGRATIONS.glob("*.sql"))}
    applied = dict(rows)
    print(f"target {t.dbname}@{t.kind}  marker={ {k: v for k, v in marker.items() if k != 'project_ref'} }")
    for ver, cs in on_disk.items():
        state = "pending" if ver not in applied else ("ok" if applied[ver] == cs else "CHECKSUM MISMATCH")
        print(f"  {ver}: {state}")
    return 0 if all(applied.get(v) == c for v, c in on_disk.items()) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["apply", "status"])
    ap.add_argument("--url", default=os.environ.get("DB_V2_URL"))
    ap.add_argument("--with-shim", action="store_true", help="local only: create the Supabase auth shim first")
    a = ap.parse_args()
    if not a.url:
        print("set DB_V2_URL or pass --url", file=sys.stderr)
        return 2
    try:
        return apply(a.url, a.with_shim) if a.command == "apply" else status(a.url)
    except GuardError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
