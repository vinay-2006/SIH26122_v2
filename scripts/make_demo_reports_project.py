#!/usr/bin/env python3
"""Build a demo progress-report package for ANY seeded project from its REAL schedule, read-only from the hosted database, and keep only what the application's own readers and
matching engine verify (nothing is written to the database).

    set -a; . .local/hosted.env; set +a
    python3 scripts/make_demo_reports_project.py AEC-OFFSHORE      # writes sample_data/demo_v2/aec_offshore/
    python3 scripts/make_demo_reports_project.py SMP-PIPE

Files: a structured CSV and an XLSX (exact activity ids), three multi-item daily reports (TXT/PDF/DOCX, need the language-model reader), one-item notes and typed claims that were
verified to match with the rule-based reader too, a CSV with deliberate faults, an invalid CSV, two synthetic photographs, expected_results.json and a README.
Every figure is fictional demonstration data."""
from __future__ import annotations

import csv
import datetime
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("EXTRACTION_FALLBACK", "rules")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402

from backend.v2.compat import claims_api as ca, uploads  # noqa: E402
from backend.v2.matching import adapter, service as svc  # noqa: E402

spec = importlib.util.spec_from_file_location("nrl_gen", ROOT / "scripts" / "make_demo_reports_v2.py")
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)                                              # reuse the file writers (CSV header, PDF, DOCX, photographs)

WORDS = {"JOINT": "joints", "TONNE": "tonnes", "NOS": "nos", "M": "m", "M2": "m2", "M3": "m3", "KM": "km"}
UNIT_OK = {"JOINT", "TONNE", "NOS", "M", "KM"}                          # units the rule-based reader recognises in free text


def load(code: str):
    url = os.environ["DB_V2_URL"].replace(":5432/", ":6543/")
    with psycopg.connect(url, connect_timeout=20, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        c.read_only = True
        pr = c.execute("select project_id, project_name from projects where project_code=%s", (code,)).fetchone()
        pid = pr["project_id"]
        ver = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (pid,)).fetchone()["version_id"]
        acts = adapter.load_activities(c, pid, ver)
        rows = c.execute("select external_activity_id id, activity_name name, discipline_code disc, execution_state st, physical_pct pct, total_float, activity_uid from v_activity_progress where project_id=%s order by external_activity_id", (pid,)).fetchall()
        basis = []
        for a in rows:
            res = c.execute("""select pr.resource_code, br.unit_of_measure uom, br.baseline_qty, br.measures_progress mp,
                               (select cumulative_qty from approved_resource_progress p where p.assignment_uid=br.assignment_uid order by entry_seq desc limit 1) cum
                               from baseline_resources br join project_resources pr on pr.resource_id=br.resource_id where br.activity_uid=%s and br.project_id=%s order by pr.resource_code""", (a["activity_uid"], pid)).fetchall()
            basis.append({"id": a["id"], "name": a["name"], "disc": a["disc"], "state": a["st"], "pct": float(a["pct"]), "float": a["total_float"],
                          "res": [{"code": r["resource_code"], "uom": r["uom"], "base": float(r["baseline_qty"]), "mp": r["mp"], "cum": float(r["cum"] or 0)} for r in res]})
        people = c.execute("select p.full_name, m.role from project_memberships m join profiles p on p.id=m.user_id where m.project_id=%s order by m.role, p.full_name", (pid,)).fetchall()
        m, _, Elig = svc._engine()
        el, ex = Elig.filter_eligible_activities(acts, expected_project_id=str(pid), expected_schedule_id=str(ver), is_rework=False)
        eligible = {(a["activity_id"] if isinstance(a, dict) else a.activity_id) for a in el}
        blocked = sorted(k for k, v in ex.items() if v.reason == "BLOCKED")
    return pid, ver, pr["project_name"], basis, acts, eligible, blocked, people


def rank(pid, ver, acts, text, ex=None):
    ex = ex or ca.run_extraction(text)
    q = [{"reported_qty": x["qty"], "reported_uom": x["uom"]} for x in ca._quantities(ex)]
    claim = {"event_id": uuid.uuid4(), "event_date": datetime.date.today(), "raw_claim_text": text, "input_channel": "TYPED", "claimed_pct": ex.claimed_pct,
             "claim_mode": ex.claim_mode.value if ex.claim_mode else None, **ca._fields(ex)}
    return svc.rank_claim(pid, ver, claim, q, acts), q


def main(code: str) -> int:
    pid, ver, pname, basis, acts, eligible, blocked, people = load(code)
    slug = code.lower().replace("-", "_")
    out = ROOT / "sample_data" / "demo_v2" / slug
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        f.unlink()
    (out / "basis.json").write_text(json.dumps(basis, default=str))
    G.OUT, G.PROJECT = out, f"{pname} ({code})"
    prefix = code.split("-")[0]
    live = [a for a in basis if a["id"] in eligible and a["state"] == "IN_PROGRESS" and any(r["mp"] for r in a["res"])]
    notstarted = [a for a in basis if a["id"] in eligible and a["state"] == "NOT_STARTED" and any(r["mp"] for r in a["res"])]
    completed = [a for a in basis if a["state"] == "COMPLETED"]
    pool = live + notstarted
    print(f"{code}: {len(basis)} activities, {len(live)} in progress, {len(notstarted)} not started, {len(completed)} complete, blocked: {blocked}")
    if not pool:
        (out / "README.md").write_text(f"# {code}\n\nThis project has no open activity: it accepts no new progress reports. Use it to show history and lessons, and to show that a completed project refuses new work.\n")
        print("  no open activities: README only")
        return 0

    def measured(a):
        return sorted((r for r in a["res"] if r["mp"]), key=lambda r: -r["base"])

    def make_row(date, a, today_frac=0.004, start=False):
        r = measured(a)[0]
        today = max(1, round(r["base"] * today_frac))
        prior = round(r["cum"]); cum = prior + today
        pct = round(min(99.9, a["pct"] + max(0.3, 100.0 * today / r["base"] / max(1, len(measured(a))))), 1) if a["state"] == "IN_PROGRESS" else round(min(100.0, 100.0 * cum / r["base"] + 0.1), 1)
        return dict(date=date, act=a["id"], disc=a["disc"].title().replace("_", " "), code=r["code"], uom=r["uom"], planned=round(r["base"]), prior=prior, today=today, cum=cum, pct=pct,
                    desc=a["name"], status="Started" if start else "Ongoing", remark="", evidence="", task=f"{a['id']}-01", act_name=a["name"])

    d1, d2, d3, d4 = "2026-10-03", "2026-10-02", "2026-10-01", "2026-09-30"
    csv_rows = [make_row(d1, a) for a in pool[:8]]
    G.write_csv(out / f"{prefix}_daily_progress_{d1}.csv", csv_rows)
    sheets = {}
    for a in pool[:8]:
        sheets.setdefault(a["disc"].title().replace("_", " "), []).append(make_row(d4, a, 0.003))
    G.XLSX_SHEETS = sheets
    G.write_xlsx(out / f"{prefix}_discipline_progress_{d4}.xlsx")
    for fname, rows, day, title in ((f"{prefix}_daily_report_{d2}.txt", [make_row(d2, a, 0.005) for a in pool[:6]], d2, "DAILY PROGRESS REPORT"),
                                    (f"{prefix}_daily_report_{d3}.pdf", [make_row(d3, a, 0.006) for a in pool[1:6]], d3, "DAILY PROGRESS REPORT"),
                                    (f"{prefix}_site_progress_note_{d4}.docx", [make_row(d4, a, 0.004) for a in pool[:4]], d4, "SITE PROGRESS NOTE")):
        text = G.daily_text(rows, day, title)
        if fname.endswith(".txt"):
            (out / fname).write_text(text, encoding="utf-8")
        elif fname.endswith(".pdf"):
            G.write_pdf(out / fname, text)
        else:
            G.write_docx(out / fname, [], lines=text.splitlines())

    # free-text sentences: keep the ones the RULE-BASED reader + engine match with the right activity and a quantity
    typed, notes = [], []
    for a in pool:
        for r in measured(a):
            if r["uom"] not in UNIT_OK:
                continue
            n = max(2, round(r["base"] * 0.003))
            verb = "commenced today:" if a["state"] == "NOT_STARTED" else "completed today:"
            text = f"{a['name']} {verb} {n} {WORDS[r['uom']]} installed ({r['code']})."
            try:
                res, q = rank(pid, ver, acts, text)
            except Exception:                                           # noqa: BLE001
                continue
            if res["matched"] and res["activity_id"] == a["id"] and res["confidence"] >= 0.65 and q:
                typed.append((a["id"], text, "clean claim" if a["state"] != "NOT_STARTED" else "first report on a not-started activity (actual start)", round(res["confidence"], 3)))
                break
    for i, (aid, text, why, conf) in enumerate(typed[:3]):
        fname = f"{prefix}_field_note_{i + 1}_{[d1, d2, d3][i]}"
        day = [d1, d2, d3][i]
        body = G.report_text([(aid, text)], day)
        if i == 0:
            (out / f"{fname}.txt").write_text(body, encoding="utf-8")
        elif i == 1:
            G.write_pdf(out / f"{fname}.pdf", body)
        else:
            G.write_docx(out / f"{fname}.docx", [], lines=body.splitlines())
    typed_list = [{"activity": a, "text": t, "expected": w} for a, t, w, c in typed[:5]]
    first = typed[0] if typed else None
    if first:
        a0 = next(x for x in basis if x["id"] == first[0]); r0 = measured(a0)[0]
        typed_list.append({"activity": a0["id"], "text": f"{a0['name']} completed today: {round(r0['base'] * 0.6)} {WORDS[r0['uom']]} installed ({r0['code']}).",
                           "expected": "far more than the baseline allows: above-baseline warning, Supervisor acknowledgement needed"})
    typed_list.append({"activity": "-", "text": "Good progress on site today, all teams working well.", "expected": "vague: no activity, no quantity, so it is not matched and goes to a Supervisor"})
    (out / "typed_claims.json").write_text(json.dumps(typed_list, indent=1))

    # faults
    flawed = [(d1, f"{prefix}-9999", "Civil", "Unscheduled works at an unknown location", "M2", 40, 0, 0, 12.0, "Activity id is not in the schedule")]
    if completed:
        c0 = completed[0]; flawed.append((d1, c0["id"], c0["disc"].title(), c0["name"], "NOS", 2, 0, 0, 100.0, "Activity is already complete"))
    for b in blocked[:1]:
        ba = next(x for x in basis if x["id"] == b); flawed.append((d1, b, ba["disc"].title(), ba["name"], "NOS", 5, 0, 0, round(ba["pct"] + 1, 1), "Activity is blocked by an open issue"))
    if live:
        a1 = live[0]; flawed.append((d1, a1["id"], a1["disc"].title(), a1["name"], "NOS", 1, 0, 0, max(1.0, round(a1["pct"] - 15, 1)), f"Reported progress is below what is already approved ({a1['pct']:.0f}%)"))
    flawed.append((d1, "", "Piping", "Work carried out on site, good progress today", "", "", "", "", 30.0, "No activity id and no quantity"))
    G.write_flawed(out / f"{prefix}_field_note_with_errors_{d1}.csv", flawed)
    G.write_flawed(out / f"{prefix}_invalid_values_{d1}.csv", [(d1, pool[0]["id"], "Electrical", pool[0]["name"], "NOS", 60, 40, 74, 135.1, "Percentage above 100 is impossible: the whole file is refused")])
    G.write_photo(out / f"{prefix}_site_photo_1_{d1}.jpg", f"SYNTHETIC DEMO IMAGE  |  {code} site works  |  03 Oct 2026", "steel")
    G.write_photo(out / f"{prefix}_site_photo_2_{d1}.jpg", f"SYNTHETIC DEMO IMAGE  |  {code} pipe works  |  03 Oct 2026", "pipe")

    # dry run of everything (read-only) -> expected results + README
    results, lines = [], []
    for f in sorted(out.iterdir()):
        if f.name in ("basis.json", "typed_claims.json", "README.md", "expected_results.json") or f.suffix.lower() in (".jpg", ".png"):
            continue
        if "daily_report" in f.name or "site_progress_note" in f.name:
            time.sleep(12)                                              # the language-model provider's free tier allows only 8,000 tokens per minute
        try:
            drafts, _, _ = uploads.build_drafts(f.name, f.read_bytes())
        except Exception as e:                                          # noqa: BLE001
            results.append({"file": f.name, "not_read": f"{type(e).__name__}"}); lines.append((f.name, "refused as a whole file (clear message, no claim created)")); continue
        ids, weak = [], False
        for d in drafts:
            res, _q = rank(pid, ver, acts, d.raw_text, d.extracted)
            weak = weak or not (res["matched"] and res["confidence"] >= 0.65)
            ids.append(f"{d.extracted.reported_activity_id or '-'}->{res['activity_id'] or 'unmatched'} ({res['tier']}, {res['confidence']:.2f})")
        if "field_note_" in f.name and "with_errors" not in f.name and weak:
            f.unlink(); continue                                        # a one-item note that does not verify is not shipped
        results.append({"file": f.name, "claims": ids}); lines.append((f.name, f"{len(drafts)} claim(s): " + "; ".join(ids)))
    (out / "expected_results.json").write_text(json.dumps(results, indent=1))
    se = ", ".join(p["full_name"] for p in people if p["role"] == "SITE_ENGINEER"); sup = ", ".join(p["full_name"] for p in people if p["role"] == "SUPERVISOR")
    md = [f"# {code} demo progress reports", "", f"Fictional demonstration data for **{pname} ({code})**, built from its seeded schedule (real activity ids, measured quantities and approved progress as of 2026-10-04).",
          f"Site Engineers: {se}. Supervisors: {sup}. Regenerate and dry-run (read-only): `set -a; . .local/hosted.env; set +a; python3 scripts/make_demo_reports_project.py {code}`.", "",
          "Use each file or sentence **once** (the same item reported twice is kept once). Multi-item daily reports (`*_daily_report_*`, `*_site_progress_note_*`) need the language-model reader on the API.", "",
          "## Files and what the dry run (real readers + real engine, ONNX backend, rule-based fallback for free text) produces", "", "| File | Result |", "|---|---|"]
    md += [f"| `{n}` | {r} |" for n, r in lines]
    md += ["", "## Typed claims (Type Update)", "", "| Text | Intended activity | Expectation |", "|---|---|---|"]
    md += [f"| {t['text']} | {t['activity']} | {t['expected']} |" for t in typed_list]
    md += ["", f"Blocked by an open issue (never offered for matching): {', '.join(blocked) or 'none'}.", "Photographs are evidence only (not read: no OCR on the deployed API).",
           "Quantity claims on activities with several measured quantities no longer show the false 'no planned quantity' warning when the unit binds to a measured quantity."]
    (out / "README.md").write_text("\n".join(md) + "\n")
    for n, r in lines:
        print(f"  {n}: {r[:140]}")
    print(f"  typed claims verified: {len(typed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
