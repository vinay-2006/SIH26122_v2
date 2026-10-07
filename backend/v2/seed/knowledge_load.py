"""Load the generated project knowledge into a database. Additive and idempotent:
  * an entry that does not exist yet is inserted;
  * an existing FROM_RECORDS entry is refreshed when the project's records changed (a new version, audited);
  * an entry a person wrote or edited (anything not FROM_RECORDS, or whose provenance was changed) is NEVER overwritten.
The loader writes in system mode (the database role guard allows generated content only there) and records each change in the audit chain as the project's Project Manager."""
from __future__ import annotations

from typing import Any, Dict, List

from .. import audit
from . import knowledge_content as kc


def load_project(c, project_id) -> Dict[str, int]:
    """c: a connection already in system mode, inside a transaction. Returns counts."""
    pm = c.execute("select created_by from projects where project_id = %s", (project_id,)).fetchone()["created_by"]
    facts = kc.collect_facts(c, project_id)
    counts = {"inserted": 0, "refreshed": 0, "unchanged": 0, "kept_edited": 0}
    for e in kc.build_entries(facts):
        cur = c.execute("select * from project_knowledge where project_id = %s and section = %s and lower(title) = lower(%s) and status = 'ACTIVE'", (project_id, e["section"], e["title"])).fetchone()
        if cur is None:
            r = c.execute("insert into project_knowledge (project_id, section, title, body, provenance, tags, sort_order, created_by, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning knowledge_id",
                          (project_id, e["section"], e["title"], e["body"], e["provenance"], e["tags"], e["sort_order"], pm, pm)).fetchone()
            audit.log(c, project_id=project_id, actor_id=pm, role="PROJECT_MANAGER", action="KNOWLEDGE_CREATED", entity_type="PROJECT_KNOWLEDGE", entity_id=r["knowledge_id"],
                      after={"section": e["section"], "title": e["title"], "provenance": e["provenance"], "version": 1, "source": "generated"})
            counts["inserted"] += 1
        elif cur["body"] == e["body"] and cur["provenance"] == e["provenance"]:
            counts["unchanged"] += 1
        elif cur["provenance"] == "FROM_RECORDS" and e["provenance"] == "FROM_RECORDS" and cur["updated_by"] == cur["created_by"]:
            c.execute("update project_knowledge set body = %s, tags = %s, sort_order = %s, version = version + 1, updated_by = %s where knowledge_id = %s", (e["body"], e["tags"], e["sort_order"], pm, cur["knowledge_id"]))
            audit.log(c, project_id=project_id, actor_id=pm, role="PROJECT_MANAGER", action="KNOWLEDGE_UPDATED", entity_type="PROJECT_KNOWLEDGE", entity_id=cur["knowledge_id"],
                      before={"version": cur["version"], "body": cur["body"][:2000]}, after={"version": cur["version"] + 1, "body": e["body"][:2000], "source": "refreshed from the project records"})
            counts["refreshed"] += 1
        else:
            counts["kept_edited"] += 1
    return counts


def load_all(conn, project_codes: List[str] = None) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    with conn.transaction():
        conn.execute("select set_config('app.system','on',true)")
        rows = conn.execute("select project_id, project_code from projects where project_code = any(%s) order by project_code", (project_codes or list(kc.NARRATIVE),)).fetchall()
        for r in rows:
            out[r["project_code"]] = load_project(conn, r["project_id"])
    return out
