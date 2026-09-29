"""
Adapters for Institutional Memory Retrieval in SETUAI V7 Phase 11.
Provides optional embedding/vector adapter, database memory provider, and in-memory test provider.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.memory.interfaces import MemoryProvider, SemanticEncoder
from backend.memory.schemas import MemoryFilter, MemoryRecord
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# 1. OPTIONAL EMBEDDING / VECTOR ADAPTER
# ============================================================================

class SentenceTransformersAdapter(SemanticEncoder):
    """
    Adapter implementing SemanticEncoder using sentence-transformers (all-MiniLM-L6-v2).
    Reuses the repository's established CPU configuration without crashing if unavailable.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._initialized = False

    def _load_model(self):
        if not self._initialized:
            self._initialized = True
            try:
                from sentence_transformers import SentenceTransformer
                try:
                    self._model = SentenceTransformer(
                        self.model_name,
                        device="cpu",
                        model_kwargs={"local_files_only": True},
                    )
                except Exception:
                    self._model = SentenceTransformer(self.model_name, device="cpu")
                logger.info("SentenceTransformersAdapter loaded model '%s' on CPU", self.model_name)
            except Exception as e:
                logger.warning("SentenceTransformersAdapter unavailable (%s); fallback to lexical", e)
                self._model = None

    def is_available(self) -> bool:
        self._load_model()
        return self._model is not None

    def encode_text(self, text: str) -> Optional[List[float]]:
        if not self.is_available() or not text.strip():
            return None
        try:
            vec = self._model.encode(
                text,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return vec.tolist()
        except Exception as e:
            logger.warning("Encoding failed in SentenceTransformersAdapter: %s", e)
            return None

    def compute_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Computes dot product of two L2-normalized vectors (cosine similarity)."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        return sum(a * b for a, b in zip(vec1, vec2))


# ============================================================================
# 2. IN-MEMORY MEMORY PROVIDER (TESTS / INJECTION BY MEMBER 2)
# ============================================================================

class InMemoryMemoryProvider(MemoryProvider):
    """
    In-memory store of MemoryRecord items.
    Enforces strict project_id isolation during retrieval.
    """

    def __init__(self):
        self._records: Dict[uuid.UUID, List[MemoryRecord]] = {}

    def add_record(self, record: MemoryRecord) -> None:
        proj_id = record.project_id
        if proj_id not in self._records:
            self._records[proj_id] = []
        self._records[proj_id].append(record)

    def clear(self) -> None:
        self._records.clear()

    def get_candidates(
        self,
        context: ProjectContext,
        filters: Optional[MemoryFilter] = None,
    ) -> List[MemoryRecord]:
        project_records = self._records.get(context.project_id, [])
        if not filters:
            return list(project_records)

        candidates = []
        for rec in project_records:
            # Metadata filter checks
            if filters.schedule_id and rec.schedule_id and rec.schedule_id != filters.schedule_id:
                continue
            if filters.stage_id and rec.stage_id and rec.stage_id != filters.stage_id:
                continue
            if filters.activity_id and rec.activity_id and rec.activity_id != filters.activity_id:
                continue
            if filters.contractor_id and rec.contractor_id and rec.contractor_id != filters.contractor_id:
                continue
            if filters.work_package_id and rec.work_package_id and rec.work_package_id != filters.work_package_id:
                continue
            if filters.incident_type and rec.incident_type:
                if rec.incident_type.upper() != filters.incident_type.upper():
                    continue
            if filters.severity and rec.severity:
                if rec.severity.upper() != filters.severity.upper():
                    continue
            if filters.status and rec.status:
                if rec.status.upper() != filters.status.upper():
                    continue
            if filters.date_from and rec.created_at:
                rec_date = rec.created_at.date() if isinstance(rec.created_at, datetime) else rec.created_at
                if rec_date < filters.date_from:
                    continue
            if filters.date_to and rec.created_at:
                rec_date = rec.created_at.date() if isinstance(rec.created_at, datetime) else rec.created_at
                if rec_date > filters.date_to:
                    continue

            candidates.append(rec)

        return candidates


# ============================================================================
# 3. DATABASE MEMORY PROVIDER (READ-ONLY ADAPTER OVER EXISTING INCIDENTS TABLE)
# ============================================================================

class DatabaseMemoryProvider(MemoryProvider):
    """
    Read-only adapter that maps existing institutional_incidents rows into
    normalized MemoryRecord structures.
    Enforces context.project_id isolation in SQL.
    Does NOT modify the incident table or enforce domain workflows.
    """

    def get_candidates(
        self,
        context: ProjectContext,
        filters: Optional[MemoryFilter] = None,
    ) -> List[MemoryRecord]:
        query = """
            SELECT
                incident_id, project_id, stage_id, activity_id, contractor_id,
                discipline, incident_type, title, narrative, root_cause,
                delay_days, cost_impact, corrective_action, lessons_learned,
                recorded_at, status
            FROM institutional_incidents
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id}

        if filters:
            if filters.stage_id:
                query += " AND stage_id = %(stage_id)s"
                params["stage_id"] = filters.stage_id
            if filters.activity_id:
                query += " AND activity_id = %(activity_id)s"
                params["activity_id"] = filters.activity_id
            if filters.contractor_id:
                query += " AND contractor_id = %(contractor_id)s"
                params["contractor_id"] = filters.contractor_id
            if filters.incident_type:
                query += " AND UPPER(incident_type) = UPPER(%(incident_type)s)"
                params["incident_type"] = filters.incident_type
            if filters.status:
                query += " AND UPPER(status) = UPPER(%(status)s)"
                params["status"] = filters.status
            if filters.date_from:
                query += " AND recorded_at >= %(date_from)s"
                params["date_from"] = filters.date_from
            if filters.date_to:
                query += " AND recorded_at <= %(date_to)s"
                params["date_to"] = filters.date_to

        query += " ORDER BY recorded_at DESC LIMIT 200;"

        records: List[MemoryRecord] = []
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    for r in rows:
                        row_dict = dict(r)
                        content_parts = []
                        if row_dict.get("narrative"):
                            content_parts.append(row_dict["narrative"])
                        if row_dict.get("root_cause"):
                            content_parts.append(f"Root Cause: {row_dict['root_cause']}")
                        if row_dict.get("corrective_action"):
                            content_parts.append(f"Corrective Action: {row_dict['corrective_action']}")
                        if row_dict.get("lessons_learned"):
                            content_parts.append(f"Lessons Learned: {row_dict['lessons_learned']}")

                        records.append(
                            MemoryRecord(
                                memory_id=str(row_dict["incident_id"]),
                                project_id=row_dict["project_id"],
                                stage_id=row_dict.get("stage_id"),
                                activity_id=row_dict.get("activity_id"),
                                contractor_id=row_dict.get("contractor_id"),
                                title=row_dict.get("title") or "Incident",
                                summary=row_dict.get("lessons_learned") or row_dict.get("root_cause"),
                                content="\n\n".join(content_parts) or row_dict.get("title", ""),
                                incident_type=row_dict.get("incident_type"),
                                severity=None,
                                status=row_dict.get("status"),
                                metadata={
                                    "discipline": row_dict.get("discipline"),
                                    "delay_days": row_dict.get("delay_days"),
                                    "cost_impact": row_dict.get("cost_impact"),
                                },
                                source_type="INSTITUTIONAL_INCIDENT",
                                source_reference=str(row_dict["incident_id"]),
                                created_at=row_dict.get("recorded_at"),
                            )
                        )
        except Exception as e:
            logger.warning("Error fetching institutional memory candidates from database: %s", e)
            return []

        return records


# ============================================================================
# 4. COMPOSITE MEMORY PROVIDER
# ============================================================================

class CompositeMemoryProvider(MemoryProvider):
    """
    Combines multiple providers (e.g. Database + InMemory), deduplicating candidates by memory_id.
    """

    def __init__(self, providers: Optional[List[MemoryProvider]] = None):
        self.providers: List[MemoryProvider] = providers or []

    def add_provider(self, provider: MemoryProvider) -> None:
        self.providers.append(provider)

    def get_candidates(
        self,
        context: ProjectContext,
        filters: Optional[MemoryFilter] = None,
    ) -> List[MemoryRecord]:
        seen_ids = set()
        deduped: List[MemoryRecord] = []
        for p in self.providers:
            try:
                for rec in p.get_candidates(context, filters):
                    if rec.memory_id not in seen_ids:
                        seen_ids.add(rec.memory_id)
                        deduped.append(rec)
            except Exception as e:
                logger.warning("Error getting candidates from provider %s: %s", p, e)
        return deduped
