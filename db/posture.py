#!/usr/bin/env python3
"""Read-only security-posture verifier for a v2 database (local now, hosted later). Exit status 1 if any CHECK fails.

    DB_V2_URL=... python db/posture.py

It encodes the Data-API-off architecture: the backend is the only writer and the only reader of application tables. Database clients
(`anon`, `authenticated`) have NO write path at all; `authenticated` may only SELECT through row-level security (kept so a future
direct-client design remains possible); `anon` has nothing; functions are not callable by clients except the RLS helpers.
Nothing is changed; the whole run is one read-only transaction. Output contains object names only."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from db import target_guard as tg  # noqa: E402

RLS_HELPERS = {"is_project_member", "project_role_of", "can_read_claim", "can_read_claim_by_id", "is_pm", "is_supervisor_or_pm",
               "is_member_of_any_project", "shares_project_with"}
APPEND_ONLY = {"audit_logs", "planner_decisions", "approved_activity_progress", "approved_resource_progress"}
MIN_PG = 150000


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str = ""


def check_posture(conn) -> List[Finding]:
    q = lambda sql, *a: conn.execute(sql, a or None).fetchall()      # no params => the SQL is sent verbatim (literal % allowed)
    out: List[Finding] = []
    def add(name, bad, detail_if_bad=""):
        out.append(Finding(name, not bad, ", ".join(sorted(map(str, bad)))[:400] if bad else ""))
    cols = lambda row: list(row.values()) if isinstance(row, dict) else list(row)        # works with tuple and dict row factories
    r0 = lambda row: cols(row)[0]

    add("PostgreSQL 15 or newer", [] if conn.info.server_version >= MIN_PG else [conn.info.server_version])
    tables = [r0(r) for r in q("select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'")]
    add("row-level security enabled on every public table",
        [r0(r) for r in q("select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and not c.relrowsecurity")])
    add("anon has no privilege on any table / view / sequence",
        [r0(r) for r in q("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in ('r','v','S') "
                          "and (has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                          "or has_any_column_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))")])
    add("authenticated can only SELECT (no INSERT / UPDATE / DELETE / TRUNCATE / REFERENCES / TRIGGER)",
        [r0(r) for r in q("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in ('r','v') "
                          "and (has_table_privilege('authenticated', c.oid, 'INSERT') or has_table_privilege('authenticated', c.oid, 'UPDATE') "
                          "or has_table_privilege('authenticated', c.oid, 'DELETE') or has_table_privilege('authenticated', c.oid, 'TRUNCATE') "
                          "or has_table_privilege('authenticated', c.oid, 'REFERENCES') or has_table_privilege('authenticated', c.oid, 'TRIGGER') "
                          "or has_any_column_privilege('authenticated', c.oid, 'INSERT,UPDATE,REFERENCES'))")])
    add("authenticated has no sequence privilege",
        [r0(r) for r in q("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='S' "
                          "and case when c.relkind = 'S' then has_sequence_privilege('authenticated', c.oid, 'USAGE,SELECT,UPDATE') else false end")])
    add("no public function is executable by anon or PUBLIC",
        [r0(r) for r in q("select p.proname from pg_proc p where p.pronamespace='public'::regnamespace and (has_function_privilege('anon', p.oid, 'EXECUTE') "
                          "or has_function_privilege('public', p.oid, 'EXECUTE'))")])
    execs = {r0(r) for r in q("select p.proname from pg_proc p where p.pronamespace='public'::regnamespace and has_function_privilege('authenticated', p.oid, 'EXECUTE')")}
    add("authenticated may execute only the RLS helper functions", execs - RLS_HELPERS)
    add("every view runs with the caller's rights (security_invoker)",
        [r0(r) for r in q("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='v' "
                          "and not coalesce('security_invoker=true' = any(c.reloptions), false)")])
    add("every SECURITY DEFINER function pins its search_path",
        [r0(r) for r in q("select p.proname from pg_proc p where p.pronamespace='public'::regnamespace and p.prosecdef and not exists "
                          "(select 1 from unnest(coalesce(p.proconfig, '{}')) cfg where cfg like 'search_path=%')")])
    add("no row-level-security policy is USING (true) / WITH CHECK (true)",
        [".".join(map(str, cols(r))) for r in q("select tablename, policyname from pg_policies where schemaname='public' and (qual = 'true' or with_check = 'true')")])
    add("no write policy exists for database clients (the backend is the only writer)",
        [".".join(map(str, cols(r))) for r in q("select tablename, policyname from pg_policies where schemaname='public' and cmd <> 'SELECT'")])
    guarded = {r0(r) for r in q("select c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_proc p on p.oid = t.tgfoid "
                                "where not t.tgisinternal and p.proname = 'trg_append_only' and (t.tgtype & 24) = 24")}       # one trigger covering UPDATE and DELETE
    add("append-only tables reject UPDATE and DELETE (trigger present)", APPEND_ONLY - guarded)
    marker = {cols(r)[0]: cols(r)[1] for r in q("select key, value from public._setuai_env")} if "_setuai_env" in tables else {}
    add("environment fingerprint present", [] if marker.get("schema") == tg.SCHEMA_ID and marker.get("env") in ("integration", "hosted") else ["missing / wrong"])
    add("migration bookkeeping present and contiguous",
        [] if [r0(r)[:4] for r in q("select version from public.schema_migrations order by version")] == [f"{i:04d}" for i in range(1, r0(conn.execute("select count(*) from public.schema_migrations").fetchone()) + 1)] else ["gap or duplicate"])
    usage = {role: r0(conn.execute("select has_schema_privilege(%s, 'public', 'USAGE')", (role,)).fetchone()) for role in ("anon", "authenticated")}
    out.append(Finding("INFO schema USAGE for database clients (can be revoked if the Data API stays off for good)", True,
                       ", ".join(f"{k}={v}" for k, v in usage.items())))
    return out


def main() -> int:
    url = os.environ.get("DB_V2_URL")
    if not url:
        print("set DB_V2_URL", file=sys.stderr)
        return 2
    t = tg.check_target(url)
    with psycopg.connect(url, prepare_threshold=None) as conn:
        tg.verify_fingerprint(conn, t)
        conn.execute("set transaction read only")
        findings = check_posture(conn)
    bad = [f for f in findings if not f.ok]
    for f in findings:
        print(("PASS " if f.ok else "FAIL ") + f.check + (f"  -> {f.detail}" if f.detail else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
