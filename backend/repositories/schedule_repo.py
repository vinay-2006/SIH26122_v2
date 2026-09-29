"""
Project-Scoped Schedule Repository for SETUAI V7.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
import psycopg

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository
from backend.shared.db import get_connection
from backend.shared.schedule import parse_schedule_csv

logger = logging.getLogger(__name__)


class ScheduleVersionAlreadyExistsError(Exception):
    """Raised when a schedule version with the given version_code already exists in the project."""


class ScheduleVersionNotFoundError(Exception):
    """Raised when a schedule version is not found in the project context."""


class ProjectScheduleRepository(BaseRepository):
    """
    Project-scoped repository for schedule versions.
    Every operation requires ProjectContext and enforces the project_id boundary.
    """

    @classmethod
    def get(cls, context: ProjectContext, schedule_id: str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.schedule_id, s.project_name, s.project_id, s.version_code, s.active,
                           s.data_date, s.source_format, s.supersedes_schedule_id, s.source_hash,
                           s.version_metadata, s.created_at,
                           (SELECT COUNT(*) FROM schedule_activities sa WHERE sa.schedule_id = s.schedule_id) AS activity_count,
                           (SELECT COUNT(*) FROM schedule_dependencies sd WHERE sd.schedule_id = s.schedule_id) AS dependency_count
                    FROM schedules s
                    WHERE s.schedule_id = %s AND s.project_id = %s;
                    """,
                    (schedule_id, context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(cls, context: ProjectContext) -> List[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.schedule_id, s.project_name, s.project_id, s.version_code, s.active,
                           s.data_date, s.source_format, s.supersedes_schedule_id, s.source_hash,
                           s.version_metadata, s.created_at,
                           (SELECT COUNT(*) FROM schedule_activities sa WHERE sa.schedule_id = s.schedule_id) AS activity_count,
                           (SELECT COUNT(*) FROM schedule_dependencies sd WHERE sd.schedule_id = s.schedule_id) AS dependency_count
                    FROM schedules s
                    WHERE s.project_id = %s
                    ORDER BY s.created_at DESC;
                    """,
                    (context.project_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_active(cls, context: ProjectContext) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.schedule_id, s.project_name, s.project_id, s.version_code, s.active,
                           s.data_date, s.source_format, s.supersedes_schedule_id, s.source_hash,
                           s.version_metadata, s.created_at,
                           (SELECT COUNT(*) FROM schedule_activities sa WHERE sa.schedule_id = s.schedule_id) AS activity_count,
                           (SELECT COUNT(*) FROM schedule_dependencies sd WHERE sd.schedule_id = s.schedule_id) AS dependency_count
                    FROM schedules s
                    WHERE s.project_id = %s AND s.active = TRUE
                    ORDER BY s.created_at DESC
                    LIMIT 1;
                    """,
                    (context.project_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def create_version(
        cls,
        context: ProjectContext,
        version_code: str,
        csv_content: str,
        data_date: Optional[Any] = None,
        source_format: str = "csv",
        version_metadata: Optional[Dict[str, Any]] = None,
        supersedes_schedule_id: Optional[str] = None,
        activate_immediately: bool = False,
    ) -> Dict[str, Any]:
        """
        Creates a new schedule version scoped explicitly to context.project_id.
        Persists schedule metadata, activities, and dependencies atomically.
        """
        schedule_id = str(uuid.uuid4())
        if isinstance(csv_content, bytes):
            bytes_content = csv_content
            source_hash = hashlib.sha256(bytes_content).hexdigest()
        else:
            bytes_content = csv_content.encode("utf-8")
            source_hash = hashlib.sha256(bytes_content).hexdigest()

        meta = version_metadata or {}

        fmt = (source_format or "csv").lower()
        if fmt in ("xer", "p6_xer", "p6") or bytes_content.startswith(b"ERMHDR"):
            from backend.shared.xer_parser import parse_schedule_xer
            parse_result = parse_schedule_xer(bytes_content, schedule_id=schedule_id)
        else:
            text_content = bytes_content.decode("utf-8", errors="replace")
            parse_result = parse_schedule_csv(text_content, schedule_id=schedule_id)

        if not parse_result.is_valid:
            errors_str = "; ".join([e.message for e in parse_result.errors[:5]])
            raise ValueError(f"Schedule file failed validation: {errors_str}")
        if not parse_result.activities:
            raise ValueError("Schedule file contains zero activities")

        # Validate supersedes_schedule_id belongs to same project if supplied
        if supersedes_schedule_id:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM schedules WHERE schedule_id = %s AND project_id = %s;",
                        (supersedes_schedule_id, context.project_id),
                    )
                    if not cur.fetchone():
                        raise ScheduleVersionNotFoundError(
                            f"Superseded schedule '{supersedes_schedule_id}' not found in project '{context.project_id}'"
                        )

        with get_connection() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    if activate_immediately:
                        # Deactivate existing active schedules for this project
                        cur.execute(
                            "UPDATE schedules SET active = FALSE WHERE project_id = %s AND active = TRUE;",
                            (context.project_id,),
                        )

                    cur.execute(
                        """
                        INSERT INTO schedules (
                            schedule_id, project_id, project_name, version_code,
                            version_metadata, source_hash, active, supersedes_schedule_id,
                            data_date, source_format
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING schedule_id, project_id, project_name, version_code,
                                  version_metadata, source_hash, active, supersedes_schedule_id,
                                  data_date, source_format, created_at;
                        """,
                        (
                            schedule_id,
                            context.project_id,
                            context.project_name or "Project",
                            version_code,
                            json.dumps(meta),
                            source_hash,
                            activate_immediately,
                            supersedes_schedule_id,
                            data_date,
                            source_format,
                        ),
                    )
                    created_schedule = dict(cur.fetchone())

                    # Insert activities with project_id
                    for act in parse_result.activities:
                        act_dict = act.model_dump()
                        cur.execute(
                            """
                            INSERT INTO schedule_activities (
                                schedule_id, activity_id, activity_name, wbs_code, discipline,
                                location, asset_tag, planned_start, planned_finish,
                                planned_quantity, uom, baseline_pct_complete,
                                total_float, is_critical, project_id
                            ) VALUES (
                                %(schedule_id)s, %(activity_id)s, %(activity_name)s, %(wbs_code)s, %(discipline)s,
                                %(location)s, %(asset_tag)s, %(planned_start)s, %(planned_finish)s,
                                %(planned_quantity)s, %(uom)s, %(baseline_pct_complete)s,
                                %(total_float)s, %(is_critical)s, %(project_id)s
                            );
                            """,
                            {**act_dict, "project_id": context.project_id},
                        )

                    # Insert dependencies
                    for dep in parse_result.dependencies:
                        dep_dict = dep.model_dump()
                        cur.execute(
                            """
                            INSERT INTO schedule_dependencies (
                                dependency_id, schedule_id, predecessor_activity_id,
                                successor_activity_id, relationship_type, lag_days
                            ) VALUES (
                                %(dependency_id)s, %(schedule_id)s, %(predecessor_activity_id)s,
                                %(successor_activity_id)s, %(relationship_type)s, %(lag_days)s
                            );
                            """,
                            dep_dict,
                        )

                conn.commit()
            except psycopg.errors.UniqueViolation as exc:
                conn.rollback()
                raise ScheduleVersionAlreadyExistsError(
                    f"Version code '{version_code}' already exists for project '{context.project_id}'"
                ) from exc
            except Exception:
                conn.rollback()
                raise

        # Best-effort FAISS index rebuild
        if activate_immediately:
            try:
                from backend.shared import schedule_index
                schedule_index.build_index(schedule_id)
            except Exception as e:
                logger.warning("FAISS indexing skipped or failed for %s: %s", schedule_id, e)

        created_schedule["activity_count"] = len(parse_result.activities)
        created_schedule["dependency_count"] = len(parse_result.dependencies)
        return created_schedule

    @classmethod
    def activate_version(cls, context: ProjectContext, schedule_id: str) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Transactionally activates a schedule version within context.project_id.
        Deactivates any currently active schedule in the project, preserving historical versions.
        Returns (activated_schedule_dict, previous_active_schedule_id).
        """
        with get_connection() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    # Verify target schedule exists in this project
                    cur.execute(
                        """
                        SELECT schedule_id, project_id, version_code, active
                        FROM schedules
                        WHERE schedule_id = %s AND project_id = %s
                        FOR UPDATE;
                        """,
                        (schedule_id, context.project_id),
                    )
                    target = cur.fetchone()
                    if not target:
                        raise ScheduleVersionNotFoundError(
                            f"Schedule version '{schedule_id}' not found in project '{context.project_id}'"
                        )

                    # Find previous active schedule
                    cur.execute(
                        """
                        SELECT schedule_id
                        FROM schedules
                        WHERE project_id = %s AND active = TRUE AND schedule_id != %s;
                        """,
                        (context.project_id, schedule_id),
                    )
                    prev_active = cur.fetchone()
                    prev_active_id = prev_active["schedule_id"] if prev_active else None

                    # Atomic switch: deactivate all other schedules in project
                    cur.execute(
                        """
                        UPDATE schedules
                        SET active = FALSE
                        WHERE project_id = %s AND schedule_id != %s;
                        """,
                        (context.project_id, schedule_id),
                    )

                    # Set target schedule active
                    cur.execute(
                        """
                        UPDATE schedules
                        SET active = TRUE
                        WHERE project_id = %s AND schedule_id = %s
                        RETURNING schedule_id, project_id, project_name, version_code,
                                  active, supersedes_schedule_id, data_date, created_at;
                        """,
                        (context.project_id, schedule_id),
                    )
                    activated = dict(cur.fetchone())

                conn.commit()

                # Rebuild FAISS index for active schedule
                try:
                    from backend.shared import schedule_index
                    schedule_index.build_index(schedule_id)
                except Exception as e:
                    logger.warning("FAISS index update skipped for %s: %s", schedule_id, e)

                return activated, prev_active_id
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def supersede_version(
        cls,
        context: ProjectContext,
        schedule_id: str,
        supersedes_schedule_id: str,
    ) -> Dict[str, Any]:
        """
        Records that schedule_id supersedes supersedes_schedule_id.
        Both versions must belong to context.project_id.
        Historical versions and claims remain untouched.
        """
        with get_connection() as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    # Check target schedule exists in project
                    cur.execute(
                        "SELECT 1 FROM schedules WHERE schedule_id = %s AND project_id = %s;",
                        (schedule_id, context.project_id),
                    )
                    if not cur.fetchone():
                        raise ScheduleVersionNotFoundError(
                            f"Schedule version '{schedule_id}' not found in project '{context.project_id}'"
                        )

                    # Check superseded schedule exists in project
                    cur.execute(
                        "SELECT 1 FROM schedules WHERE schedule_id = %s AND project_id = %s;",
                        (supersedes_schedule_id, context.project_id),
                    )
                    if not cur.fetchone():
                        raise ScheduleVersionNotFoundError(
                            f"Superseded schedule '{supersedes_schedule_id}' not found in project '{context.project_id}'"
                        )

                    cur.execute(
                        """
                        UPDATE schedules
                        SET supersedes_schedule_id = %s
                        WHERE schedule_id = %s AND project_id = %s
                        RETURNING schedule_id, project_id, project_name, version_code,
                                  active, supersedes_schedule_id, created_at;
                        """,
                        (supersedes_schedule_id, schedule_id, context.project_id),
                    )
                    updated = dict(cur.fetchone())

                conn.commit()
                return updated
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def compare_metadata(
        cls,
        context: ProjectContext,
        schedule_a_id: str,
        schedule_b_id: str,
    ) -> Dict[str, Any]:
        """
        Compares metadata between two schedule versions within the authorized project.
        Evaluates versions, source hashes, dates, and activity/dependency counts.
        Metadata comparison only (no CPM calculations).
        """
        sched_a = cls.get(context, schedule_a_id)
        if not sched_a:
            raise ScheduleVersionNotFoundError(
                f"Schedule '{schedule_a_id}' not found in project '{context.project_id}'"
            )

        sched_b = cls.get(context, schedule_b_id)
        if not sched_b:
            raise ScheduleVersionNotFoundError(
                f"Schedule '{schedule_b_id}' not found in project '{context.project_id}'"
            )

        # Get activity summary bounds for both
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT MIN(planned_start) AS earliest_start, MAX(planned_finish) AS latest_finish
                    FROM schedule_activities
                    WHERE schedule_id = %s AND project_id = %s;
                    """,
                    (schedule_a_id, context.project_id),
                )
                bounds_a = dict(cur.fetchone() or {})

                cur.execute(
                    """
                    SELECT MIN(planned_start) AS earliest_start, MAX(planned_finish) AS latest_finish
                    FROM schedule_activities
                    WHERE schedule_id = %s AND project_id = %s;
                    """,
                    (schedule_b_id, context.project_id),
                )
                bounds_b = dict(cur.fetchone() or {})

        meta_a = {
            "schedule_id": sched_a["schedule_id"],
            "version_code": sched_a["version_code"],
            "active": sched_a["active"],
            "data_date": str(sched_a["data_date"]) if sched_a["data_date"] else None,
            "source_hash": sched_a["source_hash"],
            "activity_count": sched_a["activity_count"],
            "dependency_count": sched_a["dependency_count"],
            "earliest_planned_start": str(bounds_a.get("earliest_start")) if bounds_a.get("earliest_start") else None,
            "latest_planned_finish": str(bounds_a.get("latest_finish")) if bounds_a.get("latest_finish") else None,
            "supersedes_schedule_id": sched_a["supersedes_schedule_id"],
        }

        meta_b = {
            "schedule_id": sched_b["schedule_id"],
            "version_code": sched_b["version_code"],
            "active": sched_b["active"],
            "data_date": str(sched_b["data_date"]) if sched_b["data_date"] else None,
            "source_hash": sched_b["source_hash"],
            "activity_count": sched_b["activity_count"],
            "dependency_count": sched_b["dependency_count"],
            "earliest_planned_start": str(bounds_b.get("earliest_start")) if bounds_b.get("earliest_start") else None,
            "latest_planned_finish": str(bounds_b.get("latest_finish")) if bounds_b.get("latest_finish") else None,
            "supersedes_schedule_id": sched_b["supersedes_schedule_id"],
        }

        differences = {
            "version_code_changed": meta_a["version_code"] != meta_b["version_code"],
            "source_hash_changed": meta_a["source_hash"] != meta_b["source_hash"],
            "activity_count_diff": meta_b["activity_count"] - meta_a["activity_count"],
            "dependency_count_diff": meta_b["dependency_count"] - meta_a["dependency_count"],
            "data_date_changed": meta_a["data_date"] != meta_b["data_date"],
        }

        return {
            "project_id": str(context.project_id),
            "schedule_a": meta_a,
            "schedule_b": meta_b,
            "differences": differences,
        }
