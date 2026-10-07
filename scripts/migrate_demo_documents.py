#!/usr/bin/env python3
"""Put the hash-verified bytes of the demo documents that the database labels LOCAL into the private evidence bucket, under their EXISTING keys.

    set -a; . .local/hosted.env; set +a
    python3 scripts/migrate_demo_documents.py            # report only: the hash-based mapping of every database row to the local files; nothing is uploaded
    python3 scripts/migrate_demo_documents.py --apply    # upload the verified matches (never overwrites; verifies each by downloading it)

Design: docs/V2_STORAGE_MIGRATION.md (option A). The database is only READ (read-only transaction); no row is created, changed or deleted. A row whose file is not
available locally is reported UNAVAILABLE and left exactly as it is. Only a file whose sha256 AND size equal the row is ever uploaded. Output has ids, keys and hashes,
never contents or secrets."""
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from backend.v2 import storage  # noqa: E402
from backend.v2.seed.hosted import dedicated_evidence_dir  # noqa: E402
from db import target_guard as tg  # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def mapping(rows, root: Path):
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[sha(p.read_bytes())] = files.get(sha(p.read_bytes()), []) + [p]
    out = []
    for r in rows:
        p = root / r["storage_path"] if r["storage_path"] else None
        status, note = "UNAVAILABLE", "no local file with this hash"
        if p and p.is_file():
            b = p.read_bytes()
            if sha(b) == r["sha256"] and len(b) == r["size_bytes"]:
                status, note = "MATCHED", "file at the recorded key; hash and size equal the row"
            else:
                status, note = "MISMATCH", f"file at the recorded key has sha256 {sha(b)[:12]}… / {len(b)} bytes, the row says {r['sha256'][:12]}… / {r['size_bytes']}"
        elif r["sha256"] in files:
            status, note = "MATCHED_BY_HASH", f"no file at the recorded key, but {files[r['sha256']][0].relative_to(root)} has the row's hash"
        out.append({**r, "status": status, "note": note, "path": (p if status == "MATCHED" else (files[r["sha256"]][0] if status == "MATCHED_BY_HASH" else None))})
    return out


def main(argv) -> int:
    apply = "--apply" in argv
    url = os.environ.get("DB_V2_URL", "")
    tg.check_target(url)
    root = dedicated_evidence_dir(dict(os.environ))
    with psycopg.connect(url, connect_timeout=15, row_factory=psycopg.rows.dict_row) as c:
        c.execute("set transaction read only")                       # transaction-local: a session-level SET would leak to other clients behind a transaction pooler
        rows = c.execute("select d.document_id::text, p.project_code, d.kind, d.file_name, d.storage_path, d.storage_backend, d.sha256, d.size_bytes from source_documents d "
                         "join projects p using (project_id) order by p.project_code, d.uploaded_at, d.document_id").fetchall()
    local = [r for r in rows if r["storage_backend"] == "LOCAL"]
    print(f"{len(rows)} documents in the database, {len(local)} labelled LOCAL; local evidence directory holds {sum(1 for p in root.rglob('*') if p.is_file())} files")
    m = mapping(local, root)
    for r in m:
        print(f"  {r['document_id']}  {r['project_code']:13s} {r['kind']:13s} {r['file_name'][:34]:34s} {r['status']:15s} {r['note']}")
    counts = {}
    for r in m:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("summary:", json.dumps(counts))
    if not apply:
        print("report only: nothing uploaded. Re-run with --apply to upload the MATCHED / MATCHED_BY_HASH files.")
        return 0
    store = storage._supabase_from_env()
    store.verify_bucket()                                                   # exists and private, or stop
    done = {}
    for r in m:
        if r["status"] not in ("MATCHED", "MATCHED_BY_HASH"):
            continue
        data = r["path"].read_bytes()
        key = r["storage_path"]
        if store.exists(key):
            res = "ALREADY_PRESENT" if sha(store.read(key)) == r["sha256"] else "STOP_DIFFERENT_OBJECT_AT_KEY"
            if res.startswith("STOP"):
                print(f"STOP: {r['document_id']} {key}: an object with different content already exists; nothing further is uploaded", file=sys.stderr)
                return 1
        else:
            store.put_at(key, data)
            res = "UPLOADED_VERIFIED" if sha(store.read(key)) == r["sha256"] else None
            if res is None:
                print(f"STOP: {r['document_id']} {key}: the uploaded object does not read back with the expected hash", file=sys.stderr)
                return 1
        done[res] = done.get(res, 0) + 1
        print(f"  {r['document_id']}  {key}  {res}")
    print("upload summary:", json.dumps(done))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
