#!/usr/bin/env python3
"""Dry run of the demo reports: read each file with the application's own report readers (no LLM), then rank every extracted claim with the matching engine against the
NRL-EXPANSION schedule READ from the hosted database in a read-only transaction. Nothing is written anywhere. Prints one line per extracted claim and writes
sample_data/demo_v2/nrl_expansion/expected_results.json (used by the README).

    set -a; . .local/hosted.env; set +a; python3 scripts/check_demo_reports_v2.py"""
from __future__ import annotations

import datetime
import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("EXTRACTION_FALLBACK", "rules")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402

from backend.v2.compat import claims_api as ca, uploads  # noqa: E402
from backend.v2.matching import adapter, service as svc  # noqa: E402

DIR = ROOT / "sample_data" / "demo_v2" / "nrl_expansion"
BASIS = {a["id"]: a for a in json.loads((DIR / "basis.json").read_text())}


def bind(act_id, uom):
    """which measured quantity the reported unit would bind to (same idea as the application: unit match, ambiguous when two share it)"""
    if not act_id or act_id not in BASIS:
        return "-"
    measured = [r for r in BASIS[act_id]["res"] if r["mp"]]
    hit = [r["code"] for r in measured if uom and r["uom"].lower() == uom.lower().rstrip("s") or (uom and r["uom"].lower() == uom.lower())]
    return hit[0] if len(hit) == 1 else ("AMBIGUOUS " + "/".join(hit) if hit else ("NO_UNIT_MATCH" if uom else "no quantity"))


def main() -> int:
    url = os.environ["DB_V2_URL"].replace(":5432/", ":6543/")
    with psycopg.connect(url, connect_timeout=20, row_factory=psycopg.rows.dict_row, prepare_threshold=None) as c:
        c.read_only = True
        pid = c.execute("select project_id from projects where project_code='NRL-EXPANSION'").fetchone()["project_id"]
        ver = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (pid,)).fetchone()["version_id"]
        acts = adapter.load_activities(c, pid, ver)
    results = []
    for f in sorted(DIR.iterdir()):
        if f.name in ("basis.json", "expected_results.json", "README.md", "typed_claims.json") or f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            continue                                                       # photographs are EVIDENCE for a typed claim, not reports (and are not read without OCR)
        try:
            drafts, _, _ = uploads.build_drafts(f.name, f.read_bytes())
        except Exception as e:                                             # noqa: BLE001
            msg = str(e)[:90]
            print(f"{f.name}\n   NOT READ: {type(e).__name__}: {msg}")
            results.append({"file": f.name, "claims": [], "not_read": f"{type(e).__name__}: {msg}"})
            continue
        print(f"{f.name}  ->  {len(drafts)} claim(s)")
        entry = {"file": f.name, "claims": []}
        for d in drafts:
            ex = d.extracted
            qts = [{"reported_qty": q["qty"], "reported_uom": q["uom"]} for q in ca._quantities(ex)]
            claim = {"event_id": uuid.uuid4(), "event_date": datetime.date.today(), "raw_claim_text": d.raw_text, "input_channel": "TYPED", "claimed_pct": ex.claimed_pct,
                     "claim_mode": ex.claim_mode.value if ex.claim_mode else None, **ca._fields(ex)}
            r = svc.rank_claim(pid, ver, claim, qts, acts)
            top = r["candidates"][0] if r["candidates"] else None
            q = qts[0] if qts else None
            row = {"reported_id": ex.reported_activity_id, "qty": q and float(q["reported_qty"]), "uom": q and q["reported_uom"], "matched": r["activity_id"], "tier": r["tier"],
                   "confidence": round(r["confidence"], 3), "matched_flag": r["matched"], "ambiguous": r["ambiguous"], "reason": r["reason"],
                   "binding": bind(r["activity_id"] if r["matched"] else (top.activity_id if top else None), q and q["reported_uom"]), "rank2": (r["candidates"][1].activity_id if len(r["candidates"]) > 1 else None)}
            entry["claims"].append(row)
            print(f"   id={str(row['reported_id']):9s} qty={row['qty']} {str(row['uom'] or ''):6s} -> {str(row['matched']):9s} {str(row['tier']):16s} conf={row['confidence']:.3f} "
                  f"{'MATCHED' if row['matched_flag'] else 'NOT MATCHED'} bind={row['binding']}{'  reason=' + str(row['reason'])[:60] if not row['matched_flag'] else ''}")
        results.append(entry)
    typed = json.loads((DIR / "typed_claims.json").read_text())
    print("typed claims (Type Update):")
    t_out = []
    for t in typed:
        ex = ca.run_extraction(t["text"])
        qts = [{"reported_qty": q["qty"], "reported_uom": q["uom"]} for q in ca._quantities(ex)]
        claim = {"event_id": uuid.uuid4(), "event_date": datetime.date.today(), "raw_claim_text": t["text"], "input_channel": "TYPED", "claimed_pct": ex.claimed_pct,
                 "claim_mode": ex.claim_mode.value if ex.claim_mode else None, **ca._fields(ex)}
        r = svc.rank_claim(pid, ver, claim, qts, acts)
        top = r["candidates"][0] if r["candidates"] else None
        row = {"text": t["text"], "intended": t["activity"], "matched": r["activity_id"], "tier": r["tier"], "confidence": round(r["confidence"], 3), "matched_flag": r["matched"], "ambiguous": r["ambiguous"],
               "reason": r["reason"], "binding": bind(r["activity_id"] if r["matched"] else (top.activity_id if top else None), qts[0]["reported_uom"] if qts else None), "expected": t["expected"]}
        t_out.append(row)
        print(f"   intended={t['activity']:9s} -> {str(row['matched']):9s} {str(row['tier']):16s} conf={row['confidence']:.3f} {'MATCHED' if row['matched_flag'] else 'NOT MATCHED'} bind={row['binding']}  [{t['expected'][:40]}]")
    results.append({"typed": t_out})
    (DIR / "expected_results.json").write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
