#!/usr/bin/env python3
"""
End-to-end walkthrough of the prototype against a RUNNING backend (scripts/run_local_demo.sh backend) on the isolated
demo database. Real HTTP, real HS256 JWTs, both roles:

    login (engineer, supervisor) -> projects -> dashboards for all four projects (DB-derived progress)
    -> engineer uploads a multi-file batch -> claims extracted + matched + merged -> supervisor review queue
    -> engineer reports an issue (with evidence) and sees similar past incidents -> supervisor sees the repeated pattern
    -> supervisor decides on batch claims -> engineer sees the decisions (persisted notifications)
    -> supervisor resolves the issue into institutional memory

It MUTATES the demo database (adds a batch, an issue and decisions) and prints what it did; re-run `run_local_demo.sh
setup` on a fresh database for a pristine demo. Usage: scripts/prototype_e2e_api.py [--base http://127.0.0.1:8010]
"""
import argparse
import sys
from pathlib import Path

import requests

PASSWORD = "Demo123456!"
SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "prototype_batch"
ok_count = 0


def check(cond, msg):
    global ok_count
    if not cond:
        print(f"  FAIL  {msg}")
        sys.exit(1)
    ok_count += 1
    print(f"  ok    {msg}")


class Client:
    def __init__(self, base, email):
        self.base = base
        r = requests.post(f"{base}/api/v1/auth/local-login", json={"email": email, "password": PASSWORD}, timeout=30)
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = requests.get(f"{base}/api/v1/auth/me", headers=self.h, timeout=30).json()
        self.role, self.name, self.id = me["role"], me["full_name"], me["id"]

    def req(self, method, path, project=None, schedule=None, **kw):
        h = dict(self.h)
        if project:
            h["X-Project-ID"] = project
        if schedule:
            h["X-Schedule-ID"] = schedule
        r = requests.request(method, f"{self.base}{path}", headers=h, timeout=120, **kw)
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    base = ap.parse_args().base

    print("1. Authentication: only the two prototype roles")
    eng, sup = Client(base, "engineer@setuai.demo"), Client(base, "supervisor@setuai.demo")
    check(eng.role == "SITE_ENGINEER" and sup.role == "SUPERVISOR", f"engineer -> {eng.role}, supervisor -> {sup.role}")
    check(requests.post(f"{base}/api/v1/auth/local-login", json={"email": "supervisor@setuai.demo", "password": "wrong"}).status_code == 401, "wrong password is rejected")

    print("2. Four projects, each with its lifecycle and DB-derived stage / discipline progress")
    projects = {p["project_code"]: p for p in sup.req("GET", "/api/v1/projects").json()}
    check(set(projects) == {"NNB-COP-01", "AND-ODC-01", "NRL-EXP-01", "SMP-CCP-01"}, f"exactly the four projects: {sorted(projects)}")
    ctx = {}
    for code, p in projects.items():
        pid = p["project_id"]
        scheds = sup.req("GET", f"/api/v1/projects/{pid}/schedules").json()
        sid = next(s["schedule_id"] for s in scheds if s["active"])
        ctx[code] = (pid, sid)
        d = sup.req("GET", f"/api/v1/projects/{pid}/schedules/{sid}/dashboard").json()
        stages = [round(s["actual_pct"], 1) for s in d["stages"]]
        discs = {x["discipline"]: x["actual_pct"] for x in d["disciplines"]}
        print(f"        {code:11} {d['lifecycle_status']:9} overall {d['overall']['actual_pct']:6.1f}%  stages {stages}  disciplines {len(discs)}")
        if d["lifecycle_status"] == "COMPLETED":
            check(d["overall"]["actual_pct"] == 100 and all(v == 100 for v in stages) and all(v == 100 for v in discs.values()), f"{code}: completed, every stage and discipline at 100%")
        elif d["lifecycle_status"] == "UPCOMING":
            check(d["overall"]["actual_pct"] == 0 and all(v == 0 for v in stages), f"{code}: planned, 0% everywhere, nothing started")
        else:
            check(len(set(stages)) >= 5 and len(set(discs.values())) >= 4, f"{code}: ongoing, varied stage and discipline progress")

    pid, sid = ctx["NRL-EXP-01"]
    print("3. Site Engineer uploads a multi-file batch (NRL)")
    names = ["NRL_daily_report_2026-09-29.txt", "NRL_daily_report_2026-09-29_pm_shift.txt", "NRL_progress_2026-09-29.csv", "NRL_electrical_progress_2026-09-29.xlsx"]
    files = [("files", (n, (SAMPLES / n).read_bytes())) for n in names] + [("files", ("site_notes_unreadable.pdf", b"not a pdf"))]
    r = eng.req("POST", "/api/v1/claims/batch", project=pid, data={"schedule_id": sid}, files=files)
    check(r.status_code == 200, f"batch accepted ({r.status_code})")
    b = r.json()
    acts = {c["matched_activity_id"] for c in b["claims"] if c["matched_activity_id"]}
    check(b["file_count"] == 5 and b["claim_count"] == 10 and b["merged_count"] == 2, f"5 files -> {b['claim_count']} claims, {b['merged_count']} identical claims merged")
    check(len(acts) == 10, f"{len(acts)} different activities identified across the files")
    check(any(f["extraction_status"] == "FAILED" for f in b["files"]) and b["status"] == "PARTIAL", "the unreadable file is reported, the rest processed")
    multi = [c for c in b["claims"] if c["reported_by_multiple_files"]]
    check(len(multi) == 2, "two activities are each backed by two files but exist once")

    print("4. Supervisor review queue")
    q = sup.req("GET", f"/api/v1/review-queue?schedule_id={sid}", project=pid, schedule=sid).json()
    queue = q.get("items", q) if isinstance(q, dict) else q
    queued = {(x.get("claim") or x).get("event_id") for x in queue}
    check({c["event_id"] for c in b["claims"] if c["matched_activity_id"]} <= queued, "every matched claim of the batch is in the supervisor's queue")

    print("5. Site Engineer reports an issue; memory offers similar past incidents")
    cats = eng.req("GET", f"/api/v1/projects/{pid}/issue-categories", project=pid).json()
    check(len(cats) == 12, f"{len(cats)} issue categories")
    sim = eng.req("POST", f"/api/v1/projects/{pid}/memory/for-issue", project=pid,
                  json={"category_code": "MATERIAL_DELIVERY_DELAY", "query": "cable drums delivery delayed vendor logistics", "top_k": 3}).json()
    check(sim["results"] and sim["results"][0]["record"]["metadata"]["shared_from_other_project"], "a lesson shared by the completed pipeline project is retrieved")
    stage = next(s for s in sup.req("GET", f"/api/v1/projects/{pid}/stages", project=pid, schedule=sid, params={"schedule_id": sid}).json() if s["stage_code"] == "S5")
    r = eng.req("POST", f"/api/v1/projects/{pid}/schedules/{sid}/issues", project=pid, schedule=sid, json={
        "activity_id": "NRL-PEI-040", "category_code": "LABOUR_SHORTAGE", "severity": "HIGH", "title": "E2E: cable pullers short at the corridor",
        "description": "Only 9 of 16 cable pullers reported for three days.", "expected_duration_days": 5, "blocks_work": False})
    check(r.status_code == 201, "issue persisted with category, severity, stage and activity")
    issue = r.json()
    check(issue["stage_name"] and issue["discipline"] == "ELECTRICAL", f"associated with stage '{issue['stage_name']}' and discipline {issue['discipline']}")
    ev = eng.req("POST", f"/api/v1/projects/{pid}/schedules/{sid}/issues/{issue['issue_id']}/evidence", project=pid, schedule=sid,
                 files={"file": ("crew_roster.txt", b"roster: 9 of 16 present")}).json()
    check(len(ev["evidence"]) == 1, "evidence file attached")

    print("6. Supervisor: root-cause analysis shows the repeated pattern")
    a = sup.req("GET", f"/api/v1/projects/{pid}/schedules/{sid}/root-cause-analysis", project=pid, schedule=sid).json()
    labour = next(c for c in a["categories"] if c["category_code"] == "LABOUR_SHORTAGE")
    check(labour["is_repeated_pattern"] and labour["activity_count"] >= 4, f"labour shortage: {labour['issue_count']} issues across {labour['activity_count']} activities / {labour['stage_count']} stages")

    print("7. Supervisor decides; the Site Engineer receives it (persisted)")
    cl = {c["matched_activity_id"]: c for c in b["claims"] if c["matched_activity_id"]}
    d1 = sup.req("POST", "/api/v1/decisions", project=pid, json={"event_id": cl["NRL-PEI-020"]["event_id"], "action": "REJECT", "justification": "Welding records for the counted joints are missing."})
    d2 = sup.req("POST", "/api/v1/decisions", project=pid, json={"event_id": cl["NRL-CIV-050"]["event_id"], "action": "HOLD", "justification": "Attach the drain level survey before approval."})
    d3 = sup.req("POST", "/api/v1/decisions", project=pid, json={"event_id": cl["NRL-ERC-040"]["event_id"], "action": "APPROVE", "approved_pct": 46, "justification": "Verified on site."})
    check(d1.status_code == d2.status_code == d3.status_code == 200, f"3 decisions recorded ({d1.status_code}/{d2.status_code}/{d3.status_code})")
    notes = eng.req("GET", f"/api/v1/projects/{pid}/notifications", project=pid).json()
    by = {n["event_id"]: n for n in notes["items"] if n["event_id"]}
    check(by[cl["NRL-PEI-020"]["event_id"]]["decision_action"] == "REJECT" and "Welding records" in by[cl["NRL-PEI-020"]["event_id"]]["decision_comment"], "REJECT with the supervisor's comment reaches the engineer")
    check(by[cl["NRL-CIV-050"]["event_id"]]["decision_action"] == "HOLD" and by[cl["NRL-ERC-040"]["event_id"]]["decision_action"] == "APPROVE", "HOLD and APPROVE too")
    check(all(n["decided_at"] and n["decided_by_name"] == "Demo Supervisor" for n in (by[cl[a]["event_id"]] for a in ("NRL-PEI-020", "NRL-CIV-050", "NRL-ERC-040"))), "each carries who decided and when")
    check(not any(n["event_id"] in {cl[a]["event_id"] for a in ("NRL-PEI-020", "NRL-CIV-050")} for n in sup.req("GET", f"/api/v1/projects/{pid}/notifications", project=pid).json()["items"]), "notifications are recipient-only")

    print("8. Supervisor resolves the issue into institutional memory")
    r = sup.req("POST", f"/api/v1/projects/{pid}/schedules/{sid}/issues/{issue['issue_id']}/resolve", project=pid, schedule=sid, json={
        "resolution_notes": "Second pulling gang added from the Jorhat pool.", "outcome": "Pulling caught up in nine days.", "cause": "Parallel expansion absorbed local crews", "share_with_organisation": True})
    check(r.status_code == 200 and r.json()["memory_incident_id"], "resolved and stored as institutional knowledge")
    again = eng.req("POST", f"/api/v1/projects/{pid}/memory/for-issue", project=pid, json={"category_code": "LABOUR_SHORTAGE", "query": "cable pullers short corridor", "activity_id": "NRL-PEI-040"}).json()
    check(any("E2E: cable pullers short" in x["record"]["title"] for x in again["results"]), "a similar future issue now retrieves this lesson")
    print(f"\nALL {ok_count} CHECKS PASSED")


if __name__ == "__main__":
    main()
