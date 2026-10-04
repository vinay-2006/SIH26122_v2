#!/usr/bin/env python3
"""Deploy-time check of the evidence bucket (read-only): the Supabase Storage bucket named by V2_STORAGE_BUCKET exists and is PRIVATE.

    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... V2_STORAGE_BUCKET=evidence python3 scripts/check_storage.py

It never creates a bucket and never writes an object. Create the bucket (private) in the Supabase dashboard first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2 import storage  # noqa: E402


def main() -> int:
    try:
        info = storage._supabase_from_env().verify_bucket()
    except storage.StorageError as e:
        print(f"storage check failed: {e}", file=sys.stderr)
        return 1
    print(f"ok: bucket '{info['bucket']}' exists and is private")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
