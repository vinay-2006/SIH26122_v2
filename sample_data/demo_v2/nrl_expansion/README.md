# NRL-EXPANSION demo progress reports

Fictional demonstration data for the ANVYRA demo project **Numaligarh Refinery Expansion (NRL-EXPANSION)**, built from the seeded schedule (real activity ids, measured
quantities, baselines and approved progress as of 2026-10-04). Every figure is invented. Each file is under 60 KB; the deployed upload limit is about 4 MB.

Regenerate: `python3 scripts/make_demo_reports_v2.py`. Dry-run every file and typed claim against the hosted schedule with the real readers and the real matching engine
(read-only, writes nothing): `set -a; . .local/hosted.env; set +a; python3 scripts/check_demo_reports_v2.py`. The results below come from that run (ONNX backend).

**Who uploads:** the Site Engineer (Ritu Baruah) on *Claim Intake*. The Supervisors (Imran Hussain, Meera Das) then review. Each file or sentence can be used **once**: the same item reported twice is kept once.

## 1. Use these in the live demo (they match reliably on the deployed stack)

| File | Upload tab | What it contains | Expected result |
|---|---|---|---|
| `NRL_daily_progress_2026-10-03.csv` | Upload Progress Report | 8 structured rows (steel, vessels, electrical, instruments, insulation, piping, DCS) | 8 claims, all matched by activity id (EXACT_ID, confidence 1.00): NRE-2020, 2030, 2060, 2070, 2080, 4050, 4060, 7020. They are cumulative-percentage claims (the structured reader uses the Progress column). |
| `NRL_discipline_progress_2026-09-30.xlsx` | Upload Progress Report | 4 discipline sheets (Piping, Electrical, Instrumentation, Structural), 7 rows | 7 claims, all EXACT_ID 1.00: NRE-4050, 2080, 2060, 4060, 2070, 7020, 2020 |
| `NRL_daily_report_2026-10-02.txt` | Upload Progress Report | multi-item daily report (6 items: vessel, steel, spools, cable tray, a work start, DCS cabinet) | **needs LLM extraction on the API**: 6 claims, all EXACT_ID 1.00 (NRE-2030, 2020, 4050, 4060, 4070, 7020) |
| `NRL_daily_report_2026-10-01.pdf` | Upload Progress Report | multi-item text-layer PDF (5 items) | **needs LLM extraction**: 5 claims, all EXACT_ID 1.00 (NRE-2060, 2070, 2080, 4060, 2030) |
| `NRL_site_progress_note_2026-09-30.docx` | Upload Progress Report | multi-item Word note (4 items) | **needs LLM extraction**: 4 claims, all EXACT_ID 1.00 (NRE-2020, 4050, 4060, 7020) |
| `NRL_field_note_HDT_tray_2026-10-02.txt` | Upload Progress Report | one field note: 175 m cable tray, hydrotreater | 1 claim, NRE-4060, confidence about 0.77 (text match), quantity 175 m |
| `NRL_field_note_HDT_spools_2026-10-01.pdf` | Upload Progress Report | one text-layer PDF note: 9 spools | 1 claim, NRE-4050, about 0.77, quantity 9 spools |
| `NRL_field_note_HDT_joints_2026-09-30.docx` | Upload Progress Report | one Word note: 95 joints welded | 1 claim, NRE-4050, about 0.76, quantity 95 joints |
| `NRL_site_photo_*.jpg` (2) | Single File (evidence photo) or attach to a typed claim | synthetic site images | stored as evidence (hashed). They are **not read**: there is no OCR on the deployed API. |

### Typed claims for "Type Update" (verified with the real reader and engine)

| # | Text to type | Expected |
|---|---|---|
| 1 | Crude Distillation Unit 2 electrical cabling and power distribution: 2 panels installed today (CDU2_PANELS_NOS). | NRE-2060, about 0.79 |
| 2 | Hydrotreater process piping fabrication and erection: 110 joints welded today (HDT_PIPING_JOINTS). | NRE-4050, about 0.77 |
| 3 | Hydrotreater electrical cabling and power distribution: 150 m of cable tray installed today (HDT_TRAY_M). | NRE-4060, about 0.70 |
| 4 | Hydrotreater instrumentation installation and loop checking commenced today: 300 m of instrument cable laid (HDT_INSTR_CABLE_M). | NRE-4070, about 0.70; first report on a not-started activity (actual start) |
| 5 | Hydrotreater electrical cabling and power distribution: 60 panels installed today (HDT_PANELS_NOS). | NRE-4060, about 0.70; more panels (40 + 60) than the 74 in the baseline: above-baseline warning and a Supervisor acknowledgement |
| 6 | Hydrotreater process piping fabrication and erection: 8 tonnes of pipe material received today. | NRE-4050, about 0.81; wrong unit for the activity (it measures joints and spools) |
| 7 | Good progress on site today, all teams working well. | not matched (0.02); goes to a Supervisor |

Attach one of the photographs to claim 2 or 3 to show evidence handling.

## 2. Files that show the checks (use after the clean ones)

`NRL_field_note_with_errors_2026-10-03.csv` — 5 rows that read correctly but should not pass quietly:

| Row | Expected |
|---|---|
| NRE-9999, an id that is not in the schedule | not matched (0.40, ambiguous) |
| NRE-4040, an activity that is already complete | not matched (0.39) |
| NRE-2050, blocked by an open issue | not offered for matching; the engine guesses NRE-4050 at 0.64 by text, which a Supervisor should reject |
| NRE-2090, hydrotest before piping is finished | matched (EXACT_ID 1.00); the Supervisor judges the sequence |
| NRE-4060 reporting 40% when 54% is already approved | matched (EXACT_ID 1.00); progress below what is approved |

`NRL_invalid_values_2026-10-03.csv` — a percentage of 135 is impossible: the **whole file is refused** with a clear message and no claim is created.

## 3. What to expect from the checks (be ready to explain)

* **"No planned quantity" warning.** A claim on an activity with *more than one* measured quantity (most CDU2 and hydrotreater activities, including NRE-4050) gets
  `VAL_UNSUPPORTED_ACCUMULATION`: "…has no planned quantity; incremental accumulation cannot be baseline verified." This is **not** a schedule-data fault: the activity has
  planned quantities (for example 18,998 joints). The legacy read model exposes a single planned quantity only when an activity has exactly one measured quantity. It is a known
  false positive of the compatibility layer. It only applies to **quantity** claims (typed claims and one-item notes) on multi-measure activities; the percentage claims that
  come from the CSV/XLSX files do not trigger it.
* **Blocked activities** (NRE-2050, NRE-7010) are excluded from matching by design while an issue blocks them.
* **Free-text reports with many items** (the three `NRL_daily_report_*` / `NRL_site_progress_note_*` files) need the **language-model reader**. Without a key the rule-based reader cannot
  read two-part activity ids such as `NRE-2030` and gives every item the whole document as its text (they then match the wrong activity). With the model enabled on the API they all match
  at 1.00. The structured CSV/XLSX, the one-item notes and the typed claims work in both modes.
* **Photographs and scans** are stored as evidence but are not read (no OCR on Vercel).
* The CSV/XLSX "Progress" column becomes a cumulative-percentage claim; the quantity columns are context.

## 4. Order for a smooth demo
1. Sign in as the Site Engineer → upload `NRL_daily_progress_2026-10-03.csv` → 8 claims appear, matched at 1.00.
2. Type claim 2 with a photograph attached.
3. Upload `NRL_field_note_with_errors_2026-10-03.csv` to show unmatched, ambiguous and blocked cases.
4. Sign in as a Supervisor → Review Workspace: approve a clean claim, acknowledge the above-baseline claim (typed claim 5), reject the blocked-activity guess.
5. Project Manager: Overview/Dashboard now show the movement; Lessons Radar and Root Cause & Memory for context.
