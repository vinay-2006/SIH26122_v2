"""
Read-Only Cryptographic Audit-Chain Verifier for SETUAI V7 Phase 13.
Reuses the authoritative hash-chain verification implementation in backend.shared.audit.
Never mutates or repairs audit logs.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.dossier.schemas import AuditChainVerificationResult
from backend.shared.audit import GENESIS_HASH, verify_audit_chain
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


class AuditVerifier:
    """
    Cryptographic verifier over project- and schedule-scoped audit log sequences.
    """

    @classmethod
    def fetch_audit_logs(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Fetches audit logs strictly scoped to context.project_id ordered by log_id ASC.
        Preserves natural monotonic sequence.
        """
        query = """
            SELECT
                log_id, entity_type, entity_id, action, actor_id,
                before_state, after_state, payload_hash, previous_hash,
                current_hash, timestamp, project_id, schedule_id, role
            FROM audit_logs
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id, "limit": limit}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if entity_id:
            query += " AND entity_id = %(entity_id)s"
            params["entity_id"] = entity_id

        query += " ORDER BY log_id ASC LIMIT %(limit)s;"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to fetch audit logs for verification: %s", e)
            return []

    @classmethod
    def verify_chain(
        cls,
        logs: List[Dict[str, Any]],
        expected_genesis: str = GENESIS_HASH,
        allow_subchain: bool = True,
    ) -> AuditChainVerificationResult:
        """
        Runs cryptographic verification over the supplied log sequence.
        Returns a diagnostic result without mutating any state.
        """
        if not logs:
            return AuditChainVerificationResult(
                status="EMPTY",
                records_checked=0,
                first_log_id=None,
                last_log_id=None,
                broken_at_log_id=None,
                reason="No audit records found for this scope.",
                expected_hash=None,
                actual_hash=None,
                verified_at=datetime.utcnow(),
            )

        first_id = logs[0].get("log_id")
        last_id = logs[-1].get("log_id")

        is_valid, failure_reason = verify_audit_chain(
            logs=logs,
            expected_genesis=expected_genesis,
            allow_subchain=allow_subchain,
        )

        if is_valid:
            return AuditChainVerificationResult(
                status="VALID",
                records_checked=len(logs),
                first_log_id=first_id,
                last_log_id=last_id,
                broken_at_log_id=None,
                reason=None,
                expected_hash=None,
                actual_hash=None,
                verified_at=datetime.utcnow(),
            )

        # Parse diagnostic failure details from reason string
        broken_log_id = None
        expected_hash = None
        actual_hash = None

        if failure_reason:
            # Look for index pattern: "at index (\d+)"
            idx_match = re.search(r"at index (\d+)", failure_reason)
            if idx_match:
                failed_idx = int(idx_match.group(1))
                if 0 <= failed_idx < len(logs):
                    broken_log_id = logs[failed_idx].get("log_id")

            # Look for hash mismatch pattern: "expected previous_hash '([^']+)', got '([^']+)'"
            hash_match = re.search(r"expected (?:previous_hash )?'([^']+)', got '([^']+)'", failure_reason)
            if hash_match:
                expected_hash = hash_match.group(1)
                actual_hash = hash_match.group(2)

        return AuditChainVerificationResult(
            status="BROKEN",
            records_checked=len(logs),
            first_log_id=first_id,
            last_log_id=last_id,
            broken_at_log_id=broken_log_id,
            reason=failure_reason or "Cryptographic hash chain verification failed.",
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            verified_at=datetime.utcnow(),
        )

    @classmethod
    def verify_project_chain(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> AuditChainVerificationResult:
        """Convenience method that queries DB and verifies the chain."""
        logs = cls.fetch_audit_logs(context=context, schedule_id=schedule_id)
        return cls.verify_chain(logs, allow_subchain=True)
