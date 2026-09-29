"""
Primavera P6 .xer parser.

An XER file is NOT a CSV. It's a sequence of stacked tables in one flat
text file:

    ERMHDR\t<version>\t<date>\t...                  -- one header line
    %T\t<TABLE_NAME>                                 -- start of a table
    %F\t<field1>\t<field2>\t...                      -- that table's columns
    %R\t<val1>\t<val2>\t...                          -- one row (repeats)
    %T\t<NEXT_TABLE_NAME>                             -- next table starts
    ...

Every line is tab-delimited; field order is declared per-table by its own
%F line (columns are NOT fixed/positional across exports -- different P6
versions/configs emit different column sets for the same table), so a
correct parser has to key off %F, not assume a schema. Feeding this into
pandas.read_csv would treat the whole file as one flat table and produce
garbage (mismatched column counts per line, table boundaries treated as
data rows).

This module only extracts what M2's claim intake needs: activities (TASK)
with an optional discipline hint from the WBS hierarchy (PROJWBS). It does
not attempt to model the full P6 schema (relationships, resources, calendars,
etc.) -- those aren't used by claim extraction and adding them would be
speculative/unused code.
"""
from dataclasses import dataclass, field
from datetime import date as date_type, datetime
from typing import Optional

from backend.shared.discipline_normalize import normalize_discipline
from backend.shared.schemas import ClaimMode, Discipline, EventType, ExtractedClaimFields

# Real P6 exports vary in encoding -- most modern exports are UTF-8, but
# XER is historically a Windows/ANSI format and older P6 versions (or exports
# from non-English locales) commonly use Windows-1252. Try UTF-8 first
# (strict, so we notice if it's wrong) and fall back to cp1252, which can
# decode any byte value and is the standard mojibake-avoidance fallback for
# ANSI-era Windows exports.
_ENCODINGS = ("utf-8", "cp1252")

# P6's status_code values that represent "some real progress has happened
# and is worth reporting as a claim". TK_NotStart is deliberately excluded --
# an activity that hasn't started yet has no progress to claim, mirroring
# the same has_progress_data gate schedule-export uses for CSV/XLSX.
_PROGRESS_STATUS_CODES = {"TK_Complete", "TK_Active"}


class XERParseError(Exception):
    """The file is not a well-formed XER export (missing header/tables)."""


@dataclass
class XERTable:
    fields: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class XERActivity:
    activity_id: str
    activity_name: str
    status_code: Optional[str]
    discipline: Optional[str]
    target_start: Optional[str]
    target_end: Optional[str]
    act_start: Optional[str]
    act_end: Optional[str]
    phys_complete_pct: Optional[float]


def _decode(contents: bytes) -> str:
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in _ENCODINGS:
        try:
            return contents.decode(encoding)
        except UnicodeDecodeError as e:
            last_error = e
    # Should be unreachable -- cp1252 accepts every byte value -- but keep a
    # clear error rather than a bare re-raise if a future encoding list ever
    # drops that guarantee.
    raise XERParseError(f"Could not decode XER file as any of {_ENCODINGS}: {last_error}")


def parse_xer_tables(contents: bytes) -> dict[str, XERTable]:
    """
    Parse the raw %T/%F/%R structure into {table_name: XERTable}. Raises
    XERParseError if the file doesn't look like a real XER export.
    """
    text = _decode(contents)
    lines = text.split("\n")

    if not lines or not lines[0].startswith("ERMHDR"):
        raise XERParseError(
            "File does not start with an ERMHDR line — not a valid P6 .xer export."
        )

    tables: dict[str, XERTable] = {}
    current_table: Optional[str] = None
    current_fields: list[str] = []

    for line in lines:
        line = line.rstrip("\r\n")
        if not line:
            continue
        parts = line.split("\t")
        tag = parts[0]

        if tag == "%T":
            if len(parts) < 2:
                raise XERParseError(f"Malformed %T line (no table name): {line!r}")
            current_table = parts[1]
            current_fields = []
            tables.setdefault(current_table, XERTable(fields=[]))
        elif tag == "%F":
            if current_table is None:
                raise XERParseError("Found %F line before any %T table header.")
            current_fields = parts[1:]
            tables[current_table].fields = current_fields
        elif tag == "%R":
            if current_table is None or not current_fields:
                raise XERParseError(
                    f"Found %R row before its table's %F field list: {line!r}"
                )
            values = parts[1:]
            row = dict(zip(current_fields, values))
            tables[current_table].rows.append(row)
        elif tag in ("%E", "ERMHDR"):
            continue
        # Unrecognized tags are ignored rather than raising -- future P6
        # versions may add new control lines, and failing the whole parse
        # over an unknown tag would be more fragile than useful.

    if "TASK" not in tables:
        raise XERParseError(
            "XER file has no TASK table — nothing to extract activities from."
        )

    return tables


def _build_wbs_discipline_map(projwbs: Optional[XERTable]) -> dict[str, Optional[str]]:
    """wbs_id -> discipline hint, resolved by walking each WBS node's name
    (and its parent chain, since discipline is usually set at a mid-level
    node like "Civil Works" with activities attached several levels below
    it) against _WBS_DISCIPLINE_HINTS."""
    if projwbs is None:
        return {}

    by_id = {row.get("wbs_id"): row for row in projwbs.rows}

    # Recognized Discipline enum values a WBS node name might normalize to
    # cleanly (e.g. "Civil Works" -> "CIVIL"). A node name normalizing to
    # something else (e.g. "North Field Utility Corridor" -> "NORTH_FIELD_
    # UTILITY_CORRIDOR") isn't a discipline -- keep walking up to its parent
    # instead of accepting the first normalize_discipline() result blindly.
    _KNOWN_DISCIPLINES = {
        "CIVIL", "PIPING", "STATIC_ROTATING_EQUIPMENT", "ELECTRICAL",
        "INSTRUMENTATION", "HSE",
    }

    def resolve(wbs_id: Optional[str], depth: int = 0) -> Optional[str]:
        if not wbs_id or wbs_id not in by_id or depth > 20:
            return None
        row = by_id[wbs_id]
        name = (row.get("wbs_name") or "").strip()
        candidate = normalize_discipline(name)
        if candidate in _KNOWN_DISCIPLINES:
            return candidate
        return resolve(row.get("parent_wbs_id"), depth + 1)

    return {wbs_id: resolve(wbs_id) for wbs_id in by_id}


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def extract_activities(contents: bytes) -> list[XERActivity]:
    """
    Parse an XER file's TASK table into normalized activities, with a
    discipline hint resolved from the WBS hierarchy (PROJWBS) where
    possible. Raises XERParseError for a malformed/non-XER file.
    """
    tables = parse_xer_tables(contents)
    task_table = tables["TASK"]
    wbs_discipline = _build_wbs_discipline_map(tables.get("PROJWBS"))

    activities: list[XERActivity] = []
    for row in task_table.rows:
        activity_id = (row.get("task_code") or "").strip()
        if not activity_id:
            # A TASK row with no task_code isn't a real schedulable
            # activity (e.g. a WBS summary bar in some exports) -- skip
            # rather than fabricate an id.
            continue

        activities.append(
            XERActivity(
                activity_id=activity_id,
                activity_name=(row.get("task_name") or "").strip(),
                status_code=row.get("status_code") or None,
                discipline=wbs_discipline.get(row.get("wbs_id")),
                target_start=row.get("target_start_date") or None,
                target_end=row.get("target_end_date") or None,
                act_start=row.get("act_start_date") or None,
                act_end=row.get("act_end_date") or None,
                phys_complete_pct=_to_float(row.get("phys_complete_pct")),
            )
        )

    return activities


def activities_with_progress(activities: list[XERActivity]) -> list[XERActivity]:
    """Filter to activities that actually have reportable progress -- an
    activity that hasn't started (TK_NotStart) has nothing to claim."""
    return [a for a in activities if a.status_code in _PROGRESS_STATUS_CODES]


def _parse_xer_date(value: Optional[str]) -> Optional[date_type]:
    if not value:
        return None
    # P6 XER dates are consistently "YYYY-MM-DD HH:MM"; be tolerant of a
    # bare date too in case a trimmed/hand-edited export omits the time.
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def build_claim_from_activity(
    activity: XERActivity,
) -> tuple[str, ExtractedClaimFields]:
    """
    Turn one XER activity (already filtered to activities_with_progress) into
    (raw_claim_text, ExtractedClaimFields), mirroring
    tabular_extraction.build_claim_from_row's structured, no-LLM approach --
    status_code and phys_complete_pct are the XER's own ground truth for
    progress, not something an LLM needs to infer from free text.
    """
    discipline = (
        activity.discipline
        if activity.discipline in Discipline._value2member_map_
        else None
    )

    if activity.phys_complete_pct is not None:
        claimed_pct = activity.phys_complete_pct
    elif activity.status_code == "TK_Complete":
        claimed_pct = 100.0
    else:
        claimed_pct = None

    if activity.status_code == "TK_Complete":
        event_type = EventType.ACTUAL_FINISH
        event_date = _parse_xer_date(activity.act_end) or _parse_xer_date(activity.target_end)
    else:
        event_type = EventType.PROGRESS_UPDATE
        event_date = _parse_xer_date(activity.act_start) or _parse_xer_date(activity.target_start)

    status_label = {
        "TK_Complete": "Complete",
        "TK_Active": "In progress",
    }.get(activity.status_code, activity.status_code or "")

    extracted = ExtractedClaimFields(
        event_date=event_date,
        reported_activity_id=activity.activity_id,
        discipline=discipline,
        action=activity.activity_name or None,
        event_type=event_type,
        claim_mode=ClaimMode.CUMULATIVE_PCT,
        claimed_pct=claimed_pct,
        language_detected="English",
    )

    raw_text = (
        f"P6 schedule export status for {activity.activity_id}: "
        f"{activity.activity_name} — {status_label}"
    ).strip(" —")
    return raw_text, extracted


def parse_schedule_xer(contents: bytes, schedule_id: str):
    """
    Parses a Primavera P6 .xer file into a ScheduleParseResult (ScheduleActivity + ScheduleDependency).
    Extracts activities (%T TASK), dependencies (%T TASKPRED), WBS (%T PROJWBS), float, and dates.
    """
    import uuid
    from backend.shared.schedule import ScheduleParseResult, ScheduleValidationError
    from backend.shared.schemas import ScheduleActivity, ScheduleDependency

    if not schedule_id or not schedule_id.strip():
        raise ValueError("schedule_id is required to parse a schedule")

    try:
        tables = parse_xer_tables(contents)
    except XERParseError as e:
        return ScheduleParseResult(
            schedule_id=schedule_id,
            errors=[ScheduleValidationError(row_number=1, field_name="xer_header", message=str(e))],
        )

    task_table = tables.get("TASK")
    if not task_table or not task_table.rows:
        return ScheduleParseResult(
            schedule_id=schedule_id,
            errors=[ScheduleValidationError(row_number=1, field_name="TASK", message="XER file has no TASK table or rows")],
        )

    projwbs = tables.get("PROJWBS")
    wbs_map = {}
    wbs_discipline_map = _build_wbs_discipline_map(projwbs)
    if projwbs:
        for row in projwbs.rows:
            wid = row.get("wbs_id")
            wcode = row.get("wbs_short_name") or row.get("wbs_code") or row.get("wbs_name")
            if wid:
                wbs_map[wid] = wcode

    activities: list[ScheduleActivity] = []
    errors: list[ScheduleValidationError] = []
    task_id_to_activity_id: dict[str, str] = {}
    seen_activity_ids: set[str] = set()

    for idx, row in enumerate(task_table.rows, start=2):
        activity_id = (row.get("task_code") or "").strip()
        internal_task_id = (row.get("task_id") or "").strip()

        if not activity_id:
            continue

        if activity_id in seen_activity_ids:
            errors.append(
                ScheduleValidationError(
                    row_number=idx,
                    field_name="activity_id",
                    message=f"Duplicate activity_id {activity_id!r} in XER",
                    activity_id=activity_id,
                )
            )
            continue
        seen_activity_ids.add(activity_id)
        if internal_task_id:
            task_id_to_activity_id[internal_task_id] = activity_id

        activity_name = (row.get("task_name") or "").strip() or f"Activity {activity_id}"
        wbs_id = row.get("wbs_id")
        wbs_code = wbs_map.get(wbs_id) or "WBS-MAIN"
        discipline = wbs_discipline_map.get(wbs_id) or "CIVIL"

        p_start = _parse_xer_date(row.get("target_start_date") or row.get("early_start_date") or row.get("act_start_date"))
        p_finish = _parse_xer_date(row.get("target_end_date") or row.get("early_end_date") or row.get("act_end_date"))

        if not p_start:
            p_start = date_type(2026, 1, 1)
        if not p_finish:
            p_finish = p_start

        planned_qty = _to_float(row.get("target_qty_cnt"))
        total_float_hrs = _to_float(row.get("total_float_hr_cnt"))
        total_float = round(total_float_hrs / 8.0, 2) if total_float_hrs is not None else None
        is_critical = (total_float <= 0.0) if total_float is not None else None

        try:
            act = ScheduleActivity(
                schedule_id=schedule_id,
                activity_id=activity_id,
                activity_name=activity_name,
                wbs_code=wbs_code,
                discipline=discipline,
                location="Site",
                asset_tag=None,
                planned_start=p_start,
                planned_finish=p_finish,
                planned_quantity=planned_qty,
                uom=None,
                baseline_pct_complete=0.0,
                total_float=total_float,
                is_critical=is_critical,
            )
            activities.append(act)
        except Exception as exc:
            errors.append(
                ScheduleValidationError(
                    row_number=idx,
                    field_name="activity",
                    message=str(exc),
                    activity_id=activity_id,
                )
            )

    dependencies: list[ScheduleDependency] = []
    taskpred = tables.get("TASKPRED")
    if taskpred:
        rel_map = {
            "PR_FS": "FS",
            "PR_SS": "SS",
            "PR_FF": "FF",
            "PR_SF": "SF",
        }
        for idx, row in enumerate(taskpred.rows, start=2):
            pred_tid = row.get("pred_task_id")
            succ_tid = row.get("task_id")
            pred_aid = task_id_to_activity_id.get(pred_tid)
            succ_aid = task_id_to_activity_id.get(succ_tid)

            if not pred_aid or not succ_aid:
                continue
            if pred_aid == succ_aid:
                errors.append(
                    ScheduleValidationError(
                        row_number=idx,
                        field_name="predecessor_activity_id",
                        message="Activity cannot depend on itself in XER TASKPRED",
                        activity_id=succ_aid,
                    )
                )
                continue

            raw_rel = row.get("pred_type", "PR_FS")
            rel_type = rel_map.get(raw_rel, "FS")
            lag_hrs = _to_float(row.get("lag_hr_cnt")) or 0.0
            lag_days = round(lag_hrs / 8.0, 2)

            dep = ScheduleDependency(
                dependency_id=str(uuid.uuid4()),
                schedule_id=schedule_id,
                predecessor_activity_id=pred_aid,
                successor_activity_id=succ_aid,
                relationship_type=rel_type,
                lag_days=lag_days,
            )
            dependencies.append(dep)

    return ScheduleParseResult(
        schedule_id=schedule_id,
        activities=activities,
        dependencies=dependencies,
        errors=errors,
    )

