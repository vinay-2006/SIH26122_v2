"""Grounded answers for the Time Agent and Project Intelligence.

Every statement is built from one of two kinds of source and says which:
  LIVE_DATA          computed now from the project's tables (ledger progress, review queue, issues, schedule state);
  PROJECT_KNOWLEDGE  the authored project context (domain/knowledge.py), with its provenance (FROM_RECORDS / AUTHORED / ILLUSTRATIVE / NOT_SPECIFIED).
No figure is ever generated: numbers are read from the database and copied into fixed sentences. When neither source can answer, the answer says so.
Claim wording (and claim lists) is included only when the caller is allowed to see it (`allow_claims`, a Supervisor); other roles get counts only."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..domain import knowledge as dk
from ..domain.common import actor_tx
from . import schedule_api
from .context import Ctx

PENDING = ("EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED")
INTENT_WORDS = {
    "review": ("pending", "review", "queue", "waiting", "awaiting", "approval", "approve", "claims", "claim"),
    "delay": ("delay", "delays", "delayed", "late", "behind", "overdue", "blocker", "blockers", "blocked", "hold", "holds", "issue", "issues", "slipping"),
    "unmatched": ("unmatched", "unassigned", "new scope", "binding", "bind", "no activity"),
    "progress": ("progress", "completion", "complete", "overall", "percent", "percentage", "status", "done", "stage", "stages"),
    "critical": ("critical", "float", "path", "at-risk", "at risk", "risk"),
    "today": ("today", "summary", "synthesis", "daily", "digest"),
    "lessons": ("lesson", "lessons", "learned", "learnt", "memory", "precedent", "precedents", "experience", "similar", "applicable"),
}
LINKS = {"review": ("Open Review Workspace", "/review"), "delay": ("Open Impact Preview", "/impact"), "unmatched": ("Review unmatched scope", "/review?tab=unmatched"),
         "progress": ("Open Project Dashboard", "/dashboard"), "critical": ("Open Impact Preview", "/impact"), "today": ("Open Daily Digest", "/digest"), "lessons": ("Open Root Cause & Memory", "/root-cause")}
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/\-]*")


def intents(query: str) -> List[str]:
    q = f" {(query or '').lower()} "
    found = [k for k, words in INTENT_WORDS.items() if any((f" {w} " in q or (" " in w and w in q) or re.search(rf"\b{re.escape(w)}\b", q)) for w in words)]
    return found


def _src(ref: str, label: str) -> Dict[str, Any]:
    return {"kind": "LIVE_DATA", "ref": ref, "label": label, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _days_between(a: date, b: date) -> int:
    return (a - b).days


def _iso(v: Optional[str]) -> Optional[date]:
    return date.fromisoformat(v[:10]) if v else None


def _pct_free(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ------------------------------------------------------------------------------------------------ live facts
def _acts(ctx: Ctx, c) -> List[Dict[str, Any]]:
    return schedule_api.activities(c, ctx, ctx.active_version_id) if ctx.active_version_id else []


def facts_progress(ctx: Ctx) -> Dict[str, Any]:
    v = ctx.active_version_id
    if not v:
        return {}
    with actor_tx(ctx.actor, readonly=True) as c:
        p = c.execute("select * from project_progress_as_of(%s, current_date)", (v,)).fetchone()
        st = c.execute("select wbs_name, physical_pct, planned_pct, activities from wbs_progress_as_of(%s, current_date) where node_type = 'STAGE' order by wbs_code", (v,)).fetchall()
    return {"physical_pct": float(p["physical_pct"]), "planned_pct": float(p["planned_pct"]), "activities": p["activities"], "completed": p["completed"], "in_progress": p["in_progress"],
            "not_started": p["not_started"], "weight_basis": p["weight_basis"], "as_of": str(p["as_of"]),
            "stages": [{"name": r["wbs_name"], "physical_pct": float(r["physical_pct"]), "planned_pct": float(r["planned_pct"]), "activities": r["activities"]} for r in st]}


def facts_review(ctx: Ctx, allow_claims: bool) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        counts = {r["status"]: r["n"] for r in c.execute("select status, count(*) n from execution_events where project_id = %s and status in ('REPORTED','EXTRACTED','MATCHED','VALIDATED','DISPUTED') group by status", (ctx.project_id,)).fetchall()}
        reopens = c.execute("select count(*) n from activity_reopens where project_id = %s and status = 'REQUESTED'", (ctx.project_id,)).fetchone()["n"]
        top = []
        if allow_claims:
            top = [dict(r) for r in c.execute(
                "select e.event_id, e.status, e.priority_score, left(e.raw_claim_text, 120) as text, ba.external_activity_id as activity from execution_events e "
                "left join baseline_activities ba on ba.activity_uid = e.matched_activity_uid and ba.version_id = e.filed_in_version_id where e.project_id = %s and e.status = any(%s) "
                "order by e.priority_score desc nulls last, e.created_at limit 5", (ctx.project_id, list(PENDING))).fetchall()]
    return {"pending": sum(counts.get(s, 0) for s in PENDING), "by_status": counts, "awaiting_clarification": counts.get("REPORTED", 0), "reopen_requests": reopens, "top": top}


def facts_unmatched(ctx: Ctx, allow_claims: bool) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        n = c.execute("select count(*) n from execution_events where project_id = %s and status = any(%s) and matched_activity_uid is null", (ctx.project_id, list(PENDING))).fetchone()["n"]
        rows = []
        if allow_claims and n:
            rows = [dict(r) for r in c.execute("select event_id, status, left(raw_claim_text, 120) as text from execution_events where project_id = %s and status = any(%s) and matched_activity_uid is null "
                                               "order by created_at limit 5", (ctx.project_id, list(PENDING))).fetchall()]
    return {"unmatched": n, "top": rows}


def facts_delays(ctx: Ctx) -> Dict[str, Any]:
    today = date.today()
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = _acts(ctx, c)
        issues = [dict(r) for r in c.execute("select title, severity, blocks_work from issues where project_id = %s and status = 'ACTIVE' order by array_position(array['CRITICAL','HIGH','MEDIUM','LOW'], severity), created_at limit 5", (ctx.project_id,)).fetchall()]
        n_issues = c.execute("select count(*) n from issues where project_id = %s and status = 'ACTIVE'", (ctx.project_id,)).fetchone()["n"]
    late = []
    for a in acts:
        pf = _iso(a["planned_finish"])
        if a["execution_state"] != "COMPLETED" and pf and pf < today:
            late.append({"activity_id": a["activity_id"], "name": a["activity_name"], "days_late": _days_between(today, pf), "pct": a["_pct"], "state": a["execution_state"]})
    late.sort(key=lambda x: -x["days_late"])
    blocked = [{"activity_id": a["activity_id"], "name": a["activity_name"], "kind": "quality hold" if a["_qhold"] else "blocked by an active issue"} for a in acts if a["_blocked"] or a["_qhold"]]
    return {"overdue": len(late), "overdue_top": late[:6], "blocked": blocked[:6], "blocked_count": len(blocked), "active_issues": n_issues, "issues_top": issues}


def facts_critical(ctx: Ctx) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = _acts(ctx, c)
    crit = [a for a in acts if a["is_critical"] and a["execution_state"] != "COMPLETED"]
    crit.sort(key=lambda a: (a["planned_finish"] or "9999", a["activity_id"]))
    return {"critical_open": len(crit), "top": [{"activity_id": a["activity_id"], "name": a["activity_name"], "planned_finish": a["planned_finish"], "float": a["total_float"], "pct": a["_pct"],
                                                 "state": a["execution_state"]} for a in crit[:6]]}


def facts_today(ctx: Ctx) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        filed = c.execute("select count(*) n from execution_events where project_id = %s and created_at::date = current_date", (ctx.project_id,)).fetchone()["n"]
        dec = {r["action"]: r["n"] for r in c.execute("select action, count(*) n from planner_decisions where project_id = %s and decided_at::date = current_date group by action", (ctx.project_id,)).fetchall()}
        issues = c.execute("select count(*) n from issues where project_id = %s and created_at::date = current_date", (ctx.project_id,)).fetchone()["n"]
    return {"claims_filed": filed, "decisions": dec, "decisions_total": sum(dec.values()), "issues_reported": issues}


def facts_activity(ctx: Ctx, ids: List[str]) -> List[Dict[str, Any]]:
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = {a["activity_id"]: a for a in _acts(ctx, c)}
    out = []
    for i in ids:
        a = acts.get(i)
        if a:
            out.append({"activity_id": i, "name": a["activity_name"], "state": a["execution_state"], "pct": a["_pct"], "planned_start": a["planned_start"], "planned_finish": a["planned_finish"],
                        "critical": a["is_critical"], "float": a["total_float"], "blocked": bool(a["_blocked"] or a["_qhold"])})
    return out


def _activity_ids(ctx: Ctx, query: str, extra: Optional[str]) -> List[str]:
    """the activity ids of THIS project that the question names (matched against the schedule, so only real ids count)"""
    toks = {t.upper() for t in TOKEN.findall(query or "") if any(ch.isdigit() for ch in t)}
    if not toks and not extra:
        return []
    with actor_tx(ctx.actor, readonly=True) as c:
        known = {a["activity_id"].upper(): a["activity_id"] for a in _acts(ctx, c)}
    found = [known[t] for t in sorted(toks) if t in known]
    if extra and extra.upper() in known and known[extra.upper()] not in found:
        found.append(known[extra.upper()])
    return found


# ------------------------------------------------------------------------------------------------ composing
def _pct(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") + "%"


def compose(ctx: Ctx, query: str, *, allow_claims: bool, activity_id: Optional[str] = None) -> Dict[str, Any]:
    """{answer, sources, intents, links, answered}. Deterministic: the same data and question give the same answer."""
    kws = intents(query)
    ids = _activity_ids(ctx, query, activity_id)
    parts: List[str] = []
    sources: List[Dict[str, Any]] = []
    links: List[Dict[str, str]] = []

    def add_link(k: str) -> None:
        if k in LINKS and allow_claims and LINKS[k][1] not in [l["to"] for l in links]:
            links.append({"label": LINKS[k][0], "to": LINKS[k][1]})

    if "progress" in kws:
        f = facts_progress(ctx)
        if f:
            s = ", ".join(f"{st['name']} {_pct(st['physical_pct'])} (plan {_pct(st['planned_pct'])})" for st in f["stages"])
            parts.append(f"Progress (approved quantities, as of {f['as_of']}): overall {_pct(f['physical_pct'])} against {_pct(f['planned_pct'])} planned. "
                         f"{_plural(f['activities'], 'activity', 'activities')}: {f['completed']} completed, {f['in_progress']} in progress, {f['not_started']} not started. "
                         f"By stage: {s}. Weighting basis: {f['weight_basis'].lower()}.")
            sources.append(_src("progress", "Project progress from the approved ledger")); add_link("progress")
        else:
            parts.append("Progress cannot be shown: the project has no active schedule.")
    if "review" in kws:
        f = facts_review(ctx, allow_claims)
        line = f"Review queue: {_plural(f['pending'], 'claim', 'claims')} waiting for a Supervisor decision"
        if f["by_status"]:
            line += " (" + ", ".join(f"{n} {s.lower()}" for s, n in sorted(f["by_status"].items()) if s != "REPORTED") + ")"
        line += f"; {_plural(f['awaiting_clarification'], 'claim is', 'claims are')} waiting for the engineer to answer a question; {_plural(f['reopen_requests'], 'reopen request', 'reopen requests')} pending."
        if f["top"]:
            line += " Highest priority:\n" + "\n".join(f"  - {t['event_id']} [{t['status']}] {('on ' + t['activity']) if t['activity'] else 'no activity yet'}: \"{t['text']}\"" for t in f["top"])
        elif not allow_claims and f["pending"]:
            line += " Claim wording is visible to Supervisors only."
        parts.append(line); sources.append(_src("review_queue", "Review queue")); add_link("review")
    if "unmatched" in kws:
        f = facts_unmatched(ctx, allow_claims)
        line = f"Unmatched scope: {_plural(f['unmatched'], 'pending claim has', 'pending claims have')} no schedule activity yet."
        if f["top"]:
            line += "\n" + "\n".join(f"  - {t['event_id']} [{t['status']}]: \"{t['text']}\"" for t in f["top"])
        parts.append(line); sources.append(_src("unmatched_claims", "Claims without a matched activity")); add_link("unmatched")
    if "delay" in kws:
        f = facts_delays(ctx)
        line = f"Delays: {_plural(f['overdue'], 'activity is', 'activities are')} past the planned finish date and not completed"
        if f["overdue_top"]:
            line += ":\n" + "\n".join(f"  - {a['activity_id']} {a['name']}: {a['days_late']} days past plan, {_pct(a['pct'])} complete" for a in f["overdue_top"])
        else:
            line += "."
        line += f"\nBlocked or on hold: {f['blocked_count']}" + ("".join(f"\n  - {b['activity_id']} {b['name']} ({b['kind']})" for b in f["blocked"]) if f["blocked"] else ".")
        line += f"\nActive issues: {f['active_issues']}" + ("".join(f"\n  - [{i['severity']}] {i['title']}" for i in f["issues_top"]) if f["issues_top"] else ".")
        parts.append(line); sources.append(_src("schedule_state", "Schedule state against today's date")); sources.append(_src("issues", "Active issues")); add_link("delay")
    if "critical" in kws:
        f = facts_critical(ctx)
        line = f"Critical path: {_plural(f['critical_open'], 'critical activity is', 'critical activities are')} not yet completed"
        line += (":\n" + "\n".join(f"  - {a['activity_id']} {a['name']}: planned finish {a['planned_finish']}, float {a['float'] if a['float'] is not None else 'not provided'}, {_pct(a['pct'])} complete" for a in f["top"])) if f["top"] else "."
        parts.append(line); sources.append(_src("critical_path", "Critical path from the schedule")); add_link("critical")
    if "today" in kws:
        f = facts_today(ctx)
        dec = ", ".join(f"{n} {a.lower()}" for a, n in sorted(f["decisions"].items())) or "none"
        parts.append(f"Today: {_plural(f['claims_filed'], 'claim', 'claims')} filed, {_plural(f['decisions_total'], 'decision', 'decisions')} recorded ({dec}), {_plural(f['issues_reported'], 'issue', 'issues')} reported.")
        sources.append(_src("today", "Today's activity")); add_link("today")
    if "lessons" in kws:
        from ..errors import ApiError
        from .memory_intel_api import compute_radar
        try:
            rad = compute_radar(ctx, 90)
        except ApiError:
            rad = None
        if rad is None:
            parts.append("Lessons cannot be matched: the project has no active schedule.")
        elif rad["project_status"] == "COMPLETED":
            parts.append("This project is complete, so no work is upcoming. Its lessons stay in the organisation's memory for other projects.")
        elif not rad["items"]:
            parts.append(f"Lessons: no recorded lesson matches the work under way or starting in the next {rad['horizon_days']} days ({rad['lessons_considered']} lessons considered).")
        else:
            lines = []
            for it in rad["items"][:4]:
                l = it["lessons"][0]
                when = "under way" if it["timing"] == "IN_PROGRESS" else f"starts in {it['starts_in_days']} days"
                lines.append(f"  - {it['activity_id']} {it['activity_name']} ({when}): \"{l['title']}\" ({l['project_name']}{', shared lesson' if l['from_other_project'] else ''})"
                             + (f", {_pct_free(l['delay_days'])} days of delay then" if l.get("delay_days") is not None else "")
                             + (f"; what worked: {l['corrective_action']}" if l.get("corrective_action") else ""))
            parts.append(f"Lessons that apply to work under way or starting in the next {rad['horizon_days']} days ({rad['activities_with_lessons']} activities, {rad['lessons_considered']} lessons considered, matched by {rad['retrieval_mode']}):\n" + "\n".join(lines))
        sources.append(_src("memory_radar", "Lessons radar over institutional memory")); add_link("lessons")
    if ids:
        for a in facts_activity(ctx, ids):
            parts.append(f"Activity {a['activity_id']} - {a['name']}: {a['state'].replace('_', ' ').lower()}, {_pct(a['pct'])} complete, planned {a['planned_start']} to {a['planned_finish']}, "
                         f"{'on the critical path' if a['critical'] else 'not critical'}, float {a['float'] if a['float'] is not None else 'not provided'}{', blocked or on hold' if a['blocked'] else ''}.")
            sources.append(_src(f"activity:{a['activity_id']}", f"Activity {a['activity_id']}"))

    # authored project context (always searched; shown when it matches the question)
    entries = dk.list_entries(ctx.actor)
    hits = dk.rank(query, entries, k=2, min_score=3.0 if kws or ids else 1.0)
    ctx_lines = []
    for h in hits:
        excerpt = " ".join(h["body"].split())
        excerpt = excerpt if len(excerpt) <= 420 else excerpt[:417].rsplit(" ", 1)[0] + "..."
        tag = {"FROM_RECORDS": "from the project records", "AUTHORED": "authored", "ILLUSTRATIVE": "illustrative, not a contractual fact", "NOT_SPECIFIED": "not specified"}[h["provenance"]]
        ctx_lines.append(f"{dk.SECTION_LABEL[h['section']]} - {h['title']} ({tag}): {excerpt}")
        sources.append(dk.cite(h))
    if ctx_lines:
        parts.append("Project context:\n" + "\n".join(f"  - {l}" for l in ctx_lines))

    answered = bool(parts)
    if not answered:
        topics = "pending reviews, delays and blockers, unmatched scope, overall and stage progress, critical activities, today's summary, a named activity, or the project's scope, contract dates, site, stakeholders, milestones, constraints, risks, safety and quality, procurement, reporting rules and glossary"
        parts.append("I cannot answer that from this project's records: no live data and no project-knowledge entry matches the question. "
                     f"I can answer about {topics}.")
    if kws or ids:
        parts.append("Figures above are live and computed now; project context is authored text and is labelled with where it comes from.")
    return {"answer": "\n\n".join(parts), "sources": sources, "intents": kws, "links": links, "answered": answered}
