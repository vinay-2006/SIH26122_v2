"""Site Engineer: the original Claim Intake pipeline (submit -> match -> check -> clarify) on v2, through the legacy API contract."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write
TEXT = "Welding mainline A2010: 120 joints completed today"


def submit(lg, kit, text=TEXT):
    r = lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": text, "input_channel": "TYPED_TEXT"})
    assert r.status_code == 200, r.text
    return r.json()


def test_text_claim_is_extracted_matched_automatically_and_never_approved(kit, lg):
    ev = submit(lg, kit)
    assert ev["status"] == "MATCHED" and ev["matched_activity_id"] == "A2010" and ev["input_channel"] == "TYPED_TEXT"
    assert ev["claimed_quantity"] == 120 and ev["discipline"] == "PIPING" and ev["field_provenance"]["activity_id"] == "SCHEDULE_AUTO_FILLED"
    assert kit.count("planner_decisions") == 0 and kit.count("approved_resource_progress") == 0
    m = lg.post(f"/api/v1/claims/{ev['event_id']}/match", kit.world.se)
    assert m.status_code == 200
    body = m.json()
    assert body["matched_activity_id"] == "A2010" and body["match_tier"] == "EXACT_ID" and body["candidates"][0]["activity_id"] == "A2010"
    assert body["candidates"][0]["rank_order"] == 1 and body["status"] == "MATCHED"


def test_check_runs_the_original_validation_rules_and_flags_for_review(kit, lg):
    ev = submit(lg, kit)
    lg.post(f"/api/v1/claims/{ev['event_id']}/match", kit.world.se)
    k = lg.post(f"/api/v1/claims/{ev['event_id']}/check", kit.world.se)
    assert k.status_code == 200, k.text
    body = k.json()
    # the ORIGINAL sequence rule: A2010 starts (SS) before its predecessor A2000 has any progress
    assert [i["rule_code"] for i in body["validation_issues"]] == ["VAL_OUT_OF_SEQUENCE"] and body["status"] == "REVIEW_REQUIRED"
    assert body["priority_score"] > 100 and "Critical" in body["priority_reasons"]
    assert lg.get(f"/api/v1/claims/{ev['event_id']}", kit.world.se).json()["status"] == "REVIEW_REQUIRED"
    assert kit.count("planner_decisions") == 0 and kit.count("approved_resource_progress") == 0       # checks never approve anything
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'CLAIM_CHECKED'").fetchone()["n"] == 1


def test_a_clean_claim_becomes_validated(kit, lg):
    kit.world  # predecessor progress exists, so the sequence rule is satisfied
    from v2api import seed_progress
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 3}, kit.world.sup, kit.world.se)
    ev = submit(lg, kit)
    k = lg.post(f"/api/v1/claims/{ev['event_id']}/check", kit.world.se).json()
    assert k["validation_issues"] == [] and k["status"] == "VALIDATED"
    with connect() as c:
        assert c.execute("select status from execution_events where event_id = %s", (ev["event_id"],)).fetchone()["status"] == "VALIDATED"


def test_field_copilot_holds_an_incomplete_report_and_resumes_after_the_answer(kit, lg):
    ev = submit(lg, kit, "Welding is going on near the pump station")
    assert ev["clarification_status"] == "PENDING" and ev["clarification_question"] and ev["status"] == "EXTRACTED" and ev["matched_activity_id"] is None
    r = lg.post(f"/api/v1/claims/{ev['event_id']}/match", kit.world.se)
    assert r.status_code == 400                                                       # no matching while a clarification is pending
    with connect() as c:
        row = c.execute("select status, clarification_status from execution_events where event_id = %s", (ev["event_id"],)).fetchone()
        assert (row["status"], row["clarification_status"]) == ("REPORTED", "ASKED")
        assert c.execute("select count(*) n from notifications where event_id = %s", (ev["event_id"],)).fetchone()["n"] == 0      # supervisors hear about it only once it is complete
    a = lg.post(f"/api/v1/claims/{ev['event_id']}/clarify", kit.world.se, json={"answer": "A2010 welding, 120 joints done"})
    assert a.status_code == 200, a.text
    done = a.json()
    assert done["clarification_status"] == "ANSWERED" and done["raw_claim_text"] == "Welding is going on near the pump station"      # filed text stays as filed
    assert done["clarification_answer"] == "A2010 welding, 120 joints done"
    m = lg.post(f"/api/v1/claims/{ev['event_id']}/match", kit.world.se).json()
    assert m["matched_activity_id"] == "A2010"


def test_engineers_see_only_their_own_claims_and_project_managers_none(kit, lg):
    ev = submit(lg, kit)
    other = lg.get(f"/api/v1/claims/{ev['event_id']}", kit.world.se2)
    assert other.status_code in (403, 404)
    assert lg.get(f"/api/v1/claims/{ev['event_id']}", kit.world.pm).status_code == 403
    assert lg.get("/api/v1/claims", kit.world.pm).status_code == 403
    assert [c["event_id"] for c in lg.get("/api/v1/claims", kit.world.se).json()] == [ev["event_id"]]
    assert lg.post(f"/api/v1/claims/{ev['event_id']}/match", kit.world.se2).status_code in (403, 404)
    assert lg.post("/api/v1/claims/text", kit.world.sup, json={"raw_claim_text": TEXT}).status_code == 403      # a Supervisor never files a claim
    assert lg.post("/api/v1/claims/text", kit.world.pm, json={"raw_claim_text": TEXT}).status_code == 403


def test_the_project_context_is_authoritative_and_isolated(kit, lg, api):
    ev = submit(lg, kit)
    from legacy_helpers import Legacy
    other = Legacy(api, kit.world.project2)                      # the SE has no membership of the other project
    assert other.get(f"/api/v1/claims/{ev['event_id']}", kit.world.se).status_code == 403
    assert Legacy(api, "not-a-uuid").get("/api/v1/claims", kit.world.se).status_code == 400
    bad_version = Legacy(api, kit.project, version="00000000-0000-0000-0000-000000000000")
    assert bad_version.get("/api/v1/claims", kit.world.se).status_code == 403
    no_ctx = api.get("/api/v1/claims", kit.world.se)
    assert no_ctx.status_code == 400


def test_a_structured_progress_file_creates_one_claim_per_row_without_an_llm(kit, lg):
    csv = b"Activity ID,Activity Name,Discipline,Progress Pct\nA2000,Pipe stringing,Piping,40\nA2010,Welding mainline,Piping,10\n"
    r = lg.post("/api/v1/claims/schedule-export", kit.world.se, files={"file": ("progress.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    claims = r.json()
    assert sorted(c["reported_activity_id"] for c in claims) == ["A2000", "A2010"] and all(c["input_channel"] == "SCHEDULE_EXPORT" for c in claims)
    assert all(c["matched_activity_id"] == c["reported_activity_id"] for c in claims)
    assert kit.count("planner_decisions") == 0


def test_a_text_report_file_is_ingested_through_the_original_batch_extractor(kit, lg):
    txt = b"Daily report\nA2000 pipe stringing 40 percent complete\nA2010 welding mainline 10 percent complete\n"
    r = lg.post("/api/v1/claims/file", kit.world.se, files={"file": ("dpr.txt", txt, "text/plain")}, data={"purpose": "SCANNED_DIARY"})
    assert r.status_code == 200, r.text
    assert len(r.json()) >= 2 and all(c["input_channel"] == "FILE_UPLOAD" for c in r.json())
