#!/usr/bin/env python3
"""Create the PRIVATE evidence bucket (once), then verify it. Dry run unless --apply.

    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... V2_STORAGE_BUCKET=evidence python3 scripts/create_evidence_bucket.py [--apply]

* An existing bucket is never modified: if it exists it is only checked (must be private); if it is public the script stops.
* The bucket is created private with a 25 MB per-object ceiling (the largest file family the API accepts). No MIME allow-list is set because the API stores objects as
  application/octet-stream after checking the content itself; no Storage policy is created, so only the service-role key held by the API can read or write.
* It never prints the key and never writes an object."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2 import storage  # noqa: E402

LIMIT = 25 * 1024 * 1024


def main(argv) -> int:
    apply = "--apply" in argv
    import os
    bucket = os.environ.get("V2_STORAGE_BUCKET", "evidence")
    store = storage._supabase_from_env()                                   # validates URL (https), key presence and the bucket name
    try:
        info = json.loads(store._call(store._req("GET", f"/bucket/{bucket}")) or b"{}")
        if info.get("public"):
            print(f"STOP: bucket '{bucket}' exists and is PUBLIC; it is not modified. Decide how to proceed.", file=sys.stderr)
            return 1
        print(f"bucket '{bucket}' already exists and is private: nothing to create")
        return 0
    except storage.StorageError:
        pass                                                               # not found (or unreadable): creation below, if authorised
    if not apply:
        print(f"dry run: would create PRIVATE bucket '{bucket}' (file_size_limit {LIMIT} bytes, no policies). Re-run with --apply.")
        return 0
    body = json.dumps({"id": bucket, "name": bucket, "public": False, "file_size_limit": LIMIT}).encode()
    store._call(store._req("POST", "/bucket", body, {"Content-Type": "application/json"}))
    print("created:", store.verify_bucket())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
