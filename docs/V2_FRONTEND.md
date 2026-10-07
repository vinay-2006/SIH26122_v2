# ANVYRA frontend - v2 integration (Project Manager, Site Engineer, Supervisor)

The existing frontend (`frontend/`, React + Vite + Tailwind + the app's own `components/ui`) now talks to the v2 backend (`backend/v2`) when it is started in **v2 mode**. Nothing was redesigned:
the v2 pages are built from the same shell, cards, tables, dialogs, pills, progress bars and field styles as the legacy pages (`src/v2/ui.tsx` only packages class strings that already exist).
**The default is unchanged**: without `--mode v2` the app is exactly the legacy demo against the legacy backend.

## How the two backends stay separate
| | legacy (default) | v2 |
|---|---|---|
| selected by | nothing (default) | `npm run dev:v2` / `npm run build:v2` (`vite --mode v2`) |
| API base | `VITE_API_BASE_URL` (`/api/v1`, `X-Project-ID` headers) | `VITE_V2_API_BASE_URL`, default `http://127.0.0.1:8020` (`/api/v2`) |
| browser storage keys | `supabase_access_token`, `setu_selected_project_id_v7`, ... | `setu_v2_access_token`, `setu_v2_selected_project` (never shared) |
| providers | `AuthProvider`, `ProjectProvider` | `AuthProviderV2`, `ProjectProviderV2` (same `useAuth()` / `useProjectState()` interfaces) |
| routes / menu | the original routes | `src/v2/routes.tsx` (guards are a data table) and `src/v2/nav.tsx` |

The mode is fixed when the dev server / build starts; the app never switches at run time and never redirects an existing user from one to the other. Old project ids, tokens and records cannot reach v2 and vice versa.

## Authentication and roles
* The browser holds **only a token**. Who you are (`GET /me`) and what you may do in the selected project (`my_role`, `my_permissions` from `GET /projects/{id}`) always come from the server;
  editing local storage, the URL or React state cannot make anyone a Project Manager. Hiding a menu item or a button is cosmetic: **every action is authorised again by the API** (role gate, domain service, database guard).
* Sign-in: Supabase Auth when `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` are set (the v2 API verifies Supabase tokens), otherwise the server's **local sign-in** (`POST /api/v2/auth/local-login`), which the operator must
  enable (`V2_LOCAL_LOGIN_PASSWORD`, 12+ characters), which only works against a LOCAL `setuai_v2_*` database and only for the seeded `@anvyra.demo` people. No secret, key or default password is in the frontend.
* A 401 anywhere ends the session and returns to the sign-in page. Role names: Project Manager, Supervisor, Site Engineer.

## What each role gets (all on the shared shell)
* **Project Manager** - *Portfolio* (every project, approved vs approximate planned progress, pending-claim COUNTS, issues and blockers, search and status filter, **New project**); *Overview*; *Schedule* (upload CSV / XER / MS Project XML,
  validation errors and warnings, mapping of unknown disciplines / units, WBS node types, reconciliation of renames / splits / merges / retirements, build, **activate with confirmation**, rollback with a reason, compare versions, import history);
  *WBS & Activities*; *Issues & Delays* (read-only); *Notifications*; *Audit Trail* (+ verify integrity); *Project Settings* (details, progress rules, members, invitations, archive / restore).
  A Project Manager never sees claim contents or evidence and has no approval controls (the routes do not exist for them and the API refuses).
* **Site Engineer** - *Submit Claim* (activity picker, per-resource quantities in the schedule's units, total-to-date or since-last, percent-only with an explicit "not converted" note, dates, remarks, evidence upload with progress, or file claims from a daily report),
  *My Claims* (own only; status, clarification, withdraw, answer, correct after rejection), *Issues & Delays* (report), *Notifications*, *Overview*, *WBS & Activities*. No schedule pages.
* **Supervisor** - *Review Queue*, claim detail (reported quantities, evidence download, automatic checks), match / bind tools, the **decision panel** (live preview, approve as reported, approve with changes, reject, ask a question; explicit method for
  percent-only claims; over-baseline acknowledgement; reported and approved figures side by side), *Issues & Delays* (resolve, root causes, lessons), *Audit Trail*, *Overview*, *WBS & Activities*, *Notifications*.

Pending, rejected, withdrawn and clarification-requested claims never change any progress figure; the UI never updates progress optimistically - it refetches after the server confirms.
Planned progress and SPI always carry the server's "approximation / not earned value" text, the data date and the weighting basis.

## Running it locally
```bash
# 1. backend + isolated, seeded database (creates/uses the LOCAL database setuai_v2_dev; the old demo database is never touched)
scripts/v2_dev_stack.sh up            # generates .local/v2_dev.env (git-ignored) with a throw-away signing key and sign-in password; starts the API on :8020
# 2. frontend in v2 mode
cd frontend && npm install && npm run dev:v2          # http://127.0.0.1:5190
# the sign-in password for the seeded people is the V2_LOCAL_LOGIN_PASSWORD value in .local/v2_dev.env (read it yourself; it is never printed)
scripts/v2_dev_stack.sh down | status | reset
```
The legacy demo keeps its own commands (`npm run dev` with its own `VITE_API_BASE_URL`); it is not started or changed by any of the above.

### Trying each role (seeded people, all `@anvyra.demo`)
| Role | Sign in as | Suggested walk-through |
|---|---|---|
| Project Manager | `anita.bora` (projects A and D), `rohit.menon` (B) | Portfolio -> New project -> Schedule: upload `tests/schedule_import/fixtures/nsp.csv` (+ `nsp_resources.csv`) -> build -> activate -> Project Settings -> add a member |
| Supervisor | `lakshmi.iyer` (B), `imran.hussain` (B, C) | Review Queue -> open a claim -> preview -> approve; try a percent-only claim, an over-baseline claim, reject, ask a question |
| Site Engineer | `arun.nair`, `sneha.pillai` (B), `tenzin.bhutia` (C) | Submit Claim -> pick an activity -> quantities -> evidence -> submit; My Claims -> withdraw / answer / correct |

## Tests
```bash
cd frontend
npm test                    # 56 unit/component tests (API client, auth, permissions, route guards, claim form, decision panel, mode isolation)
npm run typecheck:tests     # type-checks the tests (kept out of the production build on purpose)
npm run build && npm run build:v2
npm run e2e                 # ~28 real-browser scenarios against the real v2 API on a throw-away database (needs Chromium: E2E_CHROMIUM or the Playwright cache)
```
`npm run e2e` creates and empties ONLY the local database `setuai_v2_fe_e2e`, uses ports 8021 (API) and 5191 / 5192 (web), and stops everything afterwards.

## Known limitations
* No OCR / handwriting / photo reading and no LLM extraction (Phase 4). Reports are read only if CSV, XLSX, text or text-layer PDF.
* XER and MS Project XML import has been exercised on sample / synthetic exports only; native `.mpp` is not supported (the UI says so).
* Planned progress and SPI are linear approximations, never earned value.
* The v2 UI is English only (the legacy language switcher is still shown; v2 pages are not translated yet).
* Local sign-in is a development convenience (local database only). Hosted use needs Supabase Auth and the hosted-target opt-ins described in `db/HOSTED_SETUP.md`.
* Viewing a historical schedule version is read-only; claims are always filed against the active schedule.
