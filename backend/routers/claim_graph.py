"""
PRD v6 Features 31 & 34 -- claim-centred execution knowledge graph and Ask Why.

No materialised graph table: everything is joined at query time from
execution_events, source_documents, candidate_matches, claim_activity_splits,
conflict_records, validation_issues, evidence_links, planner_decisions and
schedule_dependencies.

    GET /api/v1/claims/{event_id}/knowledge-graph
        {event_id, nodes:[{id,label,type,properties}], edges:[{source,target,relationship,confidence}]}

    GET /api/v1/graph/explain/{activity_id}?event_id=&depth=N&schedule_id=
        Deterministic "Ask Why" explanation. depth 1 = the claim, its flags/conflicts/evidence
        and direct dependency context; depth N follows the predecessor chain N hops upstream
        (the UI re-roots by calling again with the selected activity and depth+1). Every
        statement is generated from stored records -- nothing is inferred or invented.

Supervisor only (review-side investigation tools).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.context import gates
from backend.context.event import EventContext, load_event_in_project
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["knowledge-graph"])


def _ser(row: Any) -> Dict[str, Any]:
    out = {}
    for k, v in dict(row or {}).items():
        out[k] = str(v) if isinstance(v, (datetime, date, uuid.UUID)) else v
    return out


def _q(conn, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    return [_ser(r) for r in conn.execute(sql, params).fetchall()]


def _load_event(conn, event_id: str) -> Dict[str, Any]:
    rows = _q(conn, "SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Claim '{event_id}' not found")
    return rows[0]


def _short(text: Optional[str], n: int = 48) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def build_claim_graph(conn, event_id: str) -> Dict[str, Any]:
    ev = _load_event(conn, event_id)
    sid = ev["schedule_id"]
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def node(nid: str, label: str, ntype: str, **props):
        nodes.setdefault(nid, {"id": nid, "label": label, "type": ntype, "properties": props})

    def edge(src: str, dst: str, rel: str, conf: Optional[float] = None):
        edges.append({"source": src, "target": dst, "relationship": rel, "confidence": conf})

    claim_id = f"claim:{event_id}"
    node(claim_id, f"Claim {event_id[:8]}: {_short(ev.get('raw_claim_text'))}", "CLAIM",
         event_id=event_id, status=ev.get("status"), event_date=ev.get("event_date"),
         claim_mode=ev.get("claim_mode"), claimed_pct=ev.get("claimed_pct"),
         claimed_quantity=ev.get("claimed_quantity"), priority_score=ev.get("priority_score"))

    def activity_node(activity_id: str) -> str:
        nid = f"activity:{activity_id}"
        if nid not in nodes:
            a = _q(conn, "SELECT * FROM schedule_activities WHERE schedule_id=%s AND activity_id=%s", (sid, activity_id))
            a = a[0] if a else {}
            node(nid, f"{activity_id} {_short(a.get('activity_name'), 40)}".strip(), "ACTIVITY",
                 activity_id=activity_id, wbs_code=a.get("wbs_code"), total_float=a.get("total_float"),
                 is_critical=a.get("is_critical"), planned_start=a.get("planned_start"),
                 planned_finish=a.get("planned_finish"))
            if a.get("wbs_code"):
                wid = f"wbs:{a['wbs_code']}"
                node(wid, f"WBS {a['wbs_code']}", "WBS", wbs_code=a["wbs_code"])
                edge(nid, wid, "PART_OF_WBS")
        return nid

    # source document / channel
    if ev.get("document_id"):
        d = _q(conn, "SELECT * FROM source_documents WHERE document_id=%s", (ev["document_id"],))
        d = d[0] if d else {}
        did = f"document:{ev['document_id']}"
        node(did, d.get("file_name") or "Source document", "DOCUMENT",
             document_type=d.get("document_type"), file_hash=d.get("file_hash"))
        edge(claim_id, did, "EVIDENCED_BY")
    if ev.get("location"):
        lid = f"location:{ev['location']}"
        node(lid, ev["location"], "LOCATION")
        edge(claim_id, lid, "LOCATED_AT")

    # match: candidates, chosen activity or split children
    for c in _q(conn, "SELECT * FROM candidate_matches WHERE event_id=%s ORDER BY rank_order", (event_id,)):
        edge(claim_id, activity_node(c["activity_id"]), f"CANDIDATE_{c['rank_order']}", c.get("composite_confidence"))
    if ev.get("matched_activity_id"):
        edge(claim_id, activity_node(ev["matched_activity_id"]), "MATCHED_TO")
    splits = _q(conn, "SELECT * FROM claim_activity_splits WHERE event_id=%s ORDER BY activity_id", (event_id,))
    for s in splits:
        edge(claim_id, activity_node(s["activity_id"]), f"SPLIT_{s['split_basis']}", s.get("split_pct"))

    # flags
    for v in _q(conn, "SELECT * FROM validation_issues WHERE event_id=%s", (event_id,)):
        vid = f"validation:{v['issue_id']}"
        node(vid, v.get("rule_code") or "VALIDATION", "VALIDATION", severity=v.get("severity"), description=v.get("description"))
        edge(claim_id, vid, "FLAGGED_BY")
    for c in _q(conn, "SELECT * FROM conflict_records WHERE event_id_a=%s OR event_id_b=%s", (event_id, event_id)):
        cid = f"conflict:{c['conflict_id']}"
        node(cid, f"Conflict {c.get('value_a')} vs {c.get('value_b')}", "CONFLICT",
             status=c.get("status"), variance_pct=c.get("variance_pct"), reporting_period=c.get("reporting_period"))
        edge(claim_id, cid, "IN_CONFLICT")
        other = c["event_id_b"] if c["event_id_a"] == event_id else c["event_id_a"]
        oid = f"claim:{other}"
        node(oid, f"Claim {other[:8]}", "CLAIM", event_id=other)
        edge(cid, oid, "CONFLICTS_WITH")
    for l in _q(conn, "SELECT * FROM evidence_links WHERE event_id_a=%s OR event_id_b=%s", (event_id, event_id)):
        other = l["event_id_b"] if l["event_id_a"] == event_id else l["event_id_a"]
        oid = f"claim:{other}"
        o = _q(conn, "SELECT raw_claim_text, input_channel FROM execution_events WHERE event_id=%s", (other,))
        o = o[0] if o else {}
        node(oid, f"Claim {other[:8]} ({o.get('input_channel') or 'other channel'})", "CLAIM", event_id=other)
        edge(claim_id, oid, l["relation_type"], l.get("confidence"))
    for p in _q(conn, "SELECT * FROM planner_decisions WHERE event_id=%s ORDER BY decided_at", (event_id,)):
        pid = f"decision:{p['decision_id']}"
        node(pid, f"{p['action']} by supervisor", "DECISION", justification=p.get("justification"), decided_at=p.get("decided_at"))
        edge(claim_id, pid, "DECIDED_BY")

    # direct dependency context of the affected activities
    focus = [ev["matched_activity_id"]] if ev.get("matched_activity_id") else [s["activity_id"] for s in splits]
    for aid in [a for a in focus if a]:
        for d in _q(conn, "SELECT * FROM schedule_dependencies WHERE schedule_id=%s AND (predecessor_activity_id=%s OR successor_activity_id=%s)",
                    (sid, aid, aid)):
            edge(activity_node(d["predecessor_activity_id"]), activity_node(d["successor_activity_id"]),
                 f"DEPENDS_{d['relationship_type']}")
    return {"event_id": event_id, "nodes": list(nodes.values()), "edges": edges}


@router.get("/claims/{event_id}/knowledge-graph")
def claim_knowledge_graph(event_id: str, _event_context: EventContext = Depends(gates.event_review)):
    with get_connection() as conn:
        return build_claim_graph(conn, event_id)


# ---------------------------------------------------------------------------
# Ask Why
# ---------------------------------------------------------------------------

def _state(actual: Optional[Dict[str, Any]]) -> str:
    if not actual:
        return "no approved progress yet"
    if actual.get("actual_finish") or (actual.get("actual_pct_complete") or 0) >= 100:
        return f"completed ({actual.get('actual_pct_complete')}%)"
    if actual.get("actual_start") or actual.get("actual_pct_complete"):
        return f"in progress ({actual.get('actual_pct_complete')}%)"
    if actual.get("actual_quantity"):
        return f"in progress ({actual['actual_quantity']:g} approved quantity)"
    return "not started"


def explain_activity(conn, activity_id: str, schedule_id: str, event_id: Optional[str], depth: int) -> Dict[str, Any]:
    act = _q(conn, "SELECT * FROM schedule_activities WHERE schedule_id=%s AND activity_id=%s", (schedule_id, activity_id))
    if not act:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Activity '{activity_id}' not found in schedule")
    act = act[0]
    steps: List[str] = []
    entities: List[Dict[str, str]] = [{"name": activity_id, "type": "ACTIVITY", "role": "Root of this explanation"}]
    refs: List[str] = []

    fl = act.get("total_float")
    crit = ("critical path activity (float 0)" if act.get("is_critical") or (fl is not None and fl <= 0)
            else f"total float {fl:g} day(s)" if fl is not None else "float not provided by the source schedule")
    steps.append(f"{activity_id} '{act['activity_name']}' is planned {act['planned_start']} to {act['planned_finish']}; {crit}.")

    ev = None
    if event_id:
        ev = _load_event(conn, event_id)
        cand = _q(conn, "SELECT * FROM candidate_matches WHERE event_id=%s AND activity_id=%s", (event_id, activity_id))
        if cand:
            c = cand[0]
            steps.append(f"Claim {event_id[:8]} was matched to it as rank {c['rank_order']} via {c['match_tier']} "
                         f"(confidence {c['composite_confidence']:.2f}). {c.get('supporting_signals') or ''}".strip())
            if c.get("disqualifying_signals"):
                steps.append(f"Disqualifying signals: {c['disqualifying_signals']}")
        sp = _q(conn, "SELECT * FROM claim_activity_splits WHERE event_id=%s", (event_id,))
        mine = next((s for s in sp if s["activity_id"] == activity_id), None)
        if mine:
            steps.append(f"It received {mine['split_pct'] * 100:.1f}% of this broad claim ({mine['split_basis']}): {mine.get('rationale') or ''}".strip())
        elif sp:
            steps.append("This claim was decomposed across sibling activities; this activity did not receive a share.")
        for v in _q(conn, "SELECT * FROM validation_issues WHERE event_id=%s", (event_id,)):
            steps.append(f"Validation {v['rule_code']} ({v['severity']}): {v['description']}")
        for c in _q(conn, "SELECT * FROM conflict_records WHERE event_id_a=%s OR event_id_b=%s", (event_id, event_id)):
            steps.append(f"Conflict ({c['status']}): {c['value_a']} vs {c['value_b']} on {c['reporting_period']} (variance {c['variance_pct']}).")
        for l in _q(conn, "SELECT * FROM evidence_links WHERE event_id_a=%s OR event_id_b=%s", (event_id, event_id)):
            steps.append(f"Cross-channel evidence {l['relation_type']} (confidence {l['confidence']}): {l['rationale']}")
            entities.append({"name": (l["event_id_b"] if l["event_id_a"] == event_id else l["event_id_a"])[:8],
                             "type": "CLAIM", "role": l["relation_type"].title()})
        if ev.get("priority_reasons"):
            steps.append("Review priority: " + " ".join(str(ev["priority_reasons"]).split("\n")[:2]))
        for r in _q(conn, "SELECT file_name, sheet_name, row_cell_ref, raw_snippet FROM source_references WHERE event_id=%s", (event_id,)):
            refs.append(" ".join(x for x in [r.get("file_name"), r.get("sheet_name"), r.get("row_cell_ref")] if x) + f": “{_short(r['raw_snippet'], 90)}”")

    # upstream dependency chain, bounded by depth (depth 1 = direct predecessors)
    seen = {activity_id}
    frontier = deque([(activity_id, 1)])
    while frontier:
        cur, hop = frontier.popleft()
        if hop > max(depth, 1):
            continue
        for d in _q(conn, "SELECT * FROM schedule_dependencies WHERE schedule_id=%s AND successor_activity_id=%s", (schedule_id, cur)):
            pid = d["predecessor_activity_id"]
            p = _q(conn, "SELECT * FROM schedule_activities WHERE schedule_id=%s AND activity_id=%s", (schedule_id, pid))
            p = p[0] if p else {}
            actual = _q(conn, "SELECT * FROM approved_actuals WHERE schedule_id=%s AND activity_id=%s", (schedule_id, pid))
            actual = actual[0] if actual else None
            lag = d.get("lag_days") or 0
            line = (f"Hop {hop}: {cur} depends on {pid} ({d['relationship_type']}{f', lag {lag:g}d' if lag else ''}); "
                    f"{pid} is {_state(actual)}, planned finish {p.get('planned_finish')}"
                    + (f", float {p['total_float']:g}d" if p.get("total_float") is not None else "") + ".")
            reasons = _q(conn, """SELECT DISTINCT delay_reason FROM execution_events
                                  WHERE schedule_id=%s AND (matched_activity_id=%s OR reported_activity_id=%s) AND delay_reason IS NOT NULL""",
                         (schedule_id, pid, pid))
            if reasons:
                line += " Reported delay reason(s): " + ", ".join(r["delay_reason"] for r in reasons) + "."
            flags = _q(conn, """SELECT DISTINCT vi.rule_code FROM validation_issues vi JOIN execution_events e ON e.event_id=vi.event_id
                                WHERE e.schedule_id=%s AND e.matched_activity_id=%s""", (schedule_id, pid))
            if flags:
                line += " Flagged: " + ", ".join(f["rule_code"] for f in flags) + "."
            steps.append(line)
            entities.append({"name": pid, "type": "PREDECESSOR", "role": f"{d['relationship_type']} predecessor at hop {hop}"})
            if pid not in seen and hop < depth:
                seen.add(pid)
                frontier.append((pid, hop + 1))
    if len(steps) == 1:
        steps.append("No claim context, flags or predecessors are recorded for this activity.")
    return {
        "activity_id": activity_id,
        "event_id": event_id,
        "traversal_depth": depth,
        "explanation": steps[1] if len(steps) > 1 else steps[0],
        "reasoning_steps": steps,
        "entities_involved": entities,
        "evidence_references": refs,
    }


@router.get("/graph/explain/{activity_id}")
def ask_why(
    activity_id: str,
    event_id: Optional[str] = Query(default=None),
    depth: int = Query(default=1, ge=1, le=6),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.review_or_view_schedule),
):
    schedule_id = schedule_context.schedule_id
    if event_id:
        # the event must belong to this project AND to this schedule
        ev = load_event_in_project(schedule_context.project_context, event_id)
        if ev.schedule_id != schedule_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "event_id does not belong to the requested schedule")
    with get_connection() as conn:
        return explain_activity(conn, activity_id, schedule_id, event_id, depth)
