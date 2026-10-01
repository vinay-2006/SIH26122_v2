import csv
import io
import logging
import os
from abc import ABC, abstractmethod
from datetime import date
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/export", tags=["export"])

CSV_HEADER = [
    "activity_id",
    "actual_start",
    "actual_finish",
    "actual_pct_complete",
    "actual_quantity",
]


class PMISAdapter(ABC):
    """
    Canonical PMIS adapter interface per PRD v5 Appendix B.
    """

    @abstractmethod
    def push_actual(
        self,
        activity_id: str,
        actual_start: Optional[Union[date, str]] = None,
        actual_finish: Optional[Union[date, str]] = None,
        actual_pct_complete: Optional[float] = None,
        actual_quantity: Optional[float] = None,
    ) -> bool:
        """
        Canonical 5-field write interface for PMIS integration.
        """
        pass


def format_csv_rows(rows: List[dict]) -> str:
    """
    Format approved actuals records into RFC 4180 compliant CSV text.
    Visible columns are strictly:
        activity_id, actual_start, actual_finish, actual_pct_complete, actual_quantity
    NULL values are rendered as empty strings.
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)

    for row in rows:
        pct_val = row.get("actual_pct_complete")
        qty_val = row.get("actual_quantity")

        writer.writerow(
            [
                row.get("activity_id") if row.get("activity_id") is not None else "",
                str(row.get("actual_start")) if row.get("actual_start") is not None else "",
                str(row.get("actual_finish")) if row.get("actual_finish") is not None else "",
                f"{float(pct_val):g}" if pct_val is not None else "",
                f"{float(qty_val):g}" if qty_val is not None else "",
            ]
        )

    return output.getvalue()


class CSVExportAdapter(PMISAdapter):
    """
    Canonical CSV Export Adapter per PRD v5 Feature 26 and Appendix B.
    Receives approved actuals and outputs the canonical 5-column CSV format.
    """

    def __init__(self, output_file: Optional[str] = None):
        self.output_file = output_file
        self.records: List[dict] = []

    def push_actual(
        self,
        activity_id: str,
        actual_start: Optional[Union[date, str]] = None,
        actual_finish: Optional[Union[date, str]] = None,
        actual_pct_complete: Optional[float] = None,
        actual_quantity: Optional[float] = None,
    ) -> bool:
        """
        Accepts the canonical five fields and records them.
        """
        record = {
            "activity_id": str(activity_id),
            "actual_start": actual_start,
            "actual_finish": actual_finish,
            "actual_pct_complete": actual_pct_complete,
            "actual_quantity": actual_quantity,
        }
        self.records.append(record)

        if self.output_file:
            try:
                csv_content = self.generate_csv()
                with open(self.output_file, "w", encoding="utf-8", newline="") as f:
                    f.write(csv_content)
            except Exception as e:
                logger.warning(f"Failed to write CSV export to file '{self.output_file}': {e}")
                return False

        return True

    def generate_csv(self) -> str:
        """
        Generate CSV content from collected records.
        """
        return format_csv_rows(self.records)

    def regenerate_from_records(self, records: List[dict]) -> str:
        """
        Regenerate the complete CSV from a full dataset of approved actuals.
        Ensures CSV represents the current state rather than single event appends.
        """
        self.records = [
            {
                "activity_id": str(r.get("activity_id", "")),
                "actual_start": r.get("actual_start"),
                "actual_finish": r.get("actual_finish"),
                "actual_pct_complete": r.get("actual_pct_complete"),
                "actual_quantity": r.get("actual_quantity"),
            }
            for r in records
        ]
        csv_content = self.generate_csv()

        if self.output_file:
            try:
                with open(self.output_file, "w", encoding="utf-8", newline="") as f:
                    f.write(csv_content)
            except Exception as e:
                logger.warning(f"Failed to write CSV export to file '{self.output_file}': {e}")
                raise

        return csv_content

    def regenerate_from_db(
        self,
        conn: Optional[Any] = None,
        schedule_id: Optional[str] = None,
    ) -> str:
        """
        Query current approved_actuals from DB and regenerate the CSV export.
        """
        rows = query_approved_actuals_for_export(conn=conn, schedule_id=schedule_id)
        return self.regenerate_from_records(rows)


def query_approved_actuals_for_export(
    conn: Optional[Any] = None,
    schedule_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[dict]:
    """
    Query approved_actuals table for export with deterministic ordering.
    """
    query = """
        SELECT
            activity_id,
            actual_start,
            actual_finish,
            actual_pct_complete,
            actual_quantity
        FROM approved_actuals
    """
    params = []
    where = []
    if schedule_id:
        where.append("schedule_id = %s")
        params.append(schedule_id)
    if project_id:
        where.append("project_id = %s")
        params.append(str(project_id))
    if where:
        query += " WHERE " + " AND ".join(where)

    query += " ORDER BY activity_id ASC"

    if conn is not None:
        rows = conn.execute(query, tuple(params) if params else None).fetchall()
        return [dict(r) for r in rows]

    with get_connection() as c:
        rows = c.execute(query, tuple(params) if params else None).fetchall()
        return [dict(r) for r in rows]


_default_csv_adapter: Optional[CSVExportAdapter] = None


def get_default_csv_adapter() -> CSVExportAdapter:
    global _default_csv_adapter
    if _default_csv_adapter is None:
        export_path = os.getenv("EXPORT_CSV_PATH")
        _default_csv_adapter = CSVExportAdapter(output_file=export_path)
    return _default_csv_adapter


def set_default_csv_adapter(adapter: Optional[CSVExportAdapter]) -> None:
    global _default_csv_adapter
    _default_csv_adapter = adapter


def trigger_auto_export(
    conn: Optional[Any] = None,
    schedule_id: Optional[str] = None,
    adapter: Optional[CSVExportAdapter] = None,
) -> str:
    """
    Auto-triggered CSV export executed post-commit per Feature #26.
    Uses the complete current approved_actuals dataset.
    """
    if adapter is None:
        base = get_default_csv_adapter()
        if base.output_file and schedule_id:
            # one file per schedule: a single shared file would let the last-approving project overwrite
            # (and expose) another project's export
            root, ext = os.path.splitext(base.output_file)
            active_adapter = CSVExportAdapter(output_file=f"{root}.{schedule_id}{ext or '.csv'}")
        else:
            active_adapter = base
    else:
        active_adapter = adapter
    return active_adapter.regenerate_from_db(conn=conn, schedule_id=schedule_id)


@router.get("/health")
def health():
    return {"router": "export", "status": "ok"}


@router.get("/csv")
def export_csv(
    schedule_id: Optional[str] = Query(
        default=None,
        description="Required (or X-Schedule-ID); validated against the project",
    ),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Export the approved actuals of an EXPLICIT schedule of the caller's project to the canonical 5-column CSV.
    Requires REVIEW_CLAIM (supervisor / planner / project manager / owner).
    Columns: activity_id, actual_start, actual_finish, actual_pct_complete, actual_quantity
    """
    try:
        rows = query_approved_actuals_for_export(
            schedule_id=schedule_context.schedule_id, project_id=str(schedule_context.project_id)
        )
    except Exception as e:
        logger.error(f"Error querying approved_actuals for CSV export: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate CSV export",
        )

    csv_data = format_csv_rows(rows)

    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="approved_actuals.csv"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )
