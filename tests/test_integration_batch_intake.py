"""
Multi-file intake (DB_WRITE + INTEGRATION, isolated DB, no network and no LLM): one upload batch -> many files -> many
claims -> many activity matches, with identical claims merged and unreadable files isolated.

The LLM is unavailable in the test environment (keys are blanked), so text reports exercise the deterministic rules
fallback and the CSV / XLSX files the structured extractor, i.e. the offline path the prototype must support.

    SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration \
    DATABASE_URL=postgresql://postgres@127.0.0.1:54329/setuai_integ pytest tests/test_integration_batch_intake.py
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.prototype_seed import data, seed as seeder
from backend.shared.db import get_connection

pytestmark = [pytest.mark.integration]

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "prototype_batch"
NRL = next(p for p in data.PROJECTS if p.code == "NRL-EXP-01")

# what the sample reports say, activity -> (claimed %, files that report it)
EXPECTED = {
    "NRL-PEI-020": (42.0, 2),   # report 1 AND the evening report: identical -> ONE claim, two sources
    "NRL-ERC-060": (40.0, 1),   # named only ("platforms and ladders ... installation"), no id
    "NRL-CIV-050": (76.0, 1),
    "NRL-PEI-040": (36.0, 2),   # narrative report 1 AND the electrical workbook
    "NRL-ERC-040": (46.0, 1),
    "NRL-ERC-050": (33.0, 1),
    "NRL-PEI-010": (58.0, 1),
    "NRL-PEI-060": (24.0, 1),
    "NRL-ERC-020": (64.0, 1),
    "NRL-PEI-050": (29.0, 1),
}


class _As:
    def __init__(self, who):
        self.who, self.http = who, TestClient(app)
        self.role = "SITE_ENGINEER" if who == "engineer" else "SUPERVISOR"

    def _bind(self):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=seeder.UID[self.who], full_name=self.who, role=self.role)

    def get(self, *a, **k):
        self._bind()
        return self.http.get(*a, **k)

    def post(self, *a, **k):
        self._bind()
        return self.http.post(*a, **k)


def _purge(su, project_id):
    docs = "SELECT document_id FROM source_documents WHERE project_id = %s AND batch_id IS NOT NULL"
    evs = f"SELECT event_id FROM execution_events WHERE project_id = %s AND (document_id IN ({docs}) OR claim_fingerprint IS NOT NULL)"
    for sql in (
        f"DELETE FROM notifications WHERE event_id IN ({evs})",
        f"DELETE FROM planner_decisions WHERE event_id IN ({evs})",
        f"DELETE FROM candidate_matches WHERE event_id IN ({evs})",
        f"DELETE FROM claim_activity_splits WHERE event_id IN ({evs})",
        f"DELETE FROM validation_issues WHERE event_id IN ({evs})",
        f"DELETE FROM source_references WHERE event_id IN ({evs}) OR document_id IN ({docs})",
        f"DELETE FROM execution_events WHERE event_id IN ({evs})",
    ):
        su.execute(sql, (project_id,) * sql.count("%s"))
    su.execute(f"DELETE FROM source_documents WHERE project_id = %s AND batch_id IS NOT NULL", (project_id,))
    su.execute("DELETE FROM upload_batches WHERE project_id = %s", (project_id,))


@pytest.fixture(scope="module")
def world():
    with seeder._connect() as conn:
        seeder.seed(conn)
    su = get_connection()
    su.autocommit = True
    pid = str(su.execute("SELECT project_id FROM projects WHERE project_code = 'NRL-EXP-01'").fetchone()["project_id"])
    _purge(su, pid)
    yield {"su": su, "pid": pid, "sid": NRL.schedule_id}
    app.dependency_overrides.pop(get_current_user, None)
    _purge(su, pid)


def _files(*names, extra=()):
    out = [("files", (n, (SAMPLES / n).read_bytes())) for n in names]
    out += [("files", (n, b)) for n, b in extra]
    return out


def _post(world, who, files):
    return _As(who).post("/api/v1/claims/batch", headers={"X-Project-ID": world["pid"]}, data={"schedule_id": world["sid"]}, files=files)


@pytest.fixture(scope="module")
def batch(world):
    r1 = (SAMPLES / "NRL_daily_report_2026-09-29.txt").read_bytes()
    files = _files("NRL_daily_report_2026-09-29.txt", "NRL_daily_report_2026-09-29_pm_shift.txt", "NRL_progress_2026-09-29.csv",
                   "NRL_electrical_progress_2026-09-29.xlsx",
                   extra=[("site_notes_unreadable.pdf", b"this is not a pdf at all"), ("NRL_daily_report_copy.txt", r1)])
    r = _post(world, "engineer", files)
    assert r.status_code == 200, r.text
    return r.json()


def _claim_for(batch, activity):
    found = [c for c in batch["claims"] if c["matched_activity_id"] == activity]
    assert len(found) == 1, f"{activity}: expected exactly one claim, got {len(found)}"
    return found[0]


def test_batch_maps_many_files_to_many_activities(batch):
    assert batch["file_count"] == 6
    assert batch["status"] == "PARTIAL", "one unreadable file makes the batch partial, not failed"
    assert {c["matched_activity_id"] for c in batch["claims"] if c["matched_activity_id"]} == set(EXPECTED)
    for activity, (pct, n_files) in EXPECTED.items():
        c = _claim_for(batch, activity)
        assert c["claimed_pct"] == pct, activity
        assert len(c["file_names"]) == n_files, f"{activity} backed by {c['file_names']}"
    # one file reports several activities (a daily report is not "one claim")
    report1 = next(f for f in batch["files"] if f["file_name"] == "NRL_daily_report_2026-09-29.txt")
    claims_in_report1 = [c for c in batch["claims"] if "NRL_daily_report_2026-09-29.txt" in c["file_names"]]
    assert len(claims_in_report1) >= 4 and report1["extraction_method"] == "RULES_FALLBACK"
    # a by-activity view exists: activity -> claims -> files
    by = {a["activity_id"]: a for a in batch["activities"]}
    assert set(by) == set(EXPECTED) and len(by["NRL-PEI-040"]["file_names"]) == 2


def test_identical_claims_from_different_files_are_not_duplicated(batch, world):
    assert batch["claim_count"] == len(EXPECTED) == 10
    assert batch["merged_count"] == 2   # PEI-020 and PEI-040 were each reported by a second file
    su = world["su"]
    rows = su.execute("SELECT claim_fingerprint FROM execution_events WHERE project_id = %s AND claim_fingerprint IS NOT NULL", (world["pid"],)).fetchall()
    assert len(rows) == len({r["claim_fingerprint"] for r in rows}) == len(EXPECTED)
    pei = _claim_for(batch, "NRL-PEI-020")
    assert pei["reported_by_multiple_files"] and len(pei["sources"]) == 2


def test_each_file_is_isolated_and_reported(batch):
    f = {x["file_name"]: x for x in batch["files"]}
    assert f["site_notes_unreadable.pdf"]["extraction_status"] == "FAILED" and f["site_notes_unreadable.pdf"]["error"]
    assert f["NRL_daily_report_copy.txt"]["extraction_status"] == "EMPTY" and "Identical" in f["NRL_daily_report_copy.txt"]["error"]
    assert f["NRL_progress_2026-09-29.csv"]["extraction_method"] == "STRUCTURED"
    assert f["NRL_electrical_progress_2026-09-29.xlsx"]["extraction_method"] == "STRUCTURED"
    for ok in ("NRL_daily_report_2026-09-29.txt", "NRL_daily_report_2026-09-29_pm_shift.txt", "NRL_progress_2026-09-29.csv", "NRL_electrical_progress_2026-09-29.xlsx"):
        assert f[ok]["extraction_status"] == "EXTRACTED" and f[ok]["claims_extracted"] >= 2


def test_matching_returns_ranked_explained_candidates_and_does_not_need_an_id(batch):
    named_only = _claim_for(batch, "NRL-ERC-060")      # "Platforms and ladders fabrication and installation reached 40 percent ..." (no id)
    assert named_only["reported_activity_id"] is None and named_only["match_tier"] == "HYBRID_FALLBACK"
    cands = named_only["candidates"]
    assert cands[0]["activity_id"] == "NRL-ERC-060" and [c["rank"] for c in cands] == list(range(1, len(cands) + 1))
    assert cands[0]["confidence"] > 0.40 and cands[0]["confidence"] >= cands[-1]["confidence"]
    assert "stage" in (cands[0]["supporting"] or "").lower() or "text similarity" in (cands[0]["supporting"] or "").lower()
    by_id = _claim_for(batch, "NRL-CIV-050")
    assert by_id["match_tier"] == "EXACT_ID" and by_id["candidates"][0]["confidence"] == 1.0
    assert named_only["discipline"] == "STRUCTURAL", "discipline is filled from the matched activity, not guessed from keywords"


def test_matched_claims_reach_the_supervisor_review_queue(batch, world):
    r = _As("supervisor").get(f"/api/v1/review-queue?schedule_id={world['sid']}", headers={"X-Project-ID": world["pid"], "X-Schedule-ID": world["sid"]})
    assert r.status_code == 200, r.text
    items = r.json()
    queue = items.get("items", items) if isinstance(items, dict) else items
    queued = {(q.get("claim") or q).get("event_id") for q in queue}
    batch_ids = {c["event_id"] for c in batch["claims"] if c["matched_activity_id"]}
    assert batch_ids <= queued, "every matched claim of the batch is reviewable"
    assert {c["status"] for c in batch["claims"] if c["matched_activity_id"]} <= {"VALIDATED", "REVIEW_REQUIRED"}


def test_resubmitting_the_same_batch_creates_no_duplicate_claims(batch, world):
    again = _post(world, "engineer", _files("NRL_daily_report_2026-09-29.txt", "NRL_progress_2026-09-29.csv"))
    assert again.status_code == 200, again.text
    body = again.json()
    n = world["su"].execute("SELECT count(*) n FROM execution_events WHERE project_id = %s AND claim_fingerprint IS NOT NULL", (world["pid"],)).fetchone()["n"]
    assert n == len(EXPECTED), "the same claims were recognised, not created again"
    assert body["merged_count"] >= 5 and all(not c["created_in_this_batch"] or c["matched_activity_id"] is None for c in body["claims"])


def test_only_a_site_engineer_can_upload_and_batches_are_listed_and_readable(batch, world):
    assert _post(world, "supervisor", _files("NRL_progress_2026-09-29.csv")).status_code == 403
    eng = _As("engineer")
    listed = eng.get(f"/api/v1/projects/{world['pid']}/upload-batches?mine=true").json()
    assert any(b["batch_id"] == batch["batch_id"] and b["file_count"] == 6 for b in listed)
    got = eng.get(f"/api/v1/projects/{world['pid']}/upload-batches/{batch['batch_id']}").json()
    assert got["claim_count"] == batch["claim_count"] and len(got["files"]) == 6
    assert eng.get(f"/api/v1/projects/{world['pid']}/upload-batches/00000000-0000-0000-0000-000000000000").status_code == 404


def test_empty_upload_is_rejected(world):
    r = _As("engineer").post("/api/v1/claims/batch", headers={"X-Project-ID": world["pid"]}, data={"schedule_id": world["sid"]})
    assert r.status_code == 422
