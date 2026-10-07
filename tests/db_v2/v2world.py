"""Shared builders for the clean-schema tests. Everything runs inside one transaction that the fixture rolls back."""
from __future__ import annotations

import os
import re
import uuid
from urllib.parse import urlparse
from datetime import date, timedelta
from types import SimpleNamespace

import psycopg
import pytest

TODAY = date.today()


def server_base() -> str:
    """'postgresql://user@host:port/' of the server under test (from DATABASE_URL), so scratch-database tests follow it to any server."""
    u = urlparse(os.environ.get("DATABASE_URL") or "")
    return f"{u.scheme}://{u.netloc}/" if u.netloc else "postgresql://postgres@127.0.0.1:54329/"


def u() -> uuid.UUID:
    return uuid.uuid4()


def sysmode(conn):
    conn.execute("select set_config('app.system','on',true), set_config('app.actor_id','',true)")


def as_actor(conn, uid):
    conn.execute("select set_config('app.system','off',true), set_config('app.actor_id',%s,true)", (str(uid),))


def fails(conn, sql, params=None, match=None, code=None):
    """Statement must fail; the failure is rolled back to a savepoint so the test transaction stays usable."""
    with pytest.raises(psycopg.Error) as ei:
        with conn.transaction():
            conn.execute(sql, params)
    if match:
        assert re.search(match, str(ei.value), re.I | re.S), f"{match!r} not in: {ei.value}"
    if code:
        assert ei.value.sqlstate == code, f"sqlstate {ei.value.sqlstate} != {code}: {ei.value}"
    return ei.value


def one(conn, sql, params=None):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else (list(row.values())[0] if len(row) == 1 else row)


def make_user(conn, name, email=None):
    uid = u()
    conn.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s,%s,%s::jsonb)",
                 (uid, email or f"{name}.{uid.hex[:6]}@test.local", f'{{"full_name":"{name}"}}'))
    return uid


def make_member(conn, project, user, role, by=None):
    conn.execute("insert into project_memberships (project_id,user_id,role,added_by) values (%s,%s,%s,%s)",
                 (project, user, role, by))


def make_resource(conn, project, code, cls, uom):
    rid = u()
    conn.execute("insert into project_resources (resource_id,project_id,resource_code,resource_name,resource_class,default_uom) "
                 "values (%s,%s,%s,%s,%s,%s)", (rid, project, code, code.title(), cls, uom))
    return rid


def add_activity(conn, w, version, ext_id, name, wbs, disc, start_off, dur, assigns, atype="TASK", flt=None):
    """assigns: list of (resource_id, qty, uom, measures_progress, weight). Returns SimpleNamespace(uid,row,assign=[uids])."""
    uid, row = u(), u()
    conn.execute("insert into activities (activity_uid,project_id,first_version_id) values (%s,%s,%s)", (uid, w.project, version))
    start = TODAY + timedelta(days=start_off)
    conn.execute(
        "insert into baseline_activities (activity_row_id,project_id,version_id,activity_uid,external_activity_id,wbs_id,activity_name,"
        "discipline_code,activity_type,baseline_duration,baseline_start,baseline_finish,total_float) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row, w.project, version, uid, ext_id, wbs, name, disc, atype, dur, start, start + timedelta(days=int(dur)), flt))
    a = SimpleNamespace(uid=uid, row=row, assign=[], start=start)
    for rid, qty, uom, meas, wt in assigns:
        auid = u()
        conn.execute("insert into assignments (assignment_uid,project_id,activity_uid) values (%s,%s,%s)", (auid, w.project, uid))
        conn.execute("insert into baseline_resources (assignment_uid,project_id,version_id,activity_row_id,activity_uid,resource_id,"
                     "baseline_qty,unit_of_measure,measures_progress,progress_weight) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (auid, w.project, version, row, uid, rid, qty, uom, meas, wt))
        a.assign.append(auid)
    return a


def activate(conn, w, version):
    conn.execute("update schedule_versions set status='VALIDATED' where version_id=%s", (version,))
    conn.execute("update schedule_versions set locked_at=now(), locked_by=%s where version_id=%s", (w.pm, version))
    conn.execute("update schedule_versions set status='ACTIVE', activated_at=now(), activated_by=%s where version_id=%s", (w.pm, version))


def build_world(conn, a3_manhours=True, do_activate=True):
    """One project with PM / SE / SUPERVISOR, a 2-stage WBS, three activities, resources. Setup runs in system mode."""
    sysmode(conn)
    w = SimpleNamespace()
    w.admin, w.pm, w.se, w.sup, w.pm2, w.outsider = (make_user(conn, n) for n in ("admin", "pm", "se", "sup", "pm2", "outsider"))
    w.project, w.project_b = u(), u()
    conn.execute("insert into platform_grants (user_id,capability) values (%s,'PLATFORM_ADMIN'),(%s,'CREATE_PROJECT')", (w.admin, w.pm))
    tag = uuid.uuid4().hex[:6].upper()
    for pid, code, pm in ((w.project, f"TST-A{tag}", w.pm), (w.project_b, f"TST-B{tag}", w.pm2)):
        conn.execute("insert into projects (project_id,project_code,project_name,created_by,lifecycle_status) values (%s,%s,%s,%s,'ONGOING')",
                     (pid, code, f"Test project {code}", pm))
    make_member(conn, w.project, w.pm, "PROJECT_MANAGER")
    make_member(conn, w.project, w.se, "SITE_ENGINEER", w.pm)
    make_member(conn, w.project, w.sup, "SUPERVISOR", w.pm)
    make_member(conn, w.project_b, w.pm2, "PROJECT_MANAGER")
    w.v1 = u()
    conn.execute("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
                 "planned_finish_date,created_by) values (%s,%s,1,'BASELINE','Baseline Rev 0',%s,%s,%s,%s)",
                 (w.v1, w.project, TODAY - timedelta(days=120), TODAY - timedelta(days=110), TODAY + timedelta(days=200), w.pm))
    def wbs(code, name, parent, ntype):
        wid = u()
        conn.execute("insert into schedule_wbs (wbs_id,project_id,version_id,parent_wbs_id,wbs_code,wbs_name,node_type) "
                     "values (%s,%s,%s,%s,%s,%s,%s)", (wid, w.project, w.v1, parent, code, name, ntype))
        return wid
    w.root = wbs("ROOT", "Project", None, "PROJECT")
    w.s1 = wbs("S1", "Stage 1", w.root, "STAGE")
    w.s1a = wbs("S1.A", "Area A", w.s1, "AREA")
    w.s2 = wbs("S2", "Stage 2", w.root, "STAGE")
    w.s2a = wbs("S2.A", "Area B", w.s2, "AREA")
    w.r_km = make_resource(conn, w.project, "PIPELINE_KM", "MATERIAL", "KM")
    w.r_joint = make_resource(conn, w.project, "WELD_JOINTS", "MATERIAL", "JOINT")
    w.r_m3 = make_resource(conn, w.project, "CONCRETE_M3", "MATERIAL", "M3")
    w.r_mh = make_resource(conn, w.project, "MANHOURS", "LABOR", "MH")
    w.a1 = add_activity(conn, w, w.v1, "A1000", "Trench and lay pipeline", w.s1a, "PIPING", -100, 40,
                        [(w.r_km, 10, "KM", True, 0.6), (w.r_joint, 200, "JOINT", True, 0.4), (w.r_mh, 5000, "MH", False, 1)], flt=0)
    w.a2 = add_activity(conn, w, w.v1, "A1010", "Concrete foundations", w.s1a, "CIVIL", -60, 20,
                        [(w.r_m3, 500, "M3", True, 1), (w.r_mh, 2000, "MH", False, 1)], flt=5)
    w.a3 = add_activity(conn, w, w.v1, "A2000", "Obtain statutory clearance", w.s2a, "HSE", -30, 10,
                        [(w.r_mh, 100, "MH", False, 1)] if a3_manhours else [], flt=2)
    w.sdoc = 0
    if do_activate:
        activate(conn, w, w.v1)
    return w


def doc(conn, w, kind="DAILY_REPORT", by=None):
    did = u()
    conn.execute("insert into source_documents (document_id,project_id,kind,file_name,sha256,uploaded_by) values (%s,%s,%s,%s,%s,%s)",
                 (did, w.project, kind, f"{kind}.txt", uuid.uuid4().hex + uuid.uuid4().hex, by or w.se))
    return did


def claim(conn, w, activity, status="MATCHED", created_at=None, event_date=None, by=None, version=None):
    eid = u()
    conn.execute(
        "insert into execution_events (event_id,project_id,filed_in_version_id,event_date,raw_claim_text,input_channel,filed_by,"
        "matched_activity_uid,status,created_at) values (%s,%s,%s,%s,%s,'TYPED',%s,%s,%s,coalesce(%s, now()))",
        (eid, w.project, version or w.v1, event_date or TODAY, "Work progressed as reported", by or w.se, activity.uid, status, created_at))
    return eid


def decide(conn, w, event, activity, action="APPROVE", ack=False, note=None, decided_at=None, by=None, method=None):
    """Insert a supervisor decision. `method` defaults to how progress would be derived for that action (NONE for REJECT / HOLD)."""
    did = u()
    method = method or ("QUANTITIES_AS_CLAIMED" if action in ("APPROVE", "EDIT") else "NONE")
    conn.execute(
        "insert into planner_decisions (decision_id,project_id,event_id,selected_activity_uid,action,method,justification,decided_by,"
        "decided_at,overrun_ack,overrun_ack_note) values (%s,%s,%s,%s,%s,%s,'Verified against measurement book',%s,coalesce(%s, now()),%s,%s)",
        (did, w.project, event, activity.uid, action, method, by or w.sup, decided_at, ack, note))
    return did


def approve_qty(conn, w, activity, idx, cum, as_of=None, ack_by=None, ack_note=None, supersedes=None, incremental=None, event=None):
    """Derived columns (prev/incremental/baseline at entry) are computed by the database; pass `incremental` only to test the guard."""
    ev = event or claim(conn, w, activity)
    dec = decide(conn, w, ev, activity, ack=bool(ack_by), note=ack_note)
    eid = u()
    cols = ["entry_id", "project_id", "activity_uid", "assignment_uid", "decision_id", "as_of_date", "cumulative_qty",
            "overrun_ack_by", "overrun_ack_note", "supersedes_entry_id"]
    vals = [eid, w.project, activity.uid, activity.assign[idx], dec, as_of or TODAY, cum, ack_by, ack_note, supersedes]
    if incremental is not None:
        cols.append("incremental_qty"); vals.append(incremental)
    conn.execute(f"insert into approved_resource_progress ({','.join(cols)}) values ({','.join(['%s'] * len(cols))})", vals)
    return eid, dec


def approve_activity(conn, w, activity, start=None, finish=None, pct=None, as_of=None, supersedes=None):
    ev = claim(conn, w, activity)
    dec = decide(conn, w, ev, activity)
    eid = u()
    conn.execute("insert into approved_activity_progress (entry_id,project_id,activity_uid,decision_id,as_of_date,actual_start,"
                 "actual_finish,reported_pct,supersedes_entry_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (eid, w.project, activity.uid, dec, as_of or TODAY, start, finish, pct, supersedes))
    return eid, dec
