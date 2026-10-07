# AEC-OFFSHORE demo progress reports

Fictional demonstration data for **Andaman and East Coast Offshore Drilling Campaign (AEC-OFFSHORE)**, built from its seeded schedule (real activity ids, measured quantities and approved progress as of 2026-10-04).
Site Engineers: Arun Nair, Sneha Pillai. Supervisors: Imran Hussain, Lakshmi Iyer. Regenerate and dry-run (read-only): `set -a; . .local/hosted.env; set +a; python3 scripts/make_demo_reports_project.py AEC-OFFSHORE`.

Use each file or sentence **once** (the same item reported twice is kept once). Multi-item daily reports (`*_daily_report_*`, `*_site_progress_note_*`) need the language-model reader on the API.

## Files and what the dry run (real readers + real engine, ONNX backend, rule-based fallback for free text) produces

| File | Result |
|---|---|
| `AEC_daily_progress_2026-10-03.csv` | 8 claim(s): OSD-2297->OSD-2297 (EXACT_ID, 1.00); OSD-2296->OSD-2296 (EXACT_ID, 1.00); OSD-2310->OSD-2310 (EXACT_ID, 1.00); OSD-2320->OSD-2320 (EXACT_ID, 1.00); OSD-2330->OSD-2330 (EXACT_ID, 1.00); OSD-2340->OSD-2340 (EXACT_ID, 1.00); OSD-2350->OSD-2350 (EXACT_ID, 1.00); OSD-2360->OSD-2360 (EXACT_ID, 1.00) |
| `AEC_daily_report_2026-10-01.pdf` | 5 claim(s): OSD-2296->OSD-2296 (EXACT_ID, 1.00); OSD-2310->OSD-2310 (EXACT_ID, 1.00); OSD-2320->OSD-2320 (EXACT_ID, 1.00); OSD-2330->OSD-2330 (EXACT_ID, 1.00); OSD-2340->OSD-2340 (EXACT_ID, 1.00) |
| `AEC_daily_report_2026-10-02.txt` | 6 claim(s): OSD-2297->OSD-2297 (EXACT_ID, 1.00); OSD-2296->OSD-2296 (EXACT_ID, 1.00); OSD-2310->OSD-2310 (EXACT_ID, 1.00); OSD-2320->OSD-2320 (EXACT_ID, 1.00); OSD-2330->OSD-2330 (EXACT_ID, 1.00); OSD-2340->OSD-2340 (EXACT_ID, 1.00) |
| `AEC_discipline_progress_2026-09-30.xlsx` | 8 claim(s): OSD-2297->OSD-2297 (EXACT_ID, 1.00); OSD-2296->OSD-2296 (EXACT_ID, 1.00); OSD-2310->OSD-2310 (EXACT_ID, 1.00); OSD-2320->OSD-2320 (EXACT_ID, 1.00); OSD-2330->OSD-2330 (EXACT_ID, 1.00); OSD-2340->OSD-2340 (EXACT_ID, 1.00); OSD-2350->OSD-2350 (EXACT_ID, 1.00); OSD-2360->OSD-2360 (EXACT_ID, 1.00) |
| `AEC_field_note_with_errors_2026-10-03.csv` | 4 claim(s): AEC-9999->unmatched (HYBRID_FALLBACK, 0.09); OSD-1000->unmatched (HYBRID_FALLBACK, 0.31); OSD-2240->unmatched (HYBRID_FALLBACK, 0.23); OSD-2297->OSD-2297 (EXACT_ID, 1.00) |
| `AEC_invalid_values_2026-10-03.csv` | refused as a whole file (clear message, no claim created) |
| `AEC_site_progress_note_2026-09-30.docx` | 4 claim(s): OSD-2297->OSD-2297 (EXACT_ID, 1.00); OSD-2296->OSD-2296 (EXACT_ID, 1.00); OSD-2310->OSD-2310 (EXACT_ID, 1.00); OSD-2320->OSD-2320 (EXACT_ID, 1.00) |

## Typed claims (Type Update)

| Text | Intended activity | Expectation |
|---|---|---|
| Good progress on site today, all teams working well. | - | vague: no activity, no quantity, so it is not matched and goes to a Supervisor |

Blocked by an open issue (never offered for matching): OSD-2240, OSD-2250, OSD-2260, OSD-2270, OSD-2280, OSD-2290, OSD-2295.
Photographs are evidence only (not read: no OCR on the deployed API).
Quantity claims on activities with several measured quantities no longer show the false 'no planned quantity' warning when the unit binds to a measured quantity.
