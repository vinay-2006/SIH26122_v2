"""Format-agnostic parse result. Every parser (CSV, XER, MSP XML) produces a ParsedSchedule; everything downstream
(validation, mapping, reconciliation, build) works only on this."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


class ParseError(Exception):
    """The file cannot be read as the claimed format at all (nothing usable). Carries a PM-readable message."""

    def __init__(self, message: str, code: str = "UNREADABLE_FILE"):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Issue:
    code: str
    message: str
    ref: Optional[str] = None            # activity id / wbs code / row the issue is about
    severity: str = "ERROR"              # ERROR | WARNING | INFO


@dataclass
class PProject:
    name: Optional[str] = None
    code: Optional[str] = None
    data_date: Optional[date] = None
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    source_format: str = ""
    hours_per_day: float = 8.0


@dataclass
class PWbs:
    code: str
    name: str
    parent_code: Optional[str] = None    # None => root
    sequence: int = 1


@dataclass
class PActivity:
    external_id: str
    name: str
    wbs_code: Optional[str]
    start: Optional[date]
    finish: Optional[date]
    duration_days: Optional[float] = None
    total_float_days: Optional[float] = None
    activity_type: str = "TASK"          # TASK | MILESTONE | LOE
    discipline_label: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    sequence: int = 1


@dataclass
class PResource:
    code: str
    name: str
    resource_class: Optional[str] = None  # MATERIAL | LABOR | EQUIPMENT | OTHER (None => inferred)
    uom_label: Optional[str] = None


@dataclass
class PAssignment:
    activity_external_id: str
    resource_code: str
    qty: Optional[float]
    uom_label: Optional[str] = None
    measures_progress: Optional[bool] = None   # None => derived from resource class
    progress_weight: float = 1.0


@dataclass
class PDependency:
    predecessor: str
    successor: str
    type: str = "FS"
    lag_days: float = 0.0


@dataclass
class ParsedSchedule:
    project: PProject = field(default_factory=PProject)
    wbs: List[PWbs] = field(default_factory=list)
    activities: List[PActivity] = field(default_factory=list)
    resources: List[PResource] = field(default_factory=list)
    assignments: List[PAssignment] = field(default_factory=list)
    dependencies: List[PDependency] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)      # parse-time findings (e.g. skipped rows)

    # -------- JSON round trip (staging in schedule_imports.parsed_payload)
    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ParsedSchedule":
        p = d["project"]
        proj = PProject(**{**p, "data_date": _d(p.get("data_date")), "planned_start": _d(p.get("planned_start")),
                           "planned_finish": _d(p.get("planned_finish"))})
        return cls(
            project=proj,
            wbs=[PWbs(**w) for w in d["wbs"]],
            activities=[PActivity(**{**a, "start": _d(a.get("start")), "finish": _d(a.get("finish"))}) for a in d["activities"]],
            resources=[PResource(**r) for r in d["resources"]],
            assignments=[PAssignment(**a) for a in d["assignments"]],
            dependencies=[PDependency(**x) for x in d["dependencies"]],
            issues=[Issue(**i) for i in d.get("issues", [])],
        )


def _d(v):
    return date.fromisoformat(v) if isinstance(v, str) and v else (v or None)


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, date):
        return o.isoformat()
    return o


# ---------------------------------------------------------------- shared helpers
WORKDAYS_PER_WEEK = 6    # Mon-Sat default project calendar


def working_days(start: date, finish: date, per_week: int = WORKDAYS_PER_WEEK) -> float:
    """Inclusive working-day count between two dates on a Mon-Sat (or Mon-Fri) calendar; milestones (same day) -> 1 only if a working day."""
    from datetime import timedelta
    if finish < start:
        return 0.0
    n, d = 0, start
    while d <= finish:
        if d.weekday() < per_week:
            n += 1
        d += timedelta(days=1)
    return float(n)


def parse_date_text(text: Optional[str], day_first: bool = True) -> Optional[date]:
    """P6 / MS Project / Excel-ish dates: ISO, ISO with time, DD-MM-YYYY, DD/MM/YYYY, DD-Mon-YY(YY), 'DD Mon YYYY'."""
    from datetime import datetime
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    t = t.split("T")[0] if "T" in t and t[:4].isdigit() else t
    t = t.split(" ")[0] if (" " in t and t[:4].isdigit() and t[4] in "-/") else t
    fmts = ["%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y", "%d-%B-%Y"]
    fmts += ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"] if day_first else ["%m-%d-%Y", "%m/%d/%Y"]
    for f in fmts:
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None
