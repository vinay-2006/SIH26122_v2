"""Project knowledge: the authored context of a project, read by every member, written by the Project Manager, audited on every change, never deleted (retired).
`rank()` is the deterministic search the agents use to ground an answer: a section is returned with its provenance so the answer can cite it."""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from .. import audit
from ..errors import ApiError
from .common import PM, ProjectActor, actor_tx, require_role, require_writable

SECTIONS = ("OVERVIEW", "SCOPE", "CONTRACT", "SITE", "STAKEHOLDERS", "MILESTONES", "CONSTRAINTS", "RISKS", "SAFETY_QUALITY", "PROCUREMENT", "REPORTING", "GLOSSARY")
SECTION_LABEL = {"OVERVIEW": "Project overview", "SCOPE": "Scope of work", "CONTRACT": "Contract context and key dates", "SITE": "Site and access",
                 "STAKEHOLDERS": "Stakeholders", "MILESTONES": "Milestones and phases", "CONSTRAINTS": "Constraints", "RISKS": "Risks",
                 "SAFETY_QUALITY": "Safety and quality requirements", "PROCUREMENT": "Procurement and long-lead items", "REPORTING": "Reporting rules", "GLOSSARY": "Glossary"}
TYPED_PROVENANCE = ("AUTHORED", "ILLUSTRATIVE", "NOT_SPECIFIED")           # what a person may choose; FROM_RECORDS is generated from the project's own records
PUBLIC = "knowledge_id, project_id, section, title, body, provenance, tags, sort_order, status, version, created_by, created_at, updated_by, updated_at"


def _check(section: str, title: str, body: str, provenance: str, tags: Sequence[str]) -> None:
    if section not in SECTIONS:
        raise ApiError(422, "BAD_SECTION", f"section must be one of {', '.join(SECTIONS)}")
    if provenance not in TYPED_PROVENANCE:
        raise ApiError(422, "BAD_PROVENANCE", f"provenance must be one of {', '.join(TYPED_PROVENANCE)}")
    if not (3 <= len(title.strip()) <= 160):
        raise ApiError(422, "BAD_TITLE", "A title is 3 to 160 characters")
    if not (3 <= len(body.strip()) <= 8000):
        raise ApiError(422, "BAD_BODY", "The text is 3 to 8000 characters")
    if len(tags) > 20 or any(not (1 <= len(t) <= 40) for t in tags):
        raise ApiError(422, "BAD_TAGS", "At most 20 tags of 1 to 40 characters")


def list_entries(actor: ProjectActor, section: Optional[str] = None, include_retired: bool = False) -> List[Dict[str, Any]]:
    with actor_tx(actor, readonly=True) as c:
        return c.execute(f"select {PUBLIC} from project_knowledge where project_id = %s and (%s::text is null or section = %s) and (%s or status = 'ACTIVE') "
                         "order by array_position(%s::text[], section), sort_order, lower(title)",
                         (actor.project_id, section, section, include_retired and actor.role == PM, list(SECTIONS))).fetchall()


def upsert(actor: ProjectActor, *, section: str, title: str, body: str, provenance: str = "AUTHORED", tags: Sequence[str] = (), sort_order: int = 100,
           knowledge_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    require_role(actor, PM, what="editing project knowledge")
    require_writable(actor)
    _check(section, title, body, provenance, list(tags))
    with actor_tx(actor, write=True) as c:
        if knowledge_id is None:
            clash = c.execute("select 1 from project_knowledge where project_id = %s and section = %s and lower(title) = lower(%s) and status = 'ACTIVE'", (actor.project_id, section, title.strip())).fetchone()
            if clash:
                raise ApiError(409, "DUPLICATE_ENTRY", "This section already has an entry with that title")
            r = c.execute(f"insert into project_knowledge (project_id, section, title, body, provenance, tags, sort_order, created_by, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning {PUBLIC}",
                          (actor.project_id, section, title.strip(), body.strip(), provenance, list(tags), sort_order, actor.user_id, actor.user_id)).fetchone()
            audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="KNOWLEDGE_CREATED", entity_type="PROJECT_KNOWLEDGE", entity_id=r["knowledge_id"],
                      after={"section": section, "title": r["title"], "provenance": provenance, "version": 1})
            return r
        old = c.execute("select * from project_knowledge where project_id = %s and knowledge_id = %s for update", (actor.project_id, knowledge_id)).fetchone()
        if old is None:
            raise ApiError(404, "KNOWLEDGE_NOT_FOUND", "No such entry in this project")
        if old["status"] != "ACTIVE":
            raise ApiError(409, "ENTRY_RETIRED", "A retired entry is not edited; add a new one")
        clash = c.execute("select 1 from project_knowledge where project_id = %s and section = %s and lower(title) = lower(%s) and status = 'ACTIVE' and knowledge_id <> %s",
                          (actor.project_id, section, title.strip(), knowledge_id)).fetchone()
        if clash:
            raise ApiError(409, "DUPLICATE_ENTRY", "This section already has an entry with that title")
        r = c.execute(f"update project_knowledge set section = %s, title = %s, body = %s, provenance = %s, tags = %s, sort_order = %s, version = version + 1, updated_by = %s "
                      f"where project_id = %s and knowledge_id = %s returning {PUBLIC}",
                      (section, title.strip(), body.strip(), provenance, list(tags), sort_order, actor.user_id, actor.project_id, knowledge_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="KNOWLEDGE_UPDATED", entity_type="PROJECT_KNOWLEDGE", entity_id=knowledge_id,
                  before={"section": old["section"], "title": old["title"], "provenance": old["provenance"], "version": old["version"], "body": old["body"][:2000]},
                  after={"section": section, "title": r["title"], "provenance": provenance, "version": r["version"], "body": r["body"][:2000]})
        return r


def retire(actor: ProjectActor, knowledge_id: uuid.UUID) -> Dict[str, Any]:
    require_role(actor, PM, what="retiring project knowledge")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        old = c.execute("select * from project_knowledge where project_id = %s and knowledge_id = %s for update", (actor.project_id, knowledge_id)).fetchone()
        if old is None:
            raise ApiError(404, "KNOWLEDGE_NOT_FOUND", "No such entry in this project")
        if old["status"] != "ACTIVE":
            raise ApiError(409, "ENTRY_RETIRED", "Already retired")
        r = c.execute(f"update project_knowledge set status = 'RETIRED', version = version + 1, updated_by = %s where project_id = %s and knowledge_id = %s returning {PUBLIC}",
                      (actor.user_id, actor.project_id, knowledge_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="KNOWLEDGE_RETIRED", entity_type="PROJECT_KNOWLEDGE", entity_id=knowledge_id,
                  before={"section": old["section"], "title": old["title"], "version": old["version"]}, after={"status": "RETIRED"})
        return r


# ------------------------------------------------------------------------------------------------ deterministic retrieval for the agents
_WORD = re.compile(r"[a-z0-9][a-z0-9\-_/.]*", re.I)
_STOP = set("a an and are as at be by for from how in is it of on or that the this to was what when where which who why will with do does did can could should about tell me give show list".split())
_SECTION_HINTS = {"OVERVIEW": ("overview", "project", "about", "summary"), "SCOPE": ("scope", "work", "include", "included", "deliverable", "deliverables"), "CONTRACT": ("contract", "client", "key date", "dates", "completion"),
                  "SITE": ("site", "access", "location", "terrain", "weather", "where"), "STAKEHOLDERS": ("stakeholder", "stakeholders", "who", "team", "responsible", "contractor", "owner"),
                  "MILESTONES": ("milestone", "milestones", "phase", "phases", "stage", "stages"), "CONSTRAINTS": ("constraint", "constraints", "limit", "limits", "restriction"),
                  "RISKS": ("risk", "risks", "threat", "delay"), "SAFETY_QUALITY": ("safety", "quality", "hse", "inspection", "permit", "hold point", "itp"),
                  "PROCUREMENT": ("procurement", "long-lead", "long lead", "material", "materials", "delivery", "supply", "vendor"), "REPORTING": ("report", "reporting", "claim", "claims", "evidence", "approve"),
                  "GLOSSARY": ("glossary", "meaning", "mean", "means", "definition", "define", "abbreviation", "term")}


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _WORD.findall(text or "") if t.lower() not in _STOP and len(t) > 1]


def rank(query: str, entries: Sequence[Dict[str, Any]], k: int = 3, min_score: float = 1.0) -> List[Dict[str, Any]]:
    """the entries whose title / tags / body best match the question: token overlap (title and tags weigh more) plus a bonus when the question names the section.
    Deterministic, explainable, no model. Entries with nothing in common are not returned (the caller then says the information is not recorded)."""
    q = _tokens(query)
    if not q:
        return []
    qset = set(q)
    ql = (query or "").lower()
    scored = []
    for e in entries:
        title_t, tag_t, body_t = set(_tokens(e["title"])), set(_tokens(" ".join(e.get("tags") or []))), set(_tokens(e["body"]))
        score = 3.0 * len(qset & title_t) + 2.0 * len(qset & tag_t) + 1.0 * len(qset & body_t)
        if any(h in ql for h in _SECTION_HINTS.get(e["section"], ())):
            score += 2.0
        if score >= min_score:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], SECTIONS.index(x[1]["section"]), x[1]["sort_order"]))
    return [dict(e, _score=s) for s, e in scored[:k]]


def cite(e: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": "PROJECT_KNOWLEDGE", "knowledge_id": str(e["knowledge_id"]), "section": e["section"], "section_label": SECTION_LABEL[e["section"]], "title": e["title"],
            "provenance": e["provenance"], "version": e["version"]}
