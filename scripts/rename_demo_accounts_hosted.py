#!/usr/bin/env python3
"""Rename the sign-in e-mail of the 15 hosted demo people from <handle>@seed.setuai.local to <handle>@anvyra.demo. Dry run unless --apply.

    set -a; . .local/hosted.env; set +a
    python3 scripts/rename_demo_accounts_hosted.py            # plan only
    python3 scripts/rename_demo_accounts_hosted.py --apply

Only the e-mail changes (through the Auth Admin API, marked confirmed). User ids, passwords, memberships and every record stay exactly as they are; the database trigger
sync_user_email copies the new address to profiles. Idempotent: people already on the new address are skipped. Stops at once if the project holds any user outside the two
demo domains. Reads back the result (Auth list and a read-only query of profiles). Never prints keys or passwords."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from backend.v2.seed.hosted import AdminClient, HostedSeedError, PEOPLE  # noqa: E402
from db import target_guard as tg  # noqa: E402

OLD, NEW = "seed.setuai.local", "anvyra.demo"


def plan(users, people):
    by = {(u.get("email") or "").lower(): u for u in users}
    foreign = sorted(e for e in by if not (e.endswith("@" + OLD) or e.endswith("@" + NEW)))
    if foreign:
        raise HostedSeedError(f"{len(foreign)} user(s) outside the demo domains exist: the rename stops")
    steps = []
    for h in people:
        old, new = f"{h}@{OLD}", f"{h}@{NEW}"
        if new in by:
            steps.append((h, "ALREADY_RENAMED", by[new]["id"]))
        elif old in by:
            steps.append((h, "RENAME", by[old]["id"]))
        else:
            steps.append((h, "MISSING", None))
    return steps


def main(argv) -> int:
    apply = "--apply" in argv
    url = os.environ["DB_V2_URL"]
    tg.check_target(url)
    admin = AdminClient(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    steps = plan(admin.list_users(), PEOPLE)
    for h, st, uid in steps:
        print(f"  {h:16s} {st:16s} {h}@{OLD} -> {h}@{NEW}")
    todo = [s for s in steps if s[1] == "RENAME"]
    missing = [s for s in steps if s[1] == "MISSING"]
    if missing:
        print(f"STOP: {len(missing)} expected account(s) are missing; nothing changed", file=sys.stderr)
        return 1
    if not apply:
        print(f"dry run: {len(todo)} account(s) would be renamed. Re-run with --apply.")
        return 0
    for h, _st, uid in todo:
        admin.update_user_email(uid, f"{h}@{NEW}")
    after = {(u.get("email") or "").lower() for u in admin.list_users()}
    ok_auth = all(f"{h}@{NEW}" in after for h in PEOPLE) and not any(e.endswith("@" + OLD) for e in after)
    with psycopg.connect(url.replace(":5432/", ":6543/"), connect_timeout=20, prepare_threshold=None) as c:
        c.read_only = True                                           # transaction-scoped: nothing persists on a pooled server connection
        prof = {r[0].lower() for r in c.execute("select email from public.profiles").fetchall()}
    ok_prof = all(f"{h}@{NEW}" in prof for h in PEOPLE) and not any(e.endswith("@" + OLD) for e in prof)
    print(f"renamed {len(todo)}; Auth read-back {'OK' if ok_auth else 'MISMATCH'}; profiles read-back {'OK' if ok_prof else 'MISMATCH'}")
    return 0 if (ok_auth and ok_prof) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
