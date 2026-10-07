# SMP-PIPE demo progress reports

Fictional demonstration data for **Siliguri-Mughalsarai Petroleum Products Pipeline (SMP-PIPE)**, built from its seeded schedule (real activity ids, measured quantities and approved progress as of 2026-10-04).
Site Engineers: Nirmali Saikia, Pranav Rao. Supervisors: Kabir Sarma. Regenerate and dry-run (read-only): `set -a; . .local/hosted.env; set +a; python3 scripts/make_demo_reports_project.py SMP-PIPE`.

Use each file or sentence **once** (the same item reported twice is kept once). Multi-item daily reports (`*_daily_report_*`, `*_site_progress_note_*`) need the language-model reader on the API.

## Files and what the dry run (real readers + real engine, ONNX backend, rule-based fallback for free text) produces

| File | Result |
|---|---|
| `SMP_daily_progress_2026-10-03.csv` | 8 claim(s): SMP-1010->SMP-1010 (EXACT_ID, 1.00); SMP-1020->SMP-1020 (EXACT_ID, 1.00); SMP-2000->SMP-2000 (EXACT_ID, 1.00); SMP-2010->SMP-2010 (EXACT_ID, 1.00); SMP-3110->SMP-3110 (EXACT_ID, 1.00); SMP-3120->SMP-3120 (EXACT_ID, 1.00); SMP-3130->SMP-3130 (EXACT_ID, 1.00); SMP-3140->SMP-3140 (EXACT_ID, 1.00) |
| `SMP_daily_report_2026-10-01.pdf` | 5 claim(s): SMP-1020->SMP-1020 (EXACT_ID, 1.00); SMP-2000->SMP-2000 (EXACT_ID, 1.00); SMP-2010->SMP-2010 (EXACT_ID, 1.00); SMP-3110->SMP-3110 (EXACT_ID, 1.00); SMP-3120->SMP-3120 (EXACT_ID, 1.00) |
| `SMP_daily_report_2026-10-02.txt` | 6 claim(s): SMP-1010->SMP-1010 (EXACT_ID, 1.00); SMP-1020->SMP-1020 (EXACT_ID, 1.00); SMP-2000->SMP-2000 (EXACT_ID, 1.00); SMP-2010->SMP-2010 (EXACT_ID, 1.00); SMP-3110->SMP-3110 (EXACT_ID, 1.00); SMP-3120->SMP-3120 (EXACT_ID, 1.00) |
| `SMP_discipline_progress_2026-09-30.xlsx` | 8 claim(s): SMP-1010->SMP-1010 (EXACT_ID, 1.00); SMP-1020->SMP-1020 (EXACT_ID, 1.00); SMP-2000->SMP-2000 (EXACT_ID, 1.00); SMP-2010->SMP-2010 (EXACT_ID, 1.00); SMP-3110->SMP-3110 (EXACT_ID, 1.00); SMP-3120->SMP-3120 (EXACT_ID, 1.00); SMP-3130->SMP-3130 (EXACT_ID, 1.00); SMP-3140->SMP-3140 (EXACT_ID, 1.00) |
| `SMP_field_note_1_2026-10-03.txt` | 1 claim(s): -->SMP-4010 (HYBRID_FALLBACK, 0.79) |
| `SMP_field_note_2_2026-10-02.pdf` | 1 claim(s): -->SMP-4100 (HYBRID_FALLBACK, 0.71) |
| `SMP_field_note_with_errors_2026-10-03.csv` | 1 claim(s): SMP-9999->unmatched (HYBRID_FALLBACK, 0.29) |
| `SMP_invalid_values_2026-10-03.csv` | refused as a whole file (clear message, no claim created) |
| `SMP_site_progress_note_2026-09-30.docx` | 4 claim(s): SMP-1010->SMP-1010 (EXACT_ID, 1.00); SMP-1020->SMP-1020 (EXACT_ID, 1.00); SMP-2000->SMP-2000 (EXACT_ID, 1.00); SMP-2010->SMP-2010 (EXACT_ID, 1.00) |

## Typed claims (Type Update)

| Text | Intended activity | Expectation |
|---|---|---|
| HDD crossing - Ganga at Patna commenced today: 7 m installed (HDD1_PILOT_M). | SMP-4010 | first report on a not-started activity (actual start) |
| Railway crossings (cased) commenced today: 2 nos installed (RAIL_XINGS). | SMP-4100 | first report on a not-started activity (actual start) |
| Siliguri Dispatch Terminal - civil works and foundations commenced today: 2 tonnes installed (ST1_REBAR_T). | SMP-5110 | first report on a not-started activity (actual start) |
| Siliguri Dispatch Terminal - electrical installation commenced today: 46 m installed (ST1_CABLE_M). | SMP-5130 | first report on a not-started activity (actual start) |
| Siliguri Dispatch Terminal - instrumentation and SCADA commenced today: 2 nos installed (ST1_LOOPS). | SMP-5140 | first report on a not-started activity (actual start) |
| HDD crossing - Ganga at Patna completed today: 1440 m installed (HDD1_PILOT_M). | SMP-4010 | far more than the baseline allows: above-baseline warning, Supervisor acknowledgement needed |
| Good progress on site today, all teams working well. | - | vague: no activity, no quantity, so it is not matched and goes to a Supervisor |

Blocked by an open issue (never offered for matching): none.
Photographs are evidence only (not read: no OCR on the deployed API).
Quantity claims on activities with several measured quantities no longer show the false 'no planned quantity' warning when the unit binds to a measured quantity.
