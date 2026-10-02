# SIH v2 API (`backend/v2`)

A separate FastAPI app for the clean schema in `db/`. **The legacy app (`backend/main.py`) and its database are untouched.**
It reads `DB_V2_URL` (never `DATABASE_URL`) and refuses anything but a local `setuai_v2_*` database until hosted use is approved.

```bash
scripts/db_v2.sh up
DB_V2_URL="$(scripts/db_v2.sh url)" SUPABASE_JWT_SECRET=<local-secret> uvicorn backend.v2.app:app --port 8020
```

## Authorization model
* Identity: verified JWT. Authority: an ACTIVE `project_memberships` row, looked up per request (the URL's project id proves nothing).
* Roles: `PROJECT_MANAGER` (projects, settings, members, schedules), `SUPERVISOR` (read; claims/decisions arrive with the execution phase),
  `SITE_ENGINEER` (read; report/evidence upload only). `CREATE_PROJECT` / `PLATFORM_ADMIN` are separate, revocable platform grants.
* Every write transaction sets `app.actor_id`; the database then applies its own role guards (see `db/README.md`). Nothing here sets `app.system`.

## Endpoints (`/api/v2`)
| | |
|---|---|
| `GET /me`, `GET/POST /projects`, `GET/PATCH /projects/{id}`, `POST …/archive`, `POST …/restore` | create needs `CREATE_PROJECT`; patch/archive PM |
| `GET/PATCH /projects/{id}/settings` | PM |
| `GET/POST /projects/{id}/members`, `PATCH/DELETE …/members/{user_id}` | list PM+Supervisor; write PM (Site Engineer / Supervisor seats only) |
| `GET/POST /projects/{id}/invitations`, `DELETE …/{id}`, `POST /invitations/accept` | PM; accept by the invited user |
| `POST/DELETE /platform/grants…` | `PLATFORM_ADMIN` |
| `POST /projects/{id}/schedule-imports` (multipart: `file`, optional `resources_file`, header fields) | PM — stages: parse + validate, **writes nothing if invalid** |
| `GET /…/schedule-imports/{iid}` | report, WBS proposals, reconciliation preview |
| `PUT /…/schedule-imports/{iid}/decisions` | mapping (`discipline_map`, `uom_map`, `wbs_types`, `header`) and `reconcile` decisions |
| `POST /…/schedule-imports/{iid}/build` | atomic: DRAFT version + rows + lineage, then VALIDATED |
| `GET /…/schedule-versions`, `…/{vid}`, `…/{vid}/wbs`, `…/{vid}/activities`, `…/compare?old&new` | members read (compare: PM) |
| `POST /…/schedule-versions/{vid}/activate` | PM — locks; supersedes the previous; a superseded version needs `reason` (rollback) |
| `DELETE /…/schedule-versions/{vid}` | PM — unlocked drafts only |
| `POST /projects/{id}/documents` (multipart `kind`, `file`) | **Site Engineer only**; schedule-shaped files → 422 |

## Formats
Primavera `.xer`, Microsoft Project XML (MSPDI), CSV (+ resource CSV). **Native `.mpp` is not supported** (the error tells the user to export XML).
