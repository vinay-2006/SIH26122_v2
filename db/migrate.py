#!/usr/bin/env python3
"""Checksummed, rerunnable migration runner for the clean schema in db/migrations.

    python db/migrate.py apply  [--with-shim] [--url URL]
    python db/migrate.py status [--url URL]

Safety (enforced before any statement is sent):
  * the target must be loopback with a database named setuai_v2_*  (hosted targets are a separate, explicitly approved phase);
  * a host equal to the DATABASE_URL host in the repo's .env (the old shared DB) is ALWAYS refused;
  * a migration that was already applied but whose file changed is an error (migrations are immutable);
  * the database must carry the environment marker row, created here for local targets only.
The URL comes from --url or the DB_V2_URL environment variable. It never reads DATABASE_URL, so the old stack's
configuration cannot be picked up by accident. Credentials are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
SHIM = ROOT / "db" / "shim" / "000_supabase_shim.sql"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DB_PREFIX = "setuai_v2_"


class GuardError(RuntimeError):
    pass


def _old_shared_host() -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return (urlparse(line.split("=", 1)[1].strip().strip('"').strip("'")).hostname or "").lower() or None
    return None


def check_target(url: str) -> tuple[str, str]:
    """Return (host, dbname) or raise GuardError."""
    u = urlparse(url)
    host, db = (u.hostname or "").lower(), (u.path or "").lstrip("/").lower()
    if not host or not db:
        raise GuardError("database URL must include host and database name")
    old = _old_shared_host()
    if old and host == old:
        raise GuardError("target host equals the old shared database host: refusing")
    if "supabase" in host:
        raise GuardError("hosted Supabase targets are not allowed from this runner yet (separate approved phase)")
    if host not in LOOPBACK:
        raise GuardError(f"target host {host!r} is not loopback")
    if not db.startswith(DB_PREFIX):
        raise GuardError(f"database name {db!r} must start with {DB_PREFIX!r}")
    return host, db


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_bookkeeping(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public._setuai_env (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO public._setuai_env VALUES ('env','integration') ON CONFLICT (key) DO NOTHING")
    conn.execute("INSERT INTO public._setuai_env VALUES ('schema','sih-v2-baseline') ON CONFLICT (key) DO NOTHING")
    conn.execute("ALTER TABLE public._setuai_env ENABLE ROW LEVEL SECURITY")
    conn.execute("ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY")


def apply(url: str, with_shim: bool) -> int:
    host, db = check_target(url)
    files = sorted(MIGRATIONS.glob("*.sql"))
    with psycopg.connect(url, autocommit=False) as conn:
        if with_shim:
            has_auth = conn.execute("SELECT to_regclass('auth.users') IS NOT NULL").fetchone()[0]
            if not has_auth:
                conn.execute(SHIM.read_text())
                conn.commit()
                print("applied local Supabase shim")
        ensure_bookkeeping(conn)
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
        print(f"{db}@{host}: {applied} applied, {len(files) - applied} already current")
    return 0


def status(url: str) -> int:
    host, db = check_target(url)
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT version, checksum FROM public.schema_migrations ORDER BY version").fetchall()
        marker = conn.execute("SELECT key, value FROM public._setuai_env ORDER BY key").fetchall()
    on_disk = {f.stem: checksum(f) for f in sorted(MIGRATIONS.glob("*.sql"))}
    applied = dict(rows)
    print(f"target {db}@{host}  marker={dict(marker)}")
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
