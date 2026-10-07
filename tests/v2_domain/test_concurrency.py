"""Concurrency: racing supervisors, racing increments, and a raw database race with the service layer bypassed. After every scenario the ledgers are
checked from raw rows (single linear chain, increments add up, no half-written decision, SQL rollup == independent recomputation)."""
import threading
import time
import uuid
from decimal import Decimal as D

import pytest

from backend.v2.domain import claims, decisions
from backend.v2.domain.common import resolve_actor
from backend.v2.errors import ApiError
from domainkit import TODAY, assert_consistent, recompute_project_pct, run_concurrently
from v2api import connect, make_user


@pytest.fixture
def sups(kit, api, world):
    """four supervisors on the project"""
    out = [kit.sup]
    for i in range(3):
        u = make_user(f"extra_sup_{i}")
        assert api.post(f"/api/v2/projects/{world.project}/members", world.pm, json={"email": u.email, "role": "SUPERVISOR"}).status_code == 201
        out.append(resolve_actor(u.id, world.project))
    return out


def errs(results):
    return [e for k, e in results if k == "err"]


def test_many_supervisors_deciding_one_claim_produce_exactly_one_decision(kit, sups):
    r = kit.submit("A2010", 500, "joints")
    res = run_concurrently([lambda s=s: decisions.decide(s, r["claim_id"], action="APPROVE") for s in sups * 2])
    ok = [v for k, v in res if k == "ok"]
    assert len(ok) == 1
    assert all(isinstance(e, ApiError) and e.code == "CLAIM_NOT_DECIDABLE" and e.status == 409 for e in errs(res))
    led, _ = kit.ledger()
    assert len(led) == 1 and led[0]["cumulative_qty"] == 500 and kit.count("planner_decisions") == 1 and kit.count("notifications", "notification_type = 'CLAIM_DECISION'") == 1
    assert_consistent(kit)


def test_approve_and_reject_racing_on_one_claim_end_with_one_final_decision(kit, sups):
    r = kit.submit("A2010", 500, "joints")
    res = run_concurrently([lambda: decisions.decide(sups[0], r["claim_id"], action="APPROVE"),
                            lambda: decisions.decide(sups[1], r["claim_id"], action="REJECT", justification="Duplicate of another report")])
    assert sum(k == "ok" for k, _ in res) == 1 and all(e.code == "CLAIM_NOT_DECIDABLE" for e in errs(res))
    final = claims.get_claim(kit.sup, r["claim_id"])
    assert final["status"] in ("APPROVED", "REJECTED") and len([d for d in final["decisions"] if d["action"] in ("APPROVE", "REJECT")]) == 1
    assert (kit.count("approved_resource_progress") == 1) == (final["status"] == "APPROVED")
    assert_consistent(kit)


def test_approve_and_withdraw_racing_never_both_win(kit):
    outcomes = set()
    for i in range(6):
        r = kit.submit("A2010", 100 + i, "joints", text=f"race {i}", days_ago=0)
        res = run_concurrently([lambda: decisions.decide(kit.sup, r["claim_id"], action="APPROVE"), lambda: claims.withdraw_claim(kit.se, r["claim_id"], "changed my mind")])
        assert sum(k == "ok" for k, _ in res) == 1, [repr(e) for e in errs(res)]
        final = claims.get_claim(kit.sup, r["claim_id"])["status"]
        outcomes.add(final)
        assert final in ("APPROVED", "WITHDRAWN")
        assert all(e.code in ("CLAIM_ALREADY_FINAL", "CLAIM_NOT_DECIDABLE") for e in errs(res))
    assert_consistent(kit)


def test_twelve_incremental_claims_decided_concurrently_never_double_count(kit, sups):
    cids = [kit.submit("A2010", 100, "joints", basis="INCREMENTAL", text=f"+100 joints, shift report {i}")["claim_id"] for i in range(12)]
    res = run_concurrently([lambda c=c, s=sups[i % 4]: decisions.decide(s, c, action="APPROVE") for i, c in enumerate(cids)])
    assert errs(res) == [], [repr(e) for e in errs(res)]
    led, _ = kit.ledger()
    assert len(led) == 12 and [e["cumulative_qty"] for e in led] == [100 * (i + 1) for i in range(12)] and all(e["incremental_qty"] == 100 for e in led)
    assert [e["prev_cumulative_qty"] for e in led] == [100 * i for i in range(12)]
    assert kit.pct("A2010") == D("60.000")                                                         # 1,200 of 2,000, counted exactly once
    assert_consistent(kit)


def test_racing_cumulative_claims_keep_the_ledger_monotonic(kit, sups):
    cids = [kit.submit("A2010", q, "joints", text=f"cumulative {q}")["claim_id"] for q in (500, 700, 600, 650)]
    res = run_concurrently([lambda c=c, s=sups[i]: decisions.decide(s, c, action="APPROVE") for i, c in enumerate(cids)])
    assert all(e.code in ("WOULD_DECREASE", "NO_PROGRESS_CHANGE") for e in errs(res)), [repr(e) for e in errs(res)]
    led, _ = kit.ledger()
    assert 1 <= len(led) <= 4 and led[-1]["cumulative_qty"] == max(e["cumulative_qty"] for e in led) and led[-1]["cumulative_qty"] in (500, 600, 650, 700)
    assert len(led) + len(errs(res)) == 4
    assert_consistent(kit)


def test_mixed_load_across_activities_has_no_deadlock_and_stays_consistent(kit, sups):
    plan = [("A2010", 100, "joints", "INCREMENTAL")] * 6 + [("A2000", 2, "km", "INCREMENTAL")] * 4 + [("A3010", 8, "nos", "INCREMENTAL")] * 4 + [("A1020", 20, "m3", "INCREMENTAL")] * 3
    cids = [kit.submit(ext, q, u, basis=b, text=f"{ext} #{i}")["claim_id"] for i, (ext, q, u, b) in enumerate(plan)]
    t0 = time.monotonic()
    res = run_concurrently([lambda c=c, s=sups[i % 4]: decisions.decide(s, c, action="APPROVE") for i, c in enumerate(cids)], timeout=90)
    assert errs(res) == [], [repr(e) for e in errs(res)]
    assert time.monotonic() - t0 < 60
    # 6x100 of 2,000 joints; 4x2 of 24 km; 4x8 of 64 loop checks; 3x20 m3 of 480 (steel untouched, equal weights => half)
    assert (kit.pct("A2010"), kit.pct("A2000"), kit.pct("A3010"), kit.pct("A1020")) == (D("30.000"), D("33.333"), D("50.000"), D("6.250"))
    assert_consistent(kit)


def test_the_database_alone_serialises_two_writers_of_one_assignment(kit):
    """service layer bypassed: two raw transactions write ledger rows for the SAME assignment at once. The second must WAIT for the first and then
    compute its previous / incremental values from what the first committed (no fork, no double count)."""
    a = kit.submit("A2010", 100, "joints", text="writer A")["claim_id"]
    b = kit.submit("A2010", 150, "joints", text="writer B")["claim_id"]
    asg = kit.asg("A2010", "WELD_JOINTS")
    sup = kit.world.sup.id

    def write(conn, claim_id, cum):
        conn.execute("select set_config('app.actor_id', %s, true)", (str(sup),))
        d = conn.execute("insert into planner_decisions (project_id, event_id, selected_activity_uid, action, method, justification, decided_by) "
                         "values (%s,%s,%s,'APPROVE','QUANTITIES_AS_CLAIMED','raw writer',%s) returning decision_id", (kit.project, claim_id, kit.uid("A2010"), sup)).fetchone()["decision_id"]
        conn.execute("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty) values (%s,%s,%s,%s,current_date,%s)",
                     (kit.project, kit.uid("A2010"), asg, d, cum))
        conn.execute("insert into notifications (project_id, recipient_id, notification_type, decision_id, event_id, title) values (%s,%s,'CLAIM_DECISION',%s,%s,'x')", (kit.project, kit.world.se.id, d, claim_id))
        conn.execute("insert into audit_logs (project_id, actor_id, action, entity_type, entity_id, payload_hash, previous_hash, current_hash) values (%s,%s,'RAW','PLANNER_DECISION',%s,'h','p',%s)",
                     (kit.project, sup, str(d), "raw" + uuid.uuid4().hex))
    import os, psycopg, psycopg.rows
    ca = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
    cb = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
    try:
        write(ca, a, 100)                                                                            # A holds the assignment's lock, uncommitted
        done = threading.Event()
        err = []

        def run_b():
            try:
                write(cb, b, 150)
                cb.commit()
            except Exception as e:                                                                  # noqa: BLE001
                err.append(e)
            finally:
                done.set()
        t = threading.Thread(target=run_b); t.start()
        assert not done.wait(1.0), "writer B should be waiting for writer A's lock"
        ca.commit()
        assert done.wait(10) and err == []
        t.join()
    finally:
        ca.close(); cb.close()
    led, _ = kit.ledger()
    assert [(e["cumulative_qty"], e["incremental_qty"], e["prev_cumulative_qty"]) for e in led] == [(100, 100, 0), (150, 50, 100)]
    assert led[1]["prev_entry_id"] == led[0]["entry_id"]


def test_the_chain_cannot_fork_even_if_the_lock_were_missing(kit):
    """belt and braces: with the guard trigger DISABLED (as if the advisory lock did not exist) the unique indexes on the chain links still refuse a
    second successor of the same entry and a second root for an assignment. (Done inside a transaction that is rolled back.)"""
    import psycopg
    kit.approve(kit.submit("A2010", 100, "joints", days_ago=2)["claim_id"])
    kit.approve(kit.submit("A2010", 250, "joints", days_ago=1)["claim_id"])
    rej = kit.approve(kit.submit("A2010", 999, "joints", text="third, rejected")["claim_id"], action="REJECT", justification="not supported")     # just a decision row to point at
    led, _ = kit.ledger()
    row = lambda prev: ("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty, incremental_qty, prev_cumulative_qty, "
                        "baseline_qty_at_entry, prev_entry_id) values (%s,%s,%s,%s,current_date,300,50,250,2000,%s)",
                        (kit.project, kit.uid("A2010"), kit.asg("A2010", "WELD_JOINTS"), rej["decision_id"], prev))

    class Undo(Exception):
        pass
    with connect() as c:
        c.execute("select set_config('app.system','on',false)")
        try:
            with c.transaction():
                c.execute("alter table approved_resource_progress disable trigger trg_arp_guard")
                with pytest.raises(psycopg.errors.UniqueViolation, match="uq_arp_chain_next"):
                    with c.transaction():
                        c.execute(*row(led[0]["entry_id"]))                      # entry 0 already has a successor
                with pytest.raises(psycopg.errors.UniqueViolation, match="uq_arp_chain_root"):
                    with c.transaction():
                        c.execute(*row(None))                                   # the assignment already has its root
                raise Undo()
        except Undo:
            pass
    assert len(kit.ledger()[0]) == 2
