"""Deterministic schedule generation: activity templates -> a calendar-consistent CPM schedule -> the CSV files a Project Manager would upload.

Pure functions, no database, no clock, no randomness. The same inputs always yield byte-identical files, so a seed run is reproducible.
Calendar: Monday-Saturday working weeks (the importer's default). Relationship types used: FS and SS with optional lags (in working days)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

PER_WEEK = 6

# resource = (code, name, class, qty, unit label). Material resources measured in physical units drive progress; labour / equipment never do.
Res = Tuple[str, str, str, float, str]


@dataclass
class Act:
    id: str
    name: str
    wbs: Tuple[str, ...]                      # path below the project root, e.g. ("Mainline Spread 1", "Welding")
    discipline: str                           # a label the importer's alias table understands
    dur: int                                  # working days; 0 = milestone
    preds: List[Tuple[str, str, int]] = field(default_factory=list)   # (activity id, FS|SS, lag working days)
    res: List[Res] = field(default_factory=list)
    location: str = ""
    # filled by schedule()
    start: Optional[date] = None
    finish: Optional[date] = None
    total_float: int = 0

    @property
    def milestone(self) -> bool:
        return self.dur == 0


def is_work(d: date) -> bool:
    return d.weekday() < PER_WEEK


def next_work(d: date) -> date:
    while not is_work(d):
        d += timedelta(days=1)
    return d


def add_work(d: date, n: int) -> date:
    """the working day n working days after working day d (n >= 0)"""
    d = next_work(d)
    while n > 0:
        d += timedelta(days=1)
        if is_work(d):
            n -= 1
    return d


def sub_work(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if is_work(d):
            n -= 1
    return d


def work_between(a: date, b: date) -> int:
    """working days from a to b (b >= a), exclusive of a: add_work(a, work_between(a, b)) == b for working days"""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if is_work(d):
            n += 1
    return n


def schedule(acts: List[Act], project_start: date) -> date:
    """forward pass (earliest dates) then backward pass (latest dates) -> total float; returns the project finish. `acts` must be in logic order."""
    by = {a.id: a for a in acts}
    ps = next_work(project_start)
    for a in acts:
        s = ps
        for pid, typ, lag in a.preds:
            p = by[pid]
            if typ == "FS":
                same_day = lag == 0 and (p.milestone or a.milestone)      # a milestone happens on the day its predecessor finishes
                cand = p.finish if same_day else add_work(p.finish, 1 + lag)
            else:                                                       # SS
                cand = add_work(p.start, lag)
            s = max(s, cand)
        a.start = s
        a.finish = s if a.dur <= 1 else add_work(s, a.dur - 1)
    end = max(a.finish for a in acts)
    # backward pass in working-day offsets from the project start
    off = lambda d: work_between(ps, d) if d >= ps else 0
    lf: Dict[str, int] = {}
    succ: Dict[str, List[Tuple[str, str, int]]] = {a.id: [] for a in acts}
    for a in acts:
        for pid, typ, lag in a.preds:
            succ[pid].append((a.id, typ, lag))
    last = off(end)
    ls: Dict[str, int] = {}
    for a in reversed(acts):
        dur = max(a.dur, 1)
        limit = last
        for sid, typ, lag in succ[a.id]:
            s = by[sid]
            if typ == "FS":
                same_day = lag == 0 and (a.milestone or s.milestone)
                limit = min(limit, ls[sid] if same_day else ls[sid] - 1 - lag)
            else:
                limit = min(limit, ls[sid] - lag + dur - 1)
        lf[a.id] = limit
        ls[a.id] = limit - (dur - 1)
    for a in acts:
        a.total_float = max(0, ls[a.id] - off(a.start))
    return end


def _d(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _pred(p: Tuple[str, str, int]) -> str:
    pid, typ, lag = p
    return f"{pid}{typ}" + (f"{lag:+d}" if lag else "")


def to_csv(root: str, acts: List[Act]) -> Tuple[bytes, bytes]:
    a_buf, r_buf = io.StringIO(), io.StringIO()
    aw = csv.writer(a_buf, lineterminator="\n")
    aw.writerow(["Activity ID", "Activity Name", "WBS Path", "Discipline", "Activity Type", "Baseline Duration", "Baseline Start", "Baseline Finish", "Total Float", "Predecessors", "Location"])
    rw = csv.writer(r_buf, lineterminator="\n")
    rw.writerow(["Activity ID", "Resource ID", "Resource Name", "Resource Class", "Baseline Qty", "Unit"])
    for a in acts:
        aw.writerow([a.id, a.name, " > ".join((root,) + a.wbs), a.discipline, "Milestone" if a.milestone else "Task", float(a.dur), _d(a.start), _d(a.finish), a.total_float,
                     ";".join(_pred(p) for p in a.preds), a.location])
        for code, name, cls, qty, unit in a.res:
            rw.writerow([a.id, code, name, cls, f"{qty:.3f}".rstrip("0").rstrip("."), unit])
    return a_buf.getvalue().encode("utf-8"), r_buf.getvalue().encode("utf-8")


def r3(x: float) -> float:
    return round(x, 3)
