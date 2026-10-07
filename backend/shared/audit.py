import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from backend.shared.db import get_connection

GENESIS_HASH = "0" * 64

# Rows written by append_audit_record carry this marker in entity_context. Rows without it are LEGACY /
# PRE-V7 history: written before the append path was fixed (previous_hash "GENESIS" on every row, a different
# hash scheme, or the V6 project-less global chain). They are never rewritten and never "repaired"; they are
# reported as unverifiable, and the V7 chain is verified from its anchor onward.
CHAIN_MARKER_KEY = "_chain"
CHAIN_MARKER_V7 = "V7"


def canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_hash(
    entity_type: str,
    entity_id: str,
    action: str,
    actor_id: str,
    before_state: Optional[Any],
    after_state: Optional[Any],
    payload_hash: str,
    previous_hash: str,
) -> str:
    payload = canonical_json(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "actor_id": actor_id,
            "before_state": before_state,
            "after_state": after_state,
            "payload_hash": payload_hash,
            "previous_hash": previous_hash,
        }
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def payload_hash(before_state: Optional[Any], after_state: Optional[Any]) -> str:
    before_str = canonical_json(before_state) if before_state is not None else ""
    after_str = canonical_json(after_state) if after_state is not None else ""
    return hashlib.sha256((before_str + after_str).encode("utf-8")).hexdigest()


def get_previous_hash() -> str:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT current_hash
            FROM audit_logs
            ORDER BY log_id DESC
            LIMIT 1
            """
        ).fetchone()

    return row["current_hash"] if row else GENESIS_HASH


def _row_hash(row: Any) -> Optional[str]:
    if row is None:
        return None
    return row["current_hash"] if isinstance(row, dict) else row[0]


def append_audit_record(
    conn_or_cur: Any,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor_id: str,
    before_state: Optional[Any],
    after_state: Optional[Any],
    project_id: Optional[Any] = None,
    schedule_id: Optional[str] = None,
    role: Optional[str] = None,
    entity_context: Optional[Any] = None,
) -> int:
    """
    The ONLY way audit rows are appended. Append-only, per-chain hash linkage.

    Chain = one project (project_id given) or the legacy project-less chain (project_id None).
    Within the caller's open transaction it:
      1. takes a transaction-scoped advisory lock for the chain, so concurrent appenders to the same
         chain serialise (the second one reads the first one's committed hash, never a stale one);
      2. reads the chain head (last row by log_id);
      3. links previous_hash = head.current_hash (GENESIS_HASH for the first row);
      4. hashes with payload_hash()/compute_hash(), the exact scheme verify_audit_chain() checks;
      5. INSERTs. Historical rows are never read-for-update, modified or deleted.
    The caller owns commit/rollback: rolling back also removes the row and releases the lock, so a
    failed action never leaves a gap or a fork.
    `conn_or_cur` may be a psycopg connection or cursor (dict or tuple rows).
    """
    nb = _normalize_state(before_state)
    na = _normalize_state(after_state)
    ph = payload_hash(nb, na)

    chain_key = f"audit_chain:{project_id if project_id is not None else 'GLOBAL'}"
    conn_or_cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (chain_key,))

    if project_id is None:
        head = conn_or_cur.execute(
            "SELECT current_hash FROM audit_logs WHERE project_id IS NULL ORDER BY log_id DESC LIMIT 1"
        ).fetchone()
    else:
        head = conn_or_cur.execute(
            "SELECT current_hash FROM audit_logs WHERE project_id = %s ORDER BY log_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    previous_hash = _row_hash(head) or GENESIS_HASH

    current_hash = compute_hash(
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        actor_id=str(actor_id),
        before_state=nb,
        after_state=na,
        payload_hash=ph,
        previous_hash=previous_hash,
    )

    row = conn_or_cur.execute(
        """
        INSERT INTO audit_logs (
            project_id, schedule_id, actor_id, role,
            entity_type, entity_id, action, before_state, after_state,
            payload_hash, previous_hash, current_hash, entity_context
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING log_id
        """,
        (
            project_id,
            schedule_id,
            str(actor_id),
            role,
            entity_type,
            str(entity_id),
            action,
            canonical_json(nb) if nb is not None else None,
            canonical_json(na) if na is not None else None,
            ph,
            previous_hash,
            current_hash,
            canonical_json({**(entity_context if isinstance(entity_context, dict) else {}), CHAIN_MARKER_KEY: CHAIN_MARKER_V7}),
        ),
    ).fetchone()
    return row["log_id"] if isinstance(row, dict) else row[0]


def write_audit_log(
    entity_type: str,
    entity_id: str,
    action: str,
    actor_id: str,
    before_state: Optional[Any],
    after_state: Optional[Any],
    payload: Any = None,
    project_id: Optional[Any] = None,
    schedule_id: Optional[str] = None,
    role: Optional[str] = None,
) -> int:
    """Own-transaction wrapper over append_audit_record(). Without project_id it appends to the
    legacy project-less chain (V6 behaviour); pass project_id to append to that project's chain."""
    with get_connection() as conn:
        with conn.transaction():
            return append_audit_record(
                conn,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_id=actor_id,
                before_state=before_state,
                after_state=after_state,
                project_id=project_id,
                schedule_id=schedule_id,
                role=role,
            )


def list_recent_audit_logs(limit: int = 20, project_id: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Most recent audit_logs rows, newest first -- read-only convenience
    for a general activity feed (e.g. Activity History's audit trail card),
    as distinct from get_audit_trail()'s per-entity chain view.
    Operational callers MUST pass project_id: the feed is then that project's chain only."""
    safe_limit = max(1, min(limit, 200))
    with get_connection() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT %s", (safe_limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id DESC LIMIT %s",
                (project_id, safe_limit),
            ).fetchall()
        return [dict(r) for r in rows]


def _normalize_state(val: Any) -> Any:
    """Normalize state from database row or caller into native deserialized structure for consistent hashing."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def verify_audit_chain(
    logs: List[Dict[str, Any]],
    expected_genesis: str = GENESIS_HASH,
    allow_subchain: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    Verify full cryptographic audit chain integrity over a sequence of audit logs.

    Verifies:
    1. payload_hash recomputation from before_state + after_state.
    2. current_hash recomputation using compute_hash.
    3. previous_hash linkage:
       - For the first entry: previous_hash == expected_genesis (or matches expected start hash if allow_subchain).
       - For entry i > 0: previous_hash == logs[i-1]["current_hash"].
    4. Monotonic sequence ordering: log_id strictly increases.
    5. Entity / metadata integrity.

    Returns:
      (True, None) if completely valid.
      (False, failure_reason) if tampered or invalid.
    """
    if not logs:
        return True, None

    for i, log in enumerate(logs):
        log_id = log.get("log_id")

        # 1. Sequence order check
        if i > 0 and log_id is not None and logs[i - 1].get("log_id") is not None:
            if log_id <= logs[i - 1]["log_id"]:
                return False, f"Sequence order violation at index {i}: log_id {log_id} <= previous {logs[i-1]['log_id']}"

        # 2. Previous hash linkage check
        prev_hash = log.get("previous_hash")
        if i == 0:
            if not allow_subchain and prev_hash != expected_genesis:
                return False, f"Genesis/start linkage violation at index 0: expected {expected_genesis}, got {prev_hash}"
        else:
            expected_prev = logs[i - 1].get("current_hash")
            if prev_hash != expected_prev:
                return False, f"Chain linkage violation at index {i}: expected previous_hash '{expected_prev}', got '{prev_hash}'"

        # 3. Verify payload_hash
        before = _normalize_state(log.get("before_state"))
        after = _normalize_state(log.get("after_state"))
        recomputed_payload_hash = payload_hash(before, after)
        if log.get("payload_hash") != recomputed_payload_hash:
            return False, f"Payload hash mismatch at index {i} (log_id={log_id}): stored '{log.get('payload_hash')}', recomputed '{recomputed_payload_hash}'"

        # 4. Verify current_hash
        recomputed_curr_hash = compute_hash(
            entity_type=log.get("entity_type", ""),
            entity_id=str(log.get("entity_id", "")),
            action=log.get("action", ""),
            actor_id=str(log.get("actor_id", "")),
            before_state=before,
            after_state=after,
            payload_hash=log.get("payload_hash", ""),
            previous_hash=prev_hash or "",
        )
        if log.get("current_hash") != recomputed_curr_hash:
            return False, f"Current hash mismatch at index {i} (log_id={log_id}): stored '{log.get('current_hash')}', recomputed '{recomputed_curr_hash}'"

    return True, None


def get_audit_trail(entity_id: str, project_id: Optional[Any] = None) -> Dict[str, Any]:
    """Per-entity audit trail. Operational callers MUST pass project_id (rows of other projects, and
    legacy project-less rows, are then excluded)."""
    with get_connection() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE entity_id = %s ORDER BY log_id ASC", (entity_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE entity_id = %s AND project_id = %s ORDER BY log_id ASC",
                (entity_id, project_id),
            ).fetchall()

        logs = [dict(r) for r in rows]
        is_valid, reason = verify_audit_chain(logs, allow_subchain=True)

        return {
            "entity_id": entity_id,
            "chain_valid": is_valid,
            "verification_detail": reason,
            "logs": logs,
        }


def _is_v7_row(log: Dict[str, Any]) -> bool:
    ctx = log.get("entity_context")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except Exception:
            ctx = None
    return isinstance(ctx, dict) and ctx.get(CHAIN_MARKER_KEY) == CHAIN_MARKER_V7


def verify_project_chain_rows(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Classify and verify ONE project's complete audit sequence (all rows of the project, ascending log_id).

      LEGACY / PRE-V7  rows without the V7 marker. Unverifiable by design; counted, never rewritten.
                       They may only form a PREFIX: an unmarked row after a V7 row is BROKEN.
      V7 CHAIN         marked rows. The first one must link to the last legacy row's current_hash (the anchor)
                       or, when there is no legacy history, to GENESIS_HASH. Every row must recompute
                       (payload_hash, current_hash) and link to its predecessor.

    status: VALID (V7 chain verified) | BROKEN | LEGACY_ONLY (nothing to verify yet) | EMPTY
    failure_type (BROKEN only): TAMPERED_CONTENT | BROKEN_LINK (a record is missing, reordered or replaced)
                                | UNMARKED_ROW_IN_V7_CHAIN | SEQUENCE_ORDER
    Nothing is repaired; the first failure is reported with its log_id.
    """
    result: Dict[str, Any] = {
        "status": "EMPTY", "records_checked": len(logs), "legacy_records": 0, "v7_records": 0,
        "first_log_id": logs[0].get("log_id") if logs else None, "last_log_id": logs[-1].get("log_id") if logs else None,
        "legacy_first_log_id": None, "legacy_last_log_id": None, "anchor_log_id": None,
        "broken_at_log_id": None, "failure_type": None, "reason": None, "expected_hash": None, "actual_hash": None,
    }
    if not logs:
        return result

    legacy: List[Dict[str, Any]] = []
    v7: List[Dict[str, Any]] = []
    for i, log in enumerate(logs):
        if i > 0 and log.get("log_id") is not None and logs[i - 1].get("log_id") is not None and log["log_id"] <= logs[i - 1]["log_id"]:
            return {**result, "status": "BROKEN", "failure_type": "SEQUENCE_ORDER", "broken_at_log_id": log.get("log_id"),
                    "reason": f"log_id {log['log_id']} does not increase after {logs[i - 1]['log_id']}"}
        if _is_v7_row(log):
            v7.append(log)
        elif v7:
            return {**result, "status": "BROKEN", "failure_type": "UNMARKED_ROW_IN_V7_CHAIN", "broken_at_log_id": log.get("log_id"),
                    "legacy_records": len(legacy), "v7_records": len(v7),
                    "reason": "an unmarked (legacy) row appears after V7-chain rows"}
        else:
            legacy.append(log)

    result["legacy_records"], result["v7_records"] = len(legacy), len(v7)
    if legacy:
        result["legacy_first_log_id"], result["legacy_last_log_id"] = legacy[0].get("log_id"), legacy[-1].get("log_id")
        result["anchor_log_id"] = legacy[-1].get("log_id")
    if not v7:
        return {**result, "status": "LEGACY_ONLY",
                "reason": "only legacy / pre-V7 audit history exists for this project; it cannot be verified"}

    expected_prev = legacy[-1].get("current_hash") if legacy else GENESIS_HASH
    for row in v7:
        log_id = row.get("log_id")
        if row.get("previous_hash") != expected_prev:
            return {**result, "status": "BROKEN", "failure_type": "BROKEN_LINK", "broken_at_log_id": log_id,
                    "expected_hash": expected_prev, "actual_hash": row.get("previous_hash"),
                    "reason": f"link to the previous record is broken at log_id {log_id} (a record is missing, reordered or replaced)"}
        before, after = _normalize_state(row.get("before_state")), _normalize_state(row.get("after_state"))
        recomputed_payload = payload_hash(before, after)
        if row.get("payload_hash") != recomputed_payload:
            return {**result, "status": "BROKEN", "failure_type": "TAMPERED_CONTENT", "broken_at_log_id": log_id,
                    "expected_hash": recomputed_payload, "actual_hash": row.get("payload_hash"),
                    "reason": f"payload hash mismatch at log_id {log_id}: the recorded before/after state was altered"}
        recomputed = compute_hash(
            entity_type=row.get("entity_type", ""), entity_id=str(row.get("entity_id", "")), action=row.get("action", ""),
            actor_id=str(row.get("actor_id", "")), before_state=before, after_state=after,
            payload_hash=row.get("payload_hash", ""), previous_hash=row.get("previous_hash") or "",
        )
        if row.get("current_hash") != recomputed:
            return {**result, "status": "BROKEN", "failure_type": "TAMPERED_CONTENT", "broken_at_log_id": log_id,
                    "expected_hash": recomputed, "actual_hash": row.get("current_hash"),
                    "reason": f"record hash mismatch at log_id {log_id}: the record was altered"}
        expected_prev = row.get("current_hash")
    return {**result, "status": "VALID"}
