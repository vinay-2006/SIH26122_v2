"""Schedule import -> reconciliation -> build -> activation (and rollback).

Lifecycle:  upload (STAGED: parsed + validated, nothing in the schedule tables)
         -> review: mapping + reconciliation decisions
         -> build (ONE transaction: DRAFT version + WBS/activities/resources/dependencies/lineage, then VALIDATED)
         -> activate (locks the version; supersedes the previous ACTIVE one) / rollback (re-activate a SUPERSEDED version)
Every database write runs as the authenticated actor, so the database's own guards apply. Activation never touches progress history.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from .. import audit
from ..db import tx
from ..errors import ApiError
from ..schedule_import.csv_parser import parse_csv
from ..schedule_import.detect import detect_format
from ..schedule_import.mapping import RefData, normalize_wbs, propose_wbs_types, wbs_depths
from ..schedule_import.models import ParseError, ParsedSchedule
from ..schedule_import.mspdi_parser import parse_mspdi
from ..schedule_import.prepare import prepare
from ..schedule_import.reconcile import NewActivity, OldActivity, reconcile
from ..schedule_import.validate import validate
from ..schedule_import.xer_parser import parse_xer

WBS_TYPES = {"PROJECT", "STAGE", "AREA", "SUB_ASSET", "PACKAGE"}
HEADER_KEYS = {"baseline_name", "label", "data_date", "planned_start", "planned_finish"}
MAX_STAGED_BYTES = 40_000_000          # the parsed schedule is kept as JSONB while the PM reviews it


# ------------------------------------------------------------------------------------------------------ reference + base state
def load_ref(conn) -> RefData:
    return RefData(
        discipline_codes={r["code"] for r in conn.execute("select code from disciplines").fetchall()},
        discipline_aliases={r["alias"]: r["code"] for r in conn.execute("select alias, code from discipline_aliases").fetchall()},
        uoms={r["code"]: r["dimension"] for r in conn.execute("select code, dimension from units_of_measure").fetchall()})


def base_version(conn, project_id) -> Optional[dict]:
    """The version a new upload is reconciled against: the ACTIVE one, else the newest locked one."""
    return conn.execute(
        "select * from schedule_versions where project_id = %s and (status = 'ACTIVE' or locked_at is not null) "
        "order by (status = 'ACTIVE') desc, version_no desc limit 1", (project_id,)).fetchone()


def load_old_activities(conn, project_id, version_id) -> List[OldActivity]:
    acts = conn.execute(
        "select ba.activity_uid, ba.external_activity_id, ba.activity_name, w.wbs_code, ba.discipline_code, ba.baseline_start, "
        "ba.baseline_finish, ba.baseline_duration from baseline_activities ba join schedule_wbs w on w.wbs_id = ba.wbs_id "
        "where ba.version_id = %s order by ba.sequence", (version_id,)).fetchall()
    asg = conn.execute(
        "select br.activity_uid, br.assignment_uid, pr.resource_code, br.baseline_qty, br.unit_of_measure from baseline_resources br "
        "join project_resources pr on pr.resource_id = br.resource_id and pr.project_id = br.project_id where br.version_id = %s", (version_id,)).fetchall()
    prog_act = {r["activity_uid"] for r in conn.execute(
        "select distinct activity_uid from approved_activity_progress where project_id = %s union "
        "select distinct activity_uid from approved_resource_progress where project_id = %s", (project_id, project_id)).fetchall()}
    prog_asg = {r["assignment_uid"] for r in conn.execute(
        "select distinct assignment_uid from approved_resource_progress where project_id = %s", (project_id,)).fetchall()}
    by_uid: Dict[Any, OldActivity] = {}
    for a in acts:
        by_uid[a["activity_uid"]] = OldActivity(
            uid=str(a["activity_uid"]), external_id=a["external_activity_id"], name=a["activity_name"], wbs_code=a["wbs_code"],
            discipline=a["discipline_code"], start=a["baseline_start"], finish=a["baseline_finish"], duration=float(a["baseline_duration"]),
            has_progress=a["activity_uid"] in prog_act)
    for r in asg:
        o = by_uid.get(r["activity_uid"])
        if o is not None:
            o.assignments[r["resource_code"]] = dict(assignment_uid=str(r["assignment_uid"]), qty=float(r["baseline_qty"]), uom=r["unit_of_measure"])
            if r["assignment_uid"] in prog_asg:
                o.progress_assignments.add(str(r["assignment_uid"]))
    return list(by_uid.values())


def new_for_reconcile(ps: ParsedSchedule, ref: RefData, decisions) -> List[NewActivity]:
    return [NewActivity(external_id=p.a.external_id, name=p.a.name, wbs_code=p.a.wbs_code, discipline=p.discipline, start=p.a.start,
                        finish=p.a.finish, duration=p.duration,
                        assignments={c: dict(qty=x.qty, uom=x.uom) for c, x in p.assignments.items()}) for p in prepare(ps, ref, decisions)]


# ------------------------------------------------------------------------------------------------------ decisions
def _clean_decisions(patch: Dict[str, Any], ref: RefData, current: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(current)
    for key, val in (patch or {}).items():
        if key == "discipline_map":
            bad = {k: v for k, v in val.items() if v not in ref.discipline_codes}
            if bad:
                raise ApiError(422, "BAD_DECISION", f"Unknown discipline code(s): {sorted(set(bad.values()))}")
            out["discipline_map"] = {**(out.get("discipline_map") or {}), **{" ".join(k.lower().split()): v for k, v in val.items()}}
        elif key == "uom_map":
            bad = {k: v for k, v in val.items() if v not in ref.uoms}
            if bad:
                raise ApiError(422, "BAD_DECISION", f"Unknown unit code(s): {sorted(set(bad.values()))}")
            out["uom_map"] = {**(out.get("uom_map") or {}), **{" ".join(k.lower().split()): v for k, v in val.items()}}
        elif key == "wbs_types":
            bad = {k: v for k, v in val.items() if v not in WBS_TYPES}
            if bad:
                raise ApiError(422, "BAD_DECISION", f"WBS node type must be one of {sorted(WBS_TYPES)}")
            out["wbs_types"] = {**(out.get("wbs_types") or {}), **val}
        elif key == "reconcile":
            r = val or {}
            unknown = set(r) - {"accept", "new", "retire", "split", "merge"}
            if unknown:
                raise ApiError(422, "BAD_DECISION", f"Unknown reconciliation key(s): {sorted(unknown)}")
            for s in r.get("split") or []:
                if not s.get("from") or len(s.get("to") or []) < 2 or any("ext" not in t or "fraction" not in t for t in s["to"]):
                    raise ApiError(422, "BAD_DECISION", "a split needs 'from' and at least two 'to' entries with ext and fraction")
            for m in r.get("merge") or []:
                if len(m.get("from") or []) < 2 or not m.get("to"):
                    raise ApiError(422, "BAD_DECISION", "a merge needs at least two 'from' activities and one 'to'")
            out["reconcile"] = r
        elif key == "header":
            bad = set(val) - HEADER_KEYS
            if bad:
                raise ApiError(422, "BAD_DECISION", f"Unknown header field(s): {sorted(bad)}")
            out["header"] = {**(out.get("header") or {}), **val}
        else:
            raise ApiError(422, "BAD_DECISION", f"Unknown decision key {key!r}")
    return out


def _fill_header(ps: ParsedSchedule, params: Dict[str, Any]) -> None:
    p, warn = ps.project, []
    from ..schedule_import.models import Issue
    for k, attr in (("data_date", "data_date"), ("planned_start", "planned_start"), ("planned_finish", "planned_finish")):
        if params.get(k):
            setattr(p, attr, date.fromisoformat(params[k]) if isinstance(params[k], str) else params[k])
    starts = [a.start for a in ps.activities if a.start]
    finishes = [a.finish for a in ps.activities if a.finish]
    if p.planned_start is None and starts:
        p.planned_start = min(starts); ps.issues.append(Issue("PLANNED_START_DERIVED", "Planned start taken from the earliest activity", None, "WARNING"))
    if p.planned_finish is None and finishes:
        p.planned_finish = max(finishes); ps.issues.append(Issue("PLANNED_FINISH_DERIVED", "Planned finish taken from the latest activity", None, "WARNING"))
    if p.data_date is None and p.planned_start:
        p.data_date = p.planned_start; ps.issues.append(Issue("DATA_DATE_ASSUMED", "No data date in the file; the baseline cut-off is assumed to be the planned start", None, "WARNING"))


def _parse(filename: str, content: bytes, resources: Optional[bytes], params: Dict[str, Any]) -> Tuple[str, ParsedSchedule]:
    try:
        fmt = detect_format(filename, content)
        if fmt == "XER":
            ps = parse_xer(content, params.get("xer_project_id"))
        elif fmt == "MSPDI":
            ps = parse_mspdi(content)
        else:
            ps = parse_csv(content, resources, params.get("project_name"))
    except ParseError as e:
        raise ApiError(422, e.code, e.message)
    _fill_header(ps, params)
    return fmt, ps


# ------------------------------------------------------------------------------------------------------ staging
def stage_import(user, project_id, filename: str, content: bytes, resources: Optional[bytes], params: Dict[str, Any]) -> Dict[str, Any]:
    fmt, ps = _parse(filename, content, resources, params)
    with tx() as c:
        ref = load_ref(c)
    report = validate(ps, ref)
    if not report["valid"]:
        raise ApiError(422, "IMPORT_INVALID", "The schedule has errors; nothing was imported", report)      # no writes at all
    payload = ps.to_dict()
    if len(json.dumps(payload)) > MAX_STAGED_BYTES:
        raise ApiError(413, "SCHEDULE_TOO_LARGE", "The parsed schedule is too large to stage; split it or remove unneeded detail")
    sha = hashlib.sha256(content + (b"\x00" + resources if resources else b"")).hexdigest()
    with tx(user.id) as c:
        dup = c.execute("select i.import_id from source_documents d join schedule_imports i on i.source_document_id = d.document_id "
                        "and i.project_id = d.project_id where d.project_id = %s and d.kind = 'SCHEDULE_FILE' and d.sha256 = %s",
                        (project_id, sha)).fetchone()
        if dup:
            raise ApiError(409, "DUPLICATE_UPLOAD", "This exact file was already uploaded", {"import_id": str(dup["import_id"])})
        doc = c.execute("insert into source_documents (project_id, kind, file_name, sha256, size_bytes, uploaded_by, mime_type) "
                        "values (%s,'SCHEDULE_FILE',%s,%s,%s,%s,%s) returning document_id",
                        (project_id, filename, sha, len(content) + len(resources or b""), user.id, fmt)).fetchone()
        imp = c.execute("insert into schedule_imports (project_id, source_document_id, format, status, validation_report, error_count, warning_count,"
                        " uploaded_by, file_name, baseline_name, parsed_payload, decisions) values (%s,%s,%s,'PARSED',%s,0,%s,%s,%s,%s,%s,%s) "
                        "returning import_id", (project_id, doc["document_id"], fmt, Jsonb(report), len(report["warnings"]), user.id, filename,
                                                params.get("baseline_name"), Jsonb(payload), Jsonb({}))).fetchone()
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="SCHEDULE_IMPORT_STAGED",
                  entity_type="SCHEDULE_IMPORT", entity_id=imp["import_id"], after={"file": filename, "format": fmt, "sha256": sha,
                                                                                 "activities": len(ps.activities)})
    return get_import(project_id, imp["import_id"])


def _load_import(c, project_id, import_id) -> dict:
    row = c.execute("select * from schedule_imports where project_id = %s and import_id = %s", (project_id, import_id)).fetchone()
    if row is None:
        raise ApiError(404, "IMPORT_NOT_FOUND", "No such import in this project")
    return row


def get_import(project_id, import_id) -> Dict[str, Any]:
    with tx() as c:
        imp = _load_import(c, project_id, import_id)
        ref = load_ref(c)
        ps = ParsedSchedule.from_dict(imp["parsed_payload"])
        d = imp["decisions"] or {}
        report = validate(ps, ref, d)
        norm = normalize_wbs(ps)
        types = {**propose_wbs_types(norm), **(d.get("wbs_types") or {})}
        base = base_version(c, project_id)
        recon = None
        if base is not None:
            recon = reconcile(load_old_activities(c, project_id, base["version_id"]), new_for_reconcile(ps, ref, d), d.get("reconcile"))
        built = c.execute("select version_id, version_no, status from schedule_versions where project_id = %s and import_id = %s",
                          (project_id, import_id)).fetchone()
    return {"import_id": str(import_id), "status": imp["status"], "format": imp["format"], "file_name": imp["file_name"],
            "header": {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ps.to_dict()["project"].items()},
                       **(d.get("header") or {})},
            "report": report, "wbs": [{"code": w.code, "name": w.name, "parent": w.parent_code, "type": types.get(w.code)} for w in norm.wbs],
            "base_version_id": str(base["version_id"]) if base else None, "reconciliation": recon, "decisions": d,
            "built_version": ({"version_id": str(built["version_id"]), "version_no": built["version_no"], "status": built["status"]} if built else None)}


def update_decisions(user, project_id, import_id, patch: Dict[str, Any]) -> Dict[str, Any]:
    with tx(user.id) as c:
        imp = _load_import(c, project_id, import_id)
        if imp["status"] != "PARSED":
            raise ApiError(409, "IMPORT_CLOSED", f"The import is {imp['status']} and can no longer be changed")
        merged = _clean_decisions(patch, load_ref(c), imp["decisions"] or {})
        c.execute("update schedule_imports set decisions = %s where import_id = %s", (Jsonb(merged), import_id))
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="SCHEDULE_IMPORT_DECISIONS",
                  entity_type="SCHEDULE_IMPORT", entity_id=import_id, after=merged)
    return get_import(project_id, import_id)


def discard_import(user, project_id, import_id) -> None:
    with tx(user.id) as c:
        imp = _load_import(c, project_id, import_id)
        if imp["status"] == "BUILT":
            raise ApiError(409, "IMPORT_BUILT", "A built import cannot be discarded; discard its version instead")
        c.execute("update schedule_imports set status = 'DISCARDED' where import_id = %s", (import_id,))
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="SCHEDULE_IMPORT_DISCARDED",
                  entity_type="SCHEDULE_IMPORT", entity_id=import_id)


# ------------------------------------------------------------------------------------------------------ build (atomic)
def build_import(user, project_id, import_id) -> Dict[str, Any]:
    with tx(user.id) as c:
        c.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(project_id),))
        imp = _load_import(c, project_id, import_id)
        if imp["status"] != "PARSED":
            raise ApiError(409, "IMPORT_CLOSED", f"The import is {imp['status']}")
        ref = load_ref(c)
        ps = ParsedSchedule.from_dict(imp["parsed_payload"])
        d = imp["decisions"] or {}
        report = validate(ps, ref, d)
        if not report["ready_to_build"]:
            raise ApiError(409, "IMPORT_NOT_READY", "Resolve the listed mapping items before building", report)
        base = base_version(c, project_id)
        old = load_old_activities(c, project_id, base["version_id"]) if base else []
        rec = reconcile(old, new_for_reconcile(ps, ref, d), d.get("reconcile")) if base else None
        if rec and rec["blockers"]:
            raise ApiError(409, "RECONCILIATION_INCOMPLETE", "Decisions are still needed before this revision can be built", rec["blockers"])
        out = _materialize(c, user, project_id, import_id, imp, ps, ref, d, base, old, rec)
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="SCHEDULE_VERSION_BUILT",
                  entity_type="SCHEDULE_VERSION", entity_id=out["version_id"], version_id=out["version_id"],
                  after={k: out[k] for k in ("version_no", "kind", "activities", "assignments", "dependencies")})
    return out


def _materialize(c, user, project_id, import_id, imp, ps: ParsedSchedule, ref: RefData, d, base, old: List[OldActivity], rec) -> Dict[str, Any]:
    norm = normalize_wbs(ps)
    prepared = {p.a.external_id: p for p in prepare(norm, ref, d)}
    hdr = {**ps.to_dict()["project"], **(d.get("header") or {})}
    pd = lambda k: date.fromisoformat(hdr[k]) if isinstance(hdr.get(k), str) else hdr.get(k)
    vno = c.execute("select coalesce(max(version_no), 0) + 1 as n from schedule_versions where project_id = %s", (project_id,)).fetchone()["n"]
    kind = "REVISION" if base else "BASELINE"
    name = hdr.get("baseline_name") or imp["baseline_name"] or ("Baseline Rev 0" if kind == "BASELINE" else f"Revision {vno - 1}")
    ver = c.execute(
        "insert into schedule_versions (project_id, version_no, kind, baseline_name, label, data_date, planned_start_date, planned_finish_date,"
        " import_id, parent_version_id, created_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning version_id",
        (project_id, vno, kind, name, hdr.get("label"), pd("data_date"), pd("planned_start"), pd("planned_finish"), import_id,
         base["version_id"] if base else None, user.id)).fetchone()
    vid = ver["version_id"]

    # All ids are generated here, so rows go in as a few pipelined batches (one network round trip per batch, not per row).
    # ---- WBS with stable wbs_uid
    old_wbs = c.execute("select wbs_id, wbs_uid, wbs_code, wbs_name, parent_wbs_id from schedule_wbs where version_id = %s", (base["version_id"],)).fetchall() if base else []
    by_code = {w["wbs_code"]: w for w in old_wbs}
    old_uid_by_id = {w["wbs_id"]: w["wbs_uid"] for w in old_wbs}
    by_name_parent = {(w["wbs_name"].lower(), old_uid_by_id.get(w["parent_wbs_id"])): w["wbs_uid"] for w in old_wbs}
    types = {**propose_wbs_types(norm), **(d.get("wbs_types") or {})}
    depth, ids, uids = wbs_depths(norm), {}, {}
    old_rules = {r["wbs_uid"]: r for r in c.execute(
        "select w.wbs_uid, r.weight_pct, r.completion_rule from wbs_stage_rules r join schedule_wbs w on w.wbs_id = r.wbs_id where w.version_id = %s",
        (base["version_id"],)).fetchall()} if base else {}
    wbs_rows, rule_rows = [], []
    for w in sorted(norm.wbs, key=lambda x: (depth[x.code], x.sequence)):          # parents first
        parent_uid = uids.get(w.parent_code)
        stable = by_code[w.code]["wbs_uid"] if w.code in by_code else by_name_parent.get((w.name.lower(), parent_uid))
        ids[w.code], uids[w.code] = uuid.uuid4(), stable or uuid.uuid4()
        wbs_rows.append((ids[w.code], project_id, vid, uids[w.code], ids.get(w.parent_code), w.code, w.name, types[w.code], w.sequence))
        if types[w.code] == "STAGE":
            r = old_rules.get(uids[w.code])
            rule_rows.append((ids[w.code], project_id, vid, r["weight_pct"] if r else None, Jsonb(r["completion_rule"] if r else {})))
    cur = c.cursor()
    cur.executemany("insert into schedule_wbs (wbs_id, project_id, version_id, wbs_uid, parent_wbs_id, wbs_code, wbs_name, node_type, sequence) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)", wbs_rows)
    if rule_rows:
        cur.executemany("insert into wbs_stage_rules (wbs_id, project_id, version_id, weight_pct, completion_rule) values (%s,%s,%s,%s,%s)", rule_rows)

    # ---- resources (project-level catalogue, reused by code)
    have = {r["resource_code"]: r for r in c.execute("select resource_id, resource_code, resource_class from project_resources where project_id = %s", (project_id,)).fetchall()}
    rid: Dict[str, Any] = {}
    for r in ps.resources:
        if r.code in have:
            rid[r.code] = have[r.code]["resource_id"]
            continue
        sample = next((x for p in prepared.values() for x in p.assignments.values() if x.resource_code == r.code), None)
        if sample is None:
            continue
        row = c.execute("insert into project_resources (project_id, resource_code, resource_name, resource_class, default_uom) values (%s,%s,%s,%s,%s) returning resource_id",
                        (project_id, r.code, r.name, sample.resource_class, sample.uom)).fetchone()
        rid[r.code] = row["resource_id"]

    # ---- identities and rows
    uid_of: Dict[str, Any] = {}
    if rec:
        for it in rec["items"]:
            if it.get("uid"):
                uid_of[it["new"]] = uuid.UUID(it["uid"])
    old_by_uid = {uuid.UUID(o.uid): o for o in old}
    ident_rows, act_rows, new_assign_rows, res_rows = [], [], [], []
    for ext, p in sorted(prepared.items(), key=lambda kv: kv[1].a.sequence):
        a = p.a
        uid = uid_of.get(ext)
        if uid is None:
            uid = uuid.uuid4()
            ident_rows.append((uid, project_id, vid))
        uid_of[ext] = uid
        src = a.discipline_label if (a.discipline_label and p.discipline_how in ("ALIAS", "DECISION", "WBS")) else None
        row_id = uuid.uuid4()
        act_rows.append((row_id, project_id, vid, uid, ext, ids[a.wbs_code], a.name, a.description, p.discipline, src, a.activity_type, a.location,
                         p.duration, a.start, a.finish, a.total_float_days, a.sequence))
        o = old_by_uid.get(uid)
        for code, x in p.assignments.items():
            prev = o.assignments.get(code) if o else None
            if prev:
                auid = uuid.UUID(prev["assignment_uid"])
            else:
                auid = uuid.uuid4()
                new_assign_rows.append((auid, project_id, uid))
            res_rows.append((auid, project_id, vid, row_id, uid, rid[code], x.qty, x.uom, x.measures_progress, x.weight))
    cur.executemany("insert into activities (activity_uid, project_id, first_version_id) values (%s,%s,%s)", ident_rows)
    cur.executemany(
        "insert into baseline_activities (activity_row_id, project_id, version_id, activity_uid, external_activity_id, wbs_id, activity_name, description, "
        "discipline_code, discipline_source, activity_type, location, baseline_duration, baseline_start, baseline_finish, total_float, sequence) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", act_rows)
    cur.executemany("insert into assignments (assignment_uid, project_id, activity_uid) values (%s,%s,%s)", new_assign_rows)
    cur.executemany("insert into baseline_resources (assignment_uid, project_id, version_id, activity_row_id, activity_uid, resource_id, baseline_qty, "
                    "unit_of_measure, measures_progress, progress_weight) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", res_rows)
    n_assign = len(res_rows)

    # ---- dependencies
    seen, dep_rows = set(), []
    for dep in ps.dependencies:
        key = (dep.predecessor, dep.successor, dep.type)
        if key in seen or dep.predecessor not in uid_of or dep.successor not in uid_of:
            continue
        seen.add(key)
        dep_rows.append((project_id, vid, uid_of[dep.predecessor], uid_of[dep.successor], dep.type, dep.lag_days))
    cur.executemany("insert into schedule_dependencies (project_id, version_id, predecessor_uid, successor_uid, relationship_type, lag_days) values (%s,%s,%s,%s,%s,%s)", dep_rows)
    n_dep = len(dep_rows)

    # ---- lineage (what the PM decided), recorded as confirmed
    lin = 0
    def lineage(frm, to, rel, frac=None):
        nonlocal lin
        c.execute("insert into activity_lineage (project_id, version_id, from_activity_uid, to_activity_uid, relation, fraction, confirmed_by, confirmed_at) "
                  "values (%s,%s,%s,%s,%s,%s,%s, now()) on conflict do nothing", (project_id, vid, frm, to, rel, frac, user.id))
        lin += 1
    dec = d.get("reconcile") or {}
    for it in (rec["items"] if rec else []):
        if it["outcome"] == "RENAMED" or (it["outcome"] == "SAME" and (dec.get("accept") or {}).get(it["new"])):
            lineage(uuid.UUID(it["uid"]), uuid.UUID(it["uid"]), "RENAMED")
    for s_ in dec.get("split") or []:
        for t in s_["to"]:
            lineage(uuid.UUID(s_["from"]), uid_of[t["ext"]], "SPLIT", round(float(t["fraction"]), 6))
    for m in dec.get("merge") or []:
        for f in m["from"]:
            lineage(uuid.UUID(f), uid_of[m["to"]], "MERGED")
    for u_ in dec.get("retire") or []:
        lineage(uuid.UUID(u_), None, "RETIRED")
        c.execute("update activities set retired_in_version_id = %s where project_id = %s and activity_uid = %s", (vid, project_id, u_))

    c.execute("update schedule_versions set status = 'VALIDATED' where version_id = %s", (vid,))
    c.execute("update schedule_imports set status = 'BUILT', built_at = now() where import_id = %s", (import_id,))
    return {"version_id": str(vid), "version_no": vno, "kind": kind, "status": "VALIDATED", "baseline_name": name,
            "activities": len(prepared), "assignments": n_assign, "dependencies": n_dep, "wbs_nodes": len(norm.wbs), "lineage_records": lin}


# ------------------------------------------------------------------------------------------------------ activation / rollback / discard
def _lock_project(c, project_id):
    c.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(project_id),))


def activate_version(user, project_id, version_id, reason: Optional[str] = None) -> Dict[str, Any]:
    with tx(user.id) as c:
        _lock_project(c, project_id)
        v = c.execute("select * from schedule_versions where project_id = %s and version_id = %s", (project_id, version_id)).fetchone()
        if v is None:
            raise ApiError(404, "VERSION_NOT_FOUND", "No such schedule version in this project")
        if v["status"] == "ACTIVE":
            raise ApiError(409, "ALREADY_ACTIVE", "This version is already the active schedule")
        if v["status"] == "DRAFT":
            raise ApiError(409, "NOT_VALIDATED", "Only a validated version can be activated")
        rollback = v["status"] == "SUPERSEDED"
        if rollback and not (reason and reason.strip()):
            raise ApiError(422, "REASON_REQUIRED", "Re-activating a superseded version (rollback) needs a reason")
        prev = c.execute("select version_id, version_no from schedule_versions where project_id = %s and status = 'ACTIVE'", (project_id,)).fetchone()
        if prev:
            c.execute("update schedule_versions set status = 'SUPERSEDED' where version_id = %s", (prev["version_id"],))
        if v["locked_at"] is None:
            c.execute("update schedule_versions set locked_at = now(), locked_by = %s where version_id = %s", (user.id, version_id))
        try:
            c.execute("savepoint act")
            c.execute("update schedule_versions set status = 'ACTIVE', activated_at = now(), activated_by = %s where version_id = %s", (user.id, version_id))
        except Exception as exc:                                    # the database gate: approved progress would be orphaned
            c.execute("rollback to savepoint act")
            raise ApiError(409, "ACTIVATION_BLOCKED", str(getattr(getattr(exc, "diag", None), "message_primary", exc))) from exc
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER",
                  action="SCHEDULE_VERSION_ROLLBACK" if rollback else "SCHEDULE_VERSION_ACTIVATED", entity_type="SCHEDULE_VERSION",
                  entity_id=version_id, version_id=version_id,
                  before={"active_version": str(prev["version_id"]) if prev else None}, after={"active_version": str(version_id)},
                  context={"reason": reason} if reason else None)
    return {"version_id": str(version_id), "status": "ACTIVE", "previous_active_version_id": str(prev["version_id"]) if prev else None,
            "rollback": rollback}


def discard_version(user, project_id, version_id) -> None:
    with tx(user.id) as c:
        _lock_project(c, project_id)
        v = c.execute("select status, locked_at, import_id from schedule_versions where project_id = %s and version_id = %s", (project_id, version_id)).fetchone()
        if v is None:
            raise ApiError(404, "VERSION_NOT_FOUND", "No such schedule version in this project")
        if v["locked_at"] is not None or v["status"] not in ("DRAFT", "VALIDATED"):
            raise ApiError(409, "VERSION_LOCKED", "Locked / activated versions are history and cannot be discarded")
        c.execute("delete from schedule_versions where version_id = %s", (version_id,))
        if v["import_id"]:
            c.execute("update schedule_imports set status = 'PARSED', built_at = null where import_id = %s", (v["import_id"],))
        audit.log(c, project_id=project_id, actor_id=user.id, role="PROJECT_MANAGER", action="SCHEDULE_VERSION_DISCARDED",
                  entity_type="SCHEDULE_VERSION", entity_id=version_id)


# ------------------------------------------------------------------------------------------------------ reads
def list_versions(project_id) -> List[Dict[str, Any]]:
    with tx() as c:
        return c.execute(
            "select v.version_id, v.version_no, v.kind, v.status, v.baseline_name, v.label, v.data_date, v.planned_start_date, v.planned_finish_date, "
            "v.locked_at, v.activated_at, v.parent_version_id, v.created_at, "
            "(select count(*) from baseline_activities a where a.version_id = v.version_id) as activities "
            "from schedule_versions v where v.project_id = %s order by v.version_no", (project_id,)).fetchall()


def get_version(project_id, version_id) -> Dict[str, Any]:
    with tx() as c:
        v = c.execute("select * from schedule_versions where project_id = %s and version_id = %s", (project_id, version_id)).fetchone()
        if v is None:
            raise ApiError(404, "VERSION_NOT_FOUND", "No such schedule version in this project")
        counts = c.execute(
            "select (select count(*) from baseline_activities where version_id = %s) activities, (select count(*) from schedule_wbs where version_id = %s) wbs_nodes,"
            " (select count(*) from baseline_resources where version_id = %s) assignments, (select count(*) from schedule_dependencies where version_id = %s) dependencies",
            (version_id,) * 4).fetchone()
        lin = c.execute("select relation, count(*) n from activity_lineage where version_id = %s group by relation", (version_id,)).fetchall()
    return {**v, **counts, "lineage": {r["relation"]: r["n"] for r in lin}}


def version_wbs(project_id, version_id) -> List[Dict[str, Any]]:
    with tx() as c:
        _own_version(c, project_id, version_id)
        return c.execute("select wbs_id, wbs_uid, wbs_code, wbs_name, node_type, level, parent_wbs_id, sequence from schedule_wbs where version_id = %s "
                         "order by wbs_path", (version_id,)).fetchall()


def version_activities(project_id, version_id) -> List[Dict[str, Any]]:
    with tx() as c:
        _own_version(c, project_id, version_id)
        return c.execute(
            "select a.activity_uid, a.external_activity_id, a.activity_name, w.wbs_code, a.discipline_code, a.activity_type, a.baseline_duration, "
            "a.baseline_start, a.baseline_finish, a.total_float, a.is_critical, "
            "coalesce((select json_agg(json_build_object('assignment_uid', r.assignment_uid, 'resource', p.resource_code, 'qty', r.baseline_qty, "
            "'uom', r.unit_of_measure, 'measures_progress', r.measures_progress, 'weight', r.progress_weight) order by p.resource_code) "
            "from baseline_resources r join project_resources p on p.resource_id = r.resource_id where r.activity_row_id = a.activity_row_id), '[]') as assignments "
            "from baseline_activities a join schedule_wbs w on w.wbs_id = a.wbs_id where a.version_id = %s order by a.sequence", (version_id,)).fetchall()


def _own_version(c, project_id, version_id):
    if c.execute("select 1 from schedule_versions where project_id = %s and version_id = %s", (project_id, version_id)).fetchone() is None:
        raise ApiError(404, "VERSION_NOT_FOUND", "No such schedule version in this project")


def compare_versions(project_id, old_id, new_id) -> Dict[str, Any]:
    with tx() as c:
        _own_version(c, project_id, old_id)
        _own_version(c, project_id, new_id)
        acts = c.execute("select * from version_activity_diff(%s,%s)", (old_id, new_id)).fetchall()
        asg = c.execute("select * from version_assignment_diff(%s,%s) where change_kind <> 'UNCHANGED'", (old_id, new_id)).fetchall()
    summary: Dict[str, int] = {}
    for a in acts:
        summary[a["change_kind"]] = summary.get(a["change_kind"], 0) + 1
    return {"old_version_id": str(old_id), "new_version_id": str(new_id), "summary": summary,
            "activities": [a for a in acts if a["change_kind"] != "UNCHANGED"], "assignments": asg}
