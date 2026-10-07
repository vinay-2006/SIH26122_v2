#!/usr/bin/env python3
"""Deterministic four-project demo seed for the v2 schema (LOCAL database only).

    DB_V2_URL=postgresql://postgres@127.0.0.1:54329/setuai_v2_seed_dev python3 scripts/seed_v2.py            # seed an EMPTY local database (or verify if already seeded)
    ... scripts/seed_v2.py --verify                  # read-only: invariants + content digest
    ... scripts/seed_v2.py --report [--json]         # read-only: counts per project
    ... scripts/seed_v2.py --reset-local             # empty this LOCAL database, then seed

Refuses: a non-empty database that is not exactly the seeded state; any hosted / non-setuai_v2_* target; the old demo database (it is never read here).
Evidence files go to V2_EVIDENCE_DIR (default .local/v2_evidence)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.seed import runner  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", action="store_true", help="read-only: check invariants and the content digest")
    g.add_argument("--report", action="store_true", help="read-only: print the per-project report")
    g.add_argument("--reset-local", action="store_true", help="empty this LOCAL setuai_v2_* database first, then seed")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    try:
        if a.verify or a.report:
            rep = runner.verify(strict=a.verify)
        else:
            rep = runner.seed(reset=a.reset_local)
    except runner.SeedError as e:
        print(f"seed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rep, indent=1, default=str) if a.json else runner.format_report(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
