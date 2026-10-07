"""
DB-free: V7 audit chain verification vs LEGACY / PRE-V7 history (backend.shared.audit.verify_project_chain_rows).

Rules under test: legacy rows are only counted (never repaired, never "valid"); the V7 chain is verified from its
anchor onward; tampering, a missing record, a replaced record and an unmarked row inside the V7 chain are all
reported with the offending log_id; results are deterministic.
"""
import copy
import json

from backend.shared.audit import (
    CHAIN_MARKER_KEY,
    CHAIN_MARKER_V7,
    GENESIS_HASH,
    canonical_json,
    compute_hash,
    payload_hash,
    verify_project_chain_rows,
)


def _v7_row(log_id, prev, n, action="ACT"):
    before, after = None, {"n": n}
    ph = payload_hash(before, after)
    row = {
        "log_id": log_id, "entity_type": "T", "entity_id": f"e{n}", "action": action, "actor_id": "u1",
        "before_state": None, "after_state": canonical_json(after), "payload_hash": ph, "previous_hash": prev,
        "entity_context": {CHAIN_MARKER_KEY: CHAIN_MARKER_V7},
    }
    row["current_hash"] = compute_hash("T", f"e{n}", action, "u1", before, after, ph, prev)
    return row


def _legacy_row(log_id, n):
    return {
        "log_id": log_id, "entity_type": "T", "entity_id": f"old{n}", "action": "OLD", "actor_id": "u0",
        "before_state": "{}", "after_state": '{"x":1}', "payload_hash": "aa" * 32, "previous_hash": "GENESIS",
        "current_hash": f"{n:064x}", "entity_context": {"old_state": {}, "new_state": {"x": 1}},
    }


def _chain(n, first_prev=GENESIS_HASH, start_id=100):
    rows, prev = [], first_prev
    for i in range(n):
        r = _v7_row(start_id + i, prev, i)
        rows.append(r)
        prev = r["current_hash"]
    return rows


def test_empty_and_legacy_only():
    assert verify_project_chain_rows([])["status"] == "EMPTY"
    res = verify_project_chain_rows([_legacy_row(1, 1), _legacy_row(2, 2)])
    assert res["status"] == "LEGACY_ONLY" and res["legacy_records"] == 2 and res["v7_records"] == 0
    assert res["status"] != "VALID", "unverifiable history must never be reported as valid"


def test_pure_v7_chain_is_valid_from_genesis():
    res = verify_project_chain_rows(_chain(5))
    assert res["status"] == "VALID" and res["v7_records"] == 5 and res["legacy_records"] == 0 and res["anchor_log_id"] is None


def test_v7_chain_anchored_to_legacy_history():
    legacy = [_legacy_row(1, 1), _legacy_row(2, 2)]
    rows = legacy + _chain(3, first_prev=legacy[-1]["current_hash"], start_id=3)
    res = verify_project_chain_rows(rows)
    assert res["status"] == "VALID"
    assert (res["legacy_records"], res["v7_records"], res["anchor_log_id"]) == (2, 3, 2)


def test_v7_chain_not_linked_to_the_anchor_is_broken():
    legacy = [_legacy_row(1, 1)]
    res = verify_project_chain_rows(legacy + _chain(2, first_prev=GENESIS_HASH, start_id=2))
    assert res["status"] == "BROKEN" and res["failure_type"] == "BROKEN_LINK" and res["broken_at_log_id"] == 2


def test_tampered_content_is_detected_and_located():
    rows = _chain(5)
    rows[2]["after_state"] = canonical_json({"n": 999})
    res = verify_project_chain_rows(rows)
    assert res["status"] == "BROKEN" and res["failure_type"] == "TAMPERED_CONTENT" and res["broken_at_log_id"] == rows[2]["log_id"]


def test_tampered_action_or_actor_is_detected():
    for field, value in (("action", "FORGED"), ("actor_id", "mallory"), ("entity_id", "other")):
        rows = _chain(4)
        rows[1][field] = value
        res = verify_project_chain_rows(rows)
        assert res["status"] == "BROKEN" and res["failure_type"] == "TAMPERED_CONTENT", field


def test_missing_record_is_a_broken_link():
    rows = _chain(5)
    del rows[2]
    res = verify_project_chain_rows(rows)
    assert res["status"] == "BROKEN" and res["failure_type"] == "BROKEN_LINK" and res["broken_at_log_id"] == 103


def test_wrong_previous_hash_and_replaced_record_are_detected():
    rows = _chain(4)
    rows[2]["previous_hash"] = "f" * 64
    assert verify_project_chain_rows(rows)["failure_type"] == "BROKEN_LINK"
    rows = _chain(4)
    forged = _v7_row(102, rows[1]["current_hash"], 77)  # a perfectly self-consistent forged record...
    rows[2] = forged
    res = verify_project_chain_rows(rows)  # ...still breaks the link of the record after it
    assert res["status"] == "BROKEN" and res["broken_at_log_id"] == 103


def test_unmarked_row_after_v7_rows_is_broken_not_silently_legacy():
    rows = _chain(3)
    rows[1]["entity_context"] = {}  # de-marking a V7 row to hide it as "legacy"
    res = verify_project_chain_rows(rows)
    assert res["status"] == "BROKEN" and res["failure_type"] == "UNMARKED_ROW_IN_V7_CHAIN" and res["broken_at_log_id"] == 101


def test_reordered_sequence_is_reported():
    rows = _chain(3)
    rows[0], rows[1] = rows[1], rows[0]
    assert verify_project_chain_rows(rows)["failure_type"] == "SEQUENCE_ORDER"


def test_jsonb_context_as_string_and_deterministic_result():
    rows = _chain(3)
    for r in rows:
        r["entity_context"] = json.dumps(r["entity_context"])
    a = verify_project_chain_rows(copy.deepcopy(rows))
    b = verify_project_chain_rows(copy.deepcopy(rows))
    assert a == b and a["status"] == "VALID"


def test_verification_never_mutates_its_input():
    rows = _chain(3)
    rows[1]["after_state"] = canonical_json({"n": 5})
    snapshot = copy.deepcopy(rows)
    verify_project_chain_rows(rows)
    assert rows == snapshot
