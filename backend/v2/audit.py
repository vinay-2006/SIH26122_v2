"""Tamper-evident audit chain (same hash scheme as the existing system: SHA-256 over canonical JSON, previous_hash linking).
Written inside the caller's transaction, so an action and its audit record commit or roll back together."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

GENESIS = "0" * 64


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(before: Any, after: Any) -> str:
    b = canonical(before) if before is not None else ""
    a = canonical(after) if after is not None else ""
    return hashlib.sha256((b + a).encode()).hexdigest()


def chain_hash(entity_type, entity_id, action, actor_id, before, after, p_hash, previous) -> str:
    return hashlib.sha256(canonical(dict(entity_type=entity_type, entity_id=entity_id, action=action, actor_id=actor_id,
                                         before_state=before, after_state=after, payload_hash=p_hash, previous_hash=previous)).encode()).hexdigest()


def log(conn, *, project_id, actor_id, role: Optional[str], action: str, entity_type: str, entity_id: Any,
        before: Any = None, after: Any = None, version_id=None, context: Optional[dict] = None) -> str:
    conn.execute("select pg_advisory_xact_lock(724301)")                 # serialise chain appends
    row = conn.execute("select current_hash from audit_logs order by log_id desc limit 1").fetchone()
    prev = row["current_hash"] if row else GENESIS
    ph = payload_hash(before, after)
    cur = chain_hash(entity_type, str(entity_id), action, str(actor_id), before, after, ph, prev)
    conn.execute(
        "insert into audit_logs (project_id, actor_id, role, action, entity_type, entity_id, schedule_version_id, before_state, after_state,"
        " entity_context, payload_hash, previous_hash, current_hash) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (project_id, actor_id, role, action, entity_type, str(entity_id), version_id,
         canonical(before) if before is not None else None, canonical(after) if after is not None else None,
         canonical({**(context or {}), "_chain": "V7"}), ph, prev, cur))
    return cur


def verify_chain(conn) -> dict:
    rows = conn.execute("select * from audit_logs order by log_id").fetchall()
    prev, bad = GENESIS, []
    for r in rows:
        before = json.loads(r["before_state"]) if r["before_state"] else None
        after = json.loads(r["after_state"]) if r["after_state"] else None
        exp = chain_hash(r["entity_type"], r["entity_id"], r["action"], str(r["actor_id"]), before, after, r["payload_hash"], prev)
        if r["previous_hash"] != prev or r["current_hash"] != exp:
            bad.append(r["log_id"])
        prev = r["current_hash"]
    return {"valid": not bad, "entries": len(rows), "broken_at": bad}
