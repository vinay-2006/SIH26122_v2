"""
Hard safety guard for tests that touch a database.

Rule: a test that can reach a database may only run when ALL of these hold:
  1. SETUAI_ALLOW_DB_TESTS=1
  2. SETUAI_TEST_ENV=integration
  3. DATABASE_URL is set in the process environment (never inferred from .env)
  4. its host is loopback (or listed in SETUAI_INTEG_HOST_ALLOWLIST)
  5. its host is NOT a Supabase host and not the host in the repo's .env DATABASE_URL
  6. its database name contains "integ" or "test"
  7. the database carries the marker row public._setuai_env(key='env', value='integration')
Checks 1-6 are pure and unit-tested; 7 is a runtime read.
"""
from __future__ import annotations

import os
from typing import Iterable, List, Optional
from urllib.parse import urlparse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SHARED_HOST_MARKERS = ("supabase.com", "supabase.co", "pooler.supabase")


class GuardError(RuntimeError):
    pass


def _host_db(url: str):
    u = urlparse(url)
    return (u.hostname or "").lower(), (u.path or "").lstrip("/").lower()


def _dotenv_db_host(dotenv_path: Optional[str]) -> Optional[str]:
    if not dotenv_path or not os.path.exists(dotenv_path):
        return None
    try:
        from dotenv import dotenv_values
        val = dotenv_values(dotenv_path).get("DATABASE_URL")
    except Exception:
        return None
    return _host_db(val)[0] if val else None


def check_env(env: dict, dotenv_path: Optional[str] = None) -> List[str]:
    """Return the list of violated rules (empty == safe to run DB tests)."""
    problems: List[str] = []
    if env.get("SETUAI_ALLOW_DB_TESTS") != "1":
        problems.append("SETUAI_ALLOW_DB_TESTS=1 is not set")
    if env.get("SETUAI_TEST_ENV") != "integration":
        problems.append("SETUAI_TEST_ENV=integration is not set")
    url = env.get("DATABASE_URL", "")
    if not url:
        problems.append("DATABASE_URL is not set in the process environment")
        return problems
    host, db = _host_db(url)
    allow = {h.strip().lower() for h in env.get("SETUAI_INTEG_HOST_ALLOWLIST", "").split(",") if h.strip()}
    if host not in LOOPBACK and host not in allow:
        problems.append(f"database host {host!r} is not loopback/allow-listed")
    if any(m in host for m in SHARED_HOST_MARKERS):
        problems.append(f"database host {host!r} looks like the shared Supabase environment")
    shared_host = _dotenv_db_host(dotenv_path)
    if shared_host and host == shared_host and host not in LOOPBACK:
        problems.append("database host equals the DATABASE_URL host in the repo .env (shared dev DB)")
    if "integ" not in db and "test" not in db:
        problems.append(f"database name {db!r} must contain 'integ' or 'test'")
    return problems


def check_db_marker(conn) -> Optional[str]:
    """Return a problem string if the connected DB lacks the integration marker."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM public._setuai_env WHERE key = 'env'")
            row = cur.fetchone()
        val = (row["value"] if isinstance(row, dict) else row[0]) if row else None
    except Exception as exc:  # table missing, permission, etc.
        return f"integration marker table unreadable: {exc.__class__.__name__}"
    return None if val == "integration" else f"integration marker has value {val!r}"
