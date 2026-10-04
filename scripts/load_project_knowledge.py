#!/usr/bin/env python3
"""Load the generated project knowledge for the four demo projects (additive, idempotent; never overwrites an entry a person wrote or edited).

    DB_V2_URL=postgresql://postgres@127.0.0.1:54329/setuai_v2_dev python3 scripts/load_project_knowledge.py            # LOCAL setuai_v2_* database
    ... scripts/load_project_knowledge.py --dry-run                                                                      # show what would be inserted / refreshed

A hosted project is refused unless --hosted is given (and then it is a dry run unless --apply is also given); loading into a hosted project is an explicitly approved step
(see docs/V2_DEPLOYMENT.md). The hosted target guard (allow-list, deny-list, TLS, fingerprint) applies exactly as for the migration runner."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
import psycopg.rows  # noqa: E402

from backend.v2.db import database_url  # noqa: E402
from backend.v2.seed import knowledge_content as kc, knowledge_load as kl  # noqa: E402
from db import target_guard as tg  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    ap.add_argument("--hosted", action="store_true", help="allow a hosted Supabase target (guarded; dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="with --hosted: really write")
    a = ap.parse_args(argv)
    try:
        url = database_url()
        t = tg.check_target(url)
    except (RuntimeError, tg.GuardError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    if t.kind != "local" and not a.hosted:
        print("refused: this loader only runs against a LOCAL setuai_v2_* database (a hosted project needs --hosted)", file=sys.stderr)
        return 1
    dry = a.dry_run or (t.kind != "local" and not a.apply)
    with psycopg.connect(url, autocommit=True, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        tg.verify_fingerprint(c, t)
        if dry:
            try:
                with c.transaction():
                    out = kl.load_all(c)
                    raise psycopg.errors.QueryCanceled("dry run")
            except psycopg.errors.QueryCanceled:
                pass
        else:
            out = kl.load_all(c)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
