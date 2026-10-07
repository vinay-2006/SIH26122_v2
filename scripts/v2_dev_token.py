#!/usr/bin/env python3
"""Mint a short-lived LOCAL development bearer token for a seeded demo person (HS256, signed with SUPABASE_JWT_SECRET from the environment).

    SUPABASE_JWT_SECRET=<the same local secret the API runs with> python3 scripts/v2_dev_token.py rohit.menon
    curl -H "Authorization: Bearer $(... v2_dev_token.py lakshmi.iyer)" http://127.0.0.1:8020/api/v2/projects

Only the fictional @anvyra.demo people exist; the secret is read from the environment and never printed. The API itself refuses to start
without a verification key, and it re-checks the user's profile and project membership on every request."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt  # noqa: E402

from backend.v2.seed.projects_spec import PEOPLE, user_id  # noqa: E402


def main(argv) -> int:
    if len(argv) != 2 or argv[1] not in PEOPLE:
        print("usage: v2_dev_token.py <" + "|".join(PEOPLE) + ">", file=sys.stderr)
        return 2
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret or len(secret) < 32:
        print("set SUPABASE_JWT_SECRET (at least 32 characters) to the local secret the API runs with", file=sys.stderr)
        return 2
    h = argv[1]
    now = dt.datetime.now(dt.timezone.utc)
    print(jwt.encode({"sub": str(user_id(h)), "email": f"{h}@anvyra.demo", "aud": "authenticated", "role": "authenticated", "iat": now, "exp": now + dt.timedelta(hours=8)}, secret, algorithm="HS256"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
