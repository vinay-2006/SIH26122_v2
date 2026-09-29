"""
Repositories for Quality Gates/Checkpoints and Quality Evidence operations.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from backend.repositories.base import BaseRepository


class QualityGateRepository(BaseRepository):
    """Project-scoped repository for Quality Gates & Checkpoints."""

    @classmethod
    def create(
        cls,
        user_id: uuid.UUID | str,
        project_id: uuid.UUID,
        gate_name: str,
        gate_type: str,
        checkpoint_category: str = "QUALITY_CHECK",
        stage_id: Optional[uuid.UUID] = None,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
        itp_id: Optional[uuid.UUID] = None,
        contractor_id: Optional[uuid.UUID] = None,
        work_package_id: Optional[uuid.UUID] = None,
        required: bool = True,
        due_date: Optional[date] = None,
        remarks: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a new quality gate / checkpoint record."""
        query = """
            INSERT INTO quality_gates (
                project_id, gate_name, gate_type, checkpoint_category,
                stage_id, schedule_id, activity_id, itp_id, contractor_id, work_package_id,
                required, status, due_date, remarks
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, 'PENDING', %s, %s
            ) RETURNING *;
        """
        params = (
            str(project_id),
            gate_name,
            gate_type,
            checkpoint_category,
            str(stage_id) if stage_id else None,
            schedule_id,
            activity_id,
            str(itp_id) if itp_id else None,
            str(contractor_id) if contractor_id else None,
            str(work_package_id) if work_package_id else None,
            required,
            due_date,
            remarks,
        )

        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    @classmethod
    def get_by_id(cls, user_id: uuid.UUID | str, quality_gate_id: uuid.UUID, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Retrieves a quality gate by ID within the specified project."""
        query = """
            SELECT * FROM quality_gates 
            WHERE quality_gate_id = %s AND project_id = %s;
        """
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (str(quality_gate_id), str(project_id)))
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list_by_activity(cls, user_id: uuid.UUID | str, project_id: uuid.UUID, activity_id: str) -> List[Dict[str, Any]]:
        """Lists all quality gates for a specific activity in a project."""
        query = """
            SELECT * FROM quality_gates 
            WHERE project_id = %s AND activity_id = %s
            ORDER BY created_at ASC;
        """
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (str(project_id), activity_id))
                rows = cur.fetchall()
                return [dict(row) for row in rows]

    @classmethod
    def update_status(
        cls,
        user_id: uuid.UUID | str,
        quality_gate_id: uuid.UUID,
        project_id: uuid.UUID,
        status: str,
        remarks: Optional[str] = None,
        waiver_reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Updates quality gate status (PASSED, FAILED, WAIVED)."""
        now = datetime.now(timezone.utc)
        passed_at = now if status == "PASSED" else None
        passed_by = str(user_id) if status == "PASSED" else None
        waived_at = now if status == "WAIVED" else None
        waived_by = str(user_id) if status == "WAIVED" else None

        query = """
            UPDATE quality_gates 
            SET status = %s,
                passed_at = COALESCE(%s, passed_at),
                passed_by = COALESCE(%s::uuid, passed_by),
                waived_at = COALESCE(%s, waived_at),
                waived_by = COALESCE(%s::uuid, waived_by),
                waiver_reason = COALESCE(%s, waiver_reason),
                remarks = COALESCE(%s, remarks),
                updated_at = now()
            WHERE quality_gate_id = %s AND project_id = %s
            RETURNING *;
        """
        params = (
            status,
            passed_at,
            passed_by,
            waived_at,
            waived_by,
            waiver_reason,
            remarks,
            str(quality_gate_id),
            str(project_id),
        )

        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None


class QualityEvidenceRepository(BaseRepository):
    """Project-scoped repository for Quality Evidence."""

    @classmethod
    def create(
        cls,
        user_id: uuid.UUID | str,
        quality_gate_id: uuid.UUID,
        source_document_id: Optional[str] = None,
        evidence_type: str = "OTHER",
        result: str = "PASS",
        inspector_name: Optional[str] = None,
        inspection_date: Optional[date] = None,
        evidence_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Creates a quality evidence record linked to a quality gate."""
        import json
        query = """
            INSERT INTO quality_evidence (
                quality_gate_id, source_document_id, evidence_type, result,
                inspector_name, inspection_date, evidence_hash, metadata
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb
            ) RETURNING *;
        """
        params = (
            str(quality_gate_id),
            source_document_id,
            evidence_type,
            result,
            inspector_name,
            inspection_date,
            evidence_hash,
            json.dumps(metadata or {}),
        )

        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    @classmethod
    def list_by_gate(cls, user_id: uuid.UUID | str, quality_gate_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Lists all evidence records for a given quality gate."""
        query = """
            SELECT * FROM quality_evidence 
            WHERE quality_gate_id = %s
            ORDER BY created_at DESC;
        """
        with cls.rls_connection(user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (str(quality_gate_id),))
                rows = cur.fetchall()
                return [dict(row) for row in rows]
