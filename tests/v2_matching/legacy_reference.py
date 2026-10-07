"""The LEGACY matching path, run directly (no v2 adapter): canonical sample schedule -> legacy ScheduleActivity dicts -> the legacy FAISS recipe
(build_searchable_text + all-MiniLM-L6-v2 + IndexFlatIP, copied from schedule_index.build_index/search_schedule only to avoid its PostgreSQL read) -> match_claim.
Used to (re)generate legacy_snapshot.json and as the independent side of the equivalence test. It never imports anything from backend/v2."""
from __future__ import annotations

import csv
import glob
import json
import sys
from datetime import date
from pathlib import Path

import torch  # noqa: F401  (must precede faiss; see backend/shared/schedule_index.py)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "sample_data"
SCHEDULE_ID = "SIH26122_NFU"
ASSET_FOR_CASE_002 = {"PIP-PS3-WLD-024": "P-102"}        # golden MATCH-002: "Asset Tag P-102 maps to PIP-PS3-WLD-024" (the canonical csv carries no asset tags)


def canonical_activities(with_asset_tags: bool = True):
    acts = []
    for r in csv.DictReader(open(SAMPLE / "canonical" / "schedule.csv")):
        acts.append(dict(schedule_id=SCHEDULE_ID, activity_id=r["L6 Task ID"], activity_name=r["Activity"], wbs_code=r["L5 Activity ID"],
                         discipline=r["Discipline"].upper(), location=r["L1"], asset_tag=(ASSET_FOR_CASE_002.get(r["L6 Task ID"]) if with_asset_tags else None),
                         planned_start=date.fromisoformat(r["Baseline Start"]), planned_finish=date.fromisoformat(r["Baseline Finish"]),
                         planned_quantity=float(r["Planned Qty"]), uom=r["Unit"], baseline_pct_complete=0.0, actual_pct_complete=0.0))
    return acts


def cases():
    return [json.load(open(f)) for f in sorted(glob.glob(str(SAMPLE / "test-cases" / "matching" / "*.json")))]


def run_legacy(acts, case):
    from backend.shared import schedule_index as si
    from backend.routers import matching as m
    from backend.shared.schemas import ExecutionClaim, ScheduleActivity
    import faiss
    model = si._get_model()
    emb = model.encode([si.build_searchable_text(ScheduleActivity(**a)) for a in acts], batch_size=si.EMBEDDING_BATCH_SIZE, normalize_embeddings=True,
                       convert_to_numpy=True, show_progress_bar=False).astype("float32")
    idx = faiss.IndexFlatIP(emb.shape[1]); idx.add(emb)
    q = model.encode([case["raw_claim_text"]], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False).astype("float32")
    s, i = idx.search(q, min(si.DEFAULT_TOP_K, idx.ntotal))
    sem = sorted((si.SearchCandidate(SCHEDULE_ID, acts[j]["activity_id"], float(x)) for x, j in zip(s[0], i[0]) if j != -1), key=lambda c: c.score, reverse=True)
    fields = {k: v for k, v in case.items() if k in ExecutionClaim.model_fields}
    claim = ExecutionClaim(**fields)
    return m.match_claim(claim, acts, semantic_results=sem)


def summarise(cands):
    return [dict(activity_id=c.activity_id, rank_order=c.rank_order, match_tier=c.match_tier, composite_confidence=round(c.composite_confidence, 6),
                 semantic_score=None if c.semantic_score is None else round(c.semantic_score, 6), fuzzy_score=None if c.fuzzy_score is None else round(c.fuzzy_score, 6),
                 location_score=None if c.location_score is None else round(c.location_score, 6), discipline_score=None if c.discipline_score is None else round(c.discipline_score, 6),
                 supporting_signals=c.supporting_signals, disqualifying_signals=c.disqualifying_signals) for c in cands]


if __name__ == "__main__":
    acts = canonical_activities()
    out = {c["case_id"]: summarise(run_legacy(acts, c)) for c in cases()}
    (Path(__file__).parent / "legacy_snapshot.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps({k: [(x["activity_id"], x["match_tier"], x["composite_confidence"]) for x in v[:3]] for k, v in out.items()}, indent=1))
