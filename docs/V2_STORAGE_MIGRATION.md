# Moving the 20 demo documents to Supabase Storage — design only

**Status: option A executed on 2026-10-04 (approved).** Result below. Design kept for the record.

## What exists

* The hosted demo seed wrote 20 `source_documents` rows (schedule files, site reports, issue evidence) for the four projects. Each row has `storage_backend = 'LOCAL'` and `storage_path = <project uuid>/<2 hex>/<32 hex>.<ext>`, and `sha256`, `size_bytes`.
* The bytes sit in the seeding machine's `V2_EVIDENCE_DIR` (`~/.setuai-hosted-evidence`). That directory currently holds **16 files** (each a few hundred bytes), not 20: some rows share content or their file is not there. The migration therefore must reconcile row ↔ file by `sha256`, and report what it cannot match instead of guessing.
* A deployed API cannot read that directory. On Vercel those 20 downloads would answer "stored file is missing".
* `trg_documents_immutable` (migration 0014) forbids changing `storage_backend` and any non-null `storage_path`. That immutability is a feature (evidence cannot be silently repointed) and the design does not weaken it.
* `SupabaseEvidenceStore` keys have exactly the local key shape, so the same opaque key can name an object in the bucket.

## Options

| | A. Upload under the same keys + read-through (recommended) | B. Relocation table (migration 0022) | C. Re-seed the hosted demo |
|---|---|---|---|
| database change | none | one additive append-only table `document_relocations(document_id, project_id, to_backend, to_key, sha256, relocated_at, relocated_by)` | destructive |
| immutable rows touched | no | no | rows replaced |
| row still says | `LOCAL` (a legacy label; the bytes are served from the bucket) | `LOCAL`, with a relocation row saying where the bytes now are | `SUPABASE` |
| reversible | delete the objects | tombstone the relocation row | no |
| cost | upload + a small store change | migration, RLS, read-path join, tests | loses demo history |

**Recommendation: A for the demo; B only if an auditor must be able to see where each document's bytes moved.** C is rejected (it discards the demo history and audit chain).

## Option A in detail

1. **Store change (local, tested before any hosted step).** When `V2_STORAGE_BACKEND=supabase`, `store_for("LOCAL")` returns a read-only *fallback store*: try the local directory, and if the file is not there read the same key from the bucket. Writes never go to local. A missing object still answers "stored file is missing". The sha256 recorded in the row is verified against the bytes on every read (new check, cheap).
2. **Migration script `scripts/migrate_demo_documents.py` (new, dry-run by default):**
   * Preconditions, all enforced: the hosted target guard passes for the ANVYRA ref only; `scripts/check_storage.py` shows the bucket exists and is PRIVATE; `V2_EVIDENCE_DIR` is the dedicated hosted-seed directory.
   * Read the 20 rows (`document_id, project_id, storage_path, sha256, size_bytes`) with a read-only role.
   * For each row find the local file at `storage_path`; recompute sha256 and size; they must equal the row. Anything else is reported as `MISSING` or `MISMATCH` and is **not** uploaded.
   * Upload to the bucket at the same key with `x-upsert: false` (so a second run cannot overwrite), then download it and compare sha256 (`UPLOADED_VERIFIED`). An existing object whose hash matches is `ALREADY_PRESENT` (idempotent, resumable).
   * Print a table: row, key, size, result. `--apply` is required for any upload; the run stops on the first failure.
   * No database write at all, so no audit entry is needed; the script writes its own log file (keys, hashes, results, never contents or secrets).
3. **Acceptance:** 20/20 rows `UPLOADED_VERIFIED` or `ALREADY_PRESENT`, or each exception explained; from the deployed API, each document downloads and its sha256 equals the row; a project member of another project still gets 404.
4. **Rollback:** delete the uploaded objects by key (the log lists them). Nothing else changed.

## Decisions needed at approval time
1. Option A or B.
2. For rows whose file is missing from the 16 on disk: accept the report and leave them unavailable, or re-create the missing demo files from the seed (the seed is deterministic) and upload those.
3. Bucket name (default `evidence`) and region (same as the database).

## Not done / not authorized yet
Create the bucket; upload any object; read the hosted `source_documents` rows; change any hosted row.


## Executed (option A)

* Bucket `evidence` created **private** (25 MB per object, no Storage policies, RLS on `storage.objects`): the anonymous public URL, the anon key on the authenticated route, and the bucket list for the anon key all return nothing.
* Hash-based mapping of the 20 `source_documents` rows against the 16 files in the seeding machine's evidence directory:
  * **16 MATCHED** (file at the recorded key; sha256 and size equal the row): the site reports and issue reports (`EVIDENCE` / `ISSUE_REPORT`) of AEC-OFFSHORE (5), NNB-CRUDE (1) and NRL-EXPANSION (10) — uploaded under the same keys, each read back and hash-verified (`UPLOADED_VERIFIED` ×16; a second run: `ALREADY_PRESENT` ×16).
  * **4 UNAVAILABLE**: the four baseline schedule files. Their rows have `storage_path = NULL`: the seed (and every schedule import) keeps only the parsed content and the file's fingerprint, never the original file. They were not recreated, fabricated, changed or deleted.

| Unavailable document_id | Project | Kind | File name | Size |
|---|---|---|---|---|
| `26881099-34ef-4144-ab6b-716188a2c312` | AEC-OFFSHORE | SCHEDULE_FILE | aec-offshore_baseline.csv | 17,451 B |
| `d4039ee7-7c4b-4ec9-8705-04812824be75` | NNB-CRUDE | SCHEDULE_FILE | nnb-crude_baseline.csv | 21,084 B |
| `e3bc4fcb-f711-467a-a6ce-c0fc88a80f1e` | NRL-EXPANSION | SCHEDULE_FILE | nrl-expansion_baseline.csv | 10,734 B |
| `d3450e9e-199e-460e-9563-c2dd598bcb6b` | SMP-PIPE | SCHEDULE_FILE | smp-pipe_baseline.csv | 16,527 B |

* No database row was written by the migration (source_documents is unchanged; the script only reads, in a read-only transaction).
* Read-through (`storage.ReadThroughLocalStore`): with `V2_STORAGE_BACKEND=supabase` and an **empty** local disk, all 16 LOCAL-labelled documents read from the bucket and match their recorded sha256; the 4 without a stored file answer `404 CONTENT_UNAVAILABLE`.
* UI: documents carry `file_available`; a schedule import carries `original_file_available`, and the Staged import panel shows "Original file not retained · parsed content kept" when it is false. No screen offers to open a file that does not exist.
