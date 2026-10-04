"""Lessons Radar: institutional memory turned from an archive into a forward-looking warning system.

For the work that is under way or about to start, find the lessons this organisation has already paid for (its own and those other projects chose to share), say WHY each applies,
what delay it caused last time, and what corrective action worked. Pure and deterministic: the same schedule and lessons give the same radar. Relevance is explainable:
  * shared terms between the activity and the lesson (word stems, so "piling" meets "piles"), title matches weighing more than body matches;
  * wording similarity from the sentence-embedding model when it is available (never required);
  * a small bonus for the same discipline, and for a lesson whose category is typical for that kind of work (e.g. a delivery delay for a procurement activity).
No figure is generated: delay days, counts and actions are the recorded values of real lessons."""
from __future__ import annotations

import re
import statistics
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

GENERIC = set("""a an and the of for to in on at with by from or as is are was were this that it its
unit units work works project stage stages spread area areas installation complete completed completion mechanically civil phase system systems test testing tests
new old first second third one two three four five six seven eight nine ten per site plant""".split())
CATEGORY_NAME = {"MATERIAL_DELIVERY_DELAY": "material delivery delays", "EQUIPMENT_SHORTAGE": "equipment availability", "PERMIT_APPROVAL": "permit and approval delays",
                 "WEATHER": "weather disruption", "LABOUR_SHORTAGE": "labour shortage", "TECHNICAL": "technical problems"}
# stems that mark work typically exposed to a kind of delay (a small, explicit prior, shown to the reader when it is used)
CATEGORY_HINTS: Dict[str, Set[str]] = {
    "MATERIAL_DELIVERY_DELAY": {"procur", "deliver", "supply", "fabricat", "steel", "valv", "fitting", "material"},
    "EQUIPMENT_SHORTAGE": {"crane", "lift", "erect", "rig", "vessel", "equipment", "heavy"},
    "PERMIT_APPROVAL": {"permit", "approv", "clearanc", "cross", "rail", "highway", "statutor", "authority"},
}
MIN_RELEVANCE = 0.4
SEMANTIC_FLOOR = 0.55            # the embedding model alone may carry a match only when it is clearly similar
SINGLE_WORD_SEMANTIC = 0.40      # ... while a single shared word needs the model at least to lean the same way


def stem(word: str) -> str:
    w = word.lower()
    for _ in range(2):
        if len(w) > 5 and w.endswith("ing"):
            w = w[:-3]
        elif len(w) > 4 and w.endswith("ed"):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("es"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def tokens(text: str, drop: Optional[Set[str]] = None) -> Dict[str, str]:
    """informative stems of a text -> the first original word that produced each (for showing the reader the shared terms). `drop`: stems too common in this project to tell work apart"""
    out: Dict[str, str] = {}
    for raw in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or ""):
        low = raw.lower()
        if low in GENERIC:
            continue
        out.setdefault(stem(low), low)
    return {k: v for k, v in out.items() if k not in GENERIC and not (drop and k in drop)}


def _median(xs: Sequence[float]) -> float:
    return float(statistics.median(xs))


def relevance(activity: Dict[str, Any], lesson: Dict[str, Any], sem: Optional[float] = None, drop: Optional[Set[str]] = None) -> Optional[Dict[str, Any]]:
    a = tokens(activity["name"], drop)
    title_t = tokens(lesson["title"])
    body_t = tokens(" ".join(filter(None, [lesson.get("root_cause"), lesson.get("corrective_action"), lesson.get("lessons_learned"), lesson.get("narrative")])))
    shared_title = set(a) & set(title_t)
    shared_body = (set(a) & set(body_t)) - shared_title
    lex = 0.0
    if shared_title:
        lex = min(0.9, 0.5 + 0.15 * (len(shared_title) - 1) + 0.05 * len(shared_body))
    elif shared_body:
        lex = min(0.7, 0.35 + 0.1 * (len(shared_body) - 1))
    semantic = sem if (sem is not None and sem >= SEMANTIC_FLOOR) else 0.0
    base = max(lex, semantic)
    if base <= 0:
        return None
    prior = lesson.get("category") in CATEGORY_HINTS and any(t.startswith(h) for t in a for h in CATEGORY_HINTS[lesson["category"]])
    same_disc = bool(activity.get("discipline") and lesson.get("discipline") and activity["discipline"] == lesson["discipline"])
    n_shared = len(shared_title | shared_body)
    agrees = sem is not None and sem >= SINGLE_WORD_SEMANTIC
    if not semantic:
        # a little shared wording is weak evidence: one word, or two words that are only in the lesson's body, need the embedding model to lean the same way, a typical-category
        # prior or the same discipline (and, when no model is available, a word in the lesson's title)
        weak = n_shared == 1 or (n_shared == 2 and not shared_title)
        if weak and not (agrees or prior or same_disc or (sem is None and shared_title)):
            return None
    reasons: List[str] = []
    if shared_title or shared_body:
        words = sorted({a[s] for s in shared_title | shared_body} | {title_t.get(s, body_t.get(s)) for s in shared_title | shared_body})
        reasons.append("shared terms: " + ", ".join(words))
    if semantic and semantic >= lex:
        reasons.append(f"similar wording (semantic similarity {semantic:.2f})")
    bonus = 0.0
    if same_disc:
        bonus += 0.08
        reasons.append("same discipline")
    cat = lesson.get("category")
    if prior:
        bonus += 0.12
        reasons.append(f"work of this kind is typically exposed to {CATEGORY_NAME.get(cat, cat.lower())}")
    score = round(min(1.0, base + bonus), 3)
    return {"score": score, "reasons": reasons} if score >= MIN_RELEVANCE else None


def build(activities: Iterable[Dict[str, Any]], lessons: Sequence[Dict[str, Any]], *, reference: date, horizon_days: int,
          similarity: Optional[Callable[[str, Dict[str, Any]], Optional[float]]] = None, max_activities: int = 12, per_activity: int = 3) -> Dict[str, Any]:
    """activities: {activity_id, name, stage, discipline, planned_start, planned_finish (date), state}; lessons: records of institutional memory
    (memory_id, title, root_cause, corrective_action, lessons_learned, outcome, narrative, category, discipline, delay_days, project_name, own_project: bool)"""
    end = reference.toordinal() + horizon_days
    items: List[Dict[str, Any]] = []
    considered = 0
    acts = list(activities)
    # words found in a quarter or more of this project's activities (a unit or area name) do not tell one piece of work from another
    freq: Dict[str, int] = {}
    for a in acts:
        for t in tokens(a["name"]):
            freq[t] = freq.get(t, 0) + 1
    drop = {t for t, n in freq.items() if len(acts) >= 8 and n / len(acts) >= 0.25}
    for a in acts:
        if a["state"] == "COMPLETED" or a["planned_start"] is None or a["planned_finish"] is None:
            continue
        if a["planned_start"].toordinal() > end or a["planned_finish"] < reference:
            continue
        considered += 1
        hits = []
        for l in lessons:
            sem = similarity(f"{a['name']} {a.get('stage') or ''}", l) if similarity else None
            r = relevance(a, l, sem, drop)
            if r:
                hits.append((r["score"], l["memory_id"], l, r))
        if not hits:
            continue
        hits.sort(key=lambda h: (-h[0], str(h[1])))
        top = hits[:per_activity]
        delays = [float(h[2]["delay_days"]) for h in top if h[2].get("delay_days") is not None]
        actions = list(dict.fromkeys(h[2]["corrective_action"].strip() for h in top if (h[2].get("corrective_action") or "").strip()))[:3]
        days_until = (a["planned_start"] - reference).days
        items.append({
            "activity_id": a["activity_id"], "activity_name": a["name"], "stage_name": a.get("stage"), "discipline": a.get("discipline"),
            "state": a["state"], "planned_start": a["planned_start"].isoformat(), "planned_finish": a["planned_finish"].isoformat(),
            "timing": "IN_PROGRESS" if (a["state"] == "IN_PROGRESS" or days_until <= 0) else "UPCOMING", "starts_in_days": max(days_until, 0),
            "best_score": top[0][0],
            "lessons": [{"memory_id": str(h[1]), "title": h[2]["title"], "score": h[0], "why_matched": h[3]["reasons"], "root_cause": h[2].get("root_cause"),
                         "corrective_action": h[2].get("corrective_action"), "outcome": h[2].get("outcome"), "delay_days": h[2].get("delay_days"),
                         "project_name": h[2].get("project_name"), "from_other_project": not h[2].get("own_project", True)} for h in top],
            "expected_delay": {"samples": len(delays), "median_days": _median(delays), "max_days": max(delays)} if delays else None,
            "preventive_actions": actions,
        })
    items.sort(key=lambda i: (0 if i["timing"] == "IN_PROGRESS" else 1, i["starts_in_days"], -i["best_score"], i["activity_id"]))
    return {"reference_date": reference.isoformat(), "horizon_days": horizon_days, "activities_in_window": considered, "activities_with_lessons": len(items),
            "lessons_considered": len(lessons), "items": items[:max_activities]}
