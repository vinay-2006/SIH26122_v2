#!/usr/bin/env python3
"""Additive HOSTED demo seed (separate from scripts/seed_v2.py, which stays local-only and unchanged).

    python3 scripts/seed_v2_hosted.py --check          # offline: validates the environment and the target guard; no network, no database
    python3 scripts/seed_v2_hosted.py --apply          # creates the Auth users (Admin API), the grants and the four demo projects; idempotent

Needs (environment only, never printed): DB_V2_URL, V2_ALLOW_HOSTED=1, V2_ALLOWED_PROJECT_REFS, V2_DENIED_PROJECT_REFS, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY, V2_HOSTED_DEMO_PASSWORD, V2_EVIDENCE_DIR (a directory used ONLY by the hosted seed, outside .local/v2_evidence). Run `python db/migrate.py apply` first."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.seed import hosted  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="offline preflight only")
    g.add_argument("--apply", action="store_true", help="seed the hosted database (network + database)")
    a = ap.parse_args(argv)
    try:
        out = hosted.preflight() if a.check else hosted.run()
    except hosted.HostedSeedError as e:
        print(f"hosted seed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
