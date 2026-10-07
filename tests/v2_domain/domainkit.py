"""Test kit for the domain services: a committed project with the NSP schedule imported and ACTIVE (through the real Phase 2 pipeline),
actors resolved from real memberships, and small helpers. Everything here goes through the same code paths production uses."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schedule_import"))
from v2api import build_and_activate, connect  # noqa: E402

from backend.v2.domain import claims, decisions, issues, rollups, timeline  # noqa: E402,F401
from backend.v2.domain.common import resolve_actor  # noqa: E402

TODAY = date.today()


class Kit(SimpleNamespace):
    def uid(self, ext):
        return self.acts[ext]

    def asg(self, ext, resource):
        return self.asgs[(ext, resource)]

    def submit(self, ext="A2010", qty=None, uom="joints", basis="CUMULATIVE", pct=None, days_ago=0, text=None, by=None, **kw):
        quantities = kw.pop("quantities", None)
        if quantities is None:
            quantities = [{"qty": qty, "uom": uom, "basis": basis}] if qty is not None else []
        return claims.submit_claim(by or self.se, event_date=TODAY - timedelta(days=days_ago), raw_text=text or f"Progress on {ext}: {qty if qty is not None else pct}",
                                   activity_uid=self.acts[ext] if ext else None, quantities=quantities, claimed_pct=pct, **kw)

    def approve(self, claim_id, **kw):
        kw.setdefault("action", "APPROVE")
        return decisions.decide(self.sup, claim_id, **kw)

    def pct(self, ext):
        with connect() as c:
            r = c.execute("select physical_pct from activity_progress_as_of((select version_id from schedule_versions where project_id=%s and status='ACTIVE'), current_date) where external_activity_id=%s",
                          (self.project, ext)).fetchone()
        return r["physical_pct"]

    def project_pct(self):
        with connect() as c:
            return c.execute("select physical_pct from project_progress_as_of((select version_id from schedule_versions where project_id=%s and status='ACTIVE'), current_date)", (self.project,)).fetchone()["physical_pct"]

    def ledger(self):
        with connect() as c:
            return (c.execute("select entry_id, assignment_uid, cumulative_qty, incremental_qty, prev_cumulative_qty, prev_entry_id, decision_id, claim_quantity_id from approved_resource_progress order by entry_seq").fetchall(),
                    c.execute("select entry_id, activity_uid, actual_start, actual_finish, reported_pct, prev_entry_id, decision_id from approved_activity_progress order by entry_seq").fetchall())

    def count(self, table, where="true", *params):
        with connect() as c:
            return c.execute(f"select count(*) n from {table} where {where}", params).fetchone()["n"]


@pytest.fixture
def kit(world, api):
    """world = admin, pm, sup, se, se2, outsider, pm2 (project created through the API); NSP baseline built + activated through the PM pipeline"""
    build_and_activate(api, world.pm, world.project, "csv")
    k = Kit(project=world.project, world=world)
    with connect() as c:
        rows = c.execute("select external_activity_id, activity_uid from baseline_activities ba join schedule_versions v on v.version_id = ba.version_id where v.project_id = %s and v.status = 'ACTIVE'", (world.project,)).fetchall()
        k.acts = {r["external_activity_id"]: r["activity_uid"] for r in rows}
        k.asgs = {(r["e"], r["rc"]): r["a"] for r in c.execute(
            "select ba.external_activity_id e, pr.resource_code rc, br.assignment_uid a from baseline_resources br join baseline_activities ba on ba.activity_row_id = br.activity_row_id "
            "join project_resources pr on pr.resource_id = br.resource_id join schedule_versions v on v.version_id = br.version_id where v.project_id = %s and v.status = 'ACTIVE'", (world.project,)).fetchall()}
    k.pm, k.sup, k.se, k.se2 = (resolve_actor(getattr(world, n).id, world.project) for n in ("pm", "sup", "se", "se2"))
    k.outsider = world.outsider
    return k


def make_doc(kit, kind="EVIDENCE", by="se", name=None):
    """a source document row (the upload route arrives with Phase 3B); the database still checks who may upload what"""
    import hashlib, uuid
    u = getattr(kit.world, by)
    with connect() as c:
        c.execute("select set_config('app.system','on',false)")
        return c.execute("insert into source_documents (project_id, kind, file_name, sha256, uploaded_by, is_synthetic) values (%s,%s,%s,%s,%s,true) returning document_id",
                         (kit.project, kind, name or f"{kind.lower()}.txt", hashlib.sha256(uuid.uuid4().bytes).hexdigest(), u.id)).fetchone()["document_id"]


# ------------------------------------------------------------------------------------------------ independent recomputation + consistency
def run_concurrently(fns, timeout=60):
    """run callables at the same instant in threads; -> list of ('ok', value) | ('err', exception) in input order"""
    import threading
    barrier, out = threading.Barrier(len(fns)), [None] * len(fns)

    def work(i, fn):
        barrier.wait()
        try:
            out[i] = ("ok", fn())
        except BaseException as e:                                              # noqa: BLE001
            out[i] = ("err", e)
    ts = [threading.Thread(target=work, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(timeout) for t in ts]
    assert all(o is not None for o in out), "a worker did not finish: possible deadlock"
    return out


def recompute_project_pct(kit):
    """ground truth from the RAW ledgers with plain Python arithmetic (never the SQL rollup): weighted mean of activity percentages"""
    from backend.v2.domain.progress_math import D, Decimal, q3, weighted_pct, weighted_mean
    with connect() as c:
        ver = c.execute("select version_id from schedule_versions where project_id=%s and status='ACTIVE'", (kit.project,)).fetchone()["version_id"]
        acts = c.execute("select ba.activity_uid, ba.activity_type, ba.baseline_duration, (select coalesce(sum(br.baseline_qty),0) from baseline_resources br join units_of_measure u on u.code=br.unit_of_measure and u.dimension='EFFORT' where br.activity_uid=ba.activity_uid and br.version_id=ba.version_id) mh, "
                         "(select count(*) from baseline_resources br2 where br2.activity_uid=ba.activity_uid and br2.version_id=ba.version_id and br2.unit_of_measure in ('MH')) has_mh from baseline_activities ba where ba.version_id=%s", (ver,)).fetchall()
        basis = "MANHOURS" if all(a["has_mh"] > 0 for a in acts if a["activity_type"] != "MILESTONE") else ("DURATION" if all(a["baseline_duration"] > 0 for a in acts if a["activity_type"] != "MILESTONE") else "UNIT")
        pairs = []
        for a in acts:
            meas = c.execute("select br.assignment_uid, br.baseline_qty, br.progress_weight from baseline_resources br where br.version_id=%s and br.activity_uid=%s and br.measures_progress", (ver, a["activity_uid"])).fetchall()
            head = c.execute("select actual_finish, reported_pct from approved_activity_progress where activity_uid=%s order by entry_seq desc limit 1", (a["activity_uid"],)).fetchone()
            if meas:
                items = []
                for m in meas:
                    h = c.execute("select cumulative_qty from approved_resource_progress where assignment_uid=%s order by entry_seq desc limit 1", (m["assignment_uid"],)).fetchone()
                    items.append((m["progress_weight"], h["cumulative_qty"] if h else D(0), m["baseline_qty"]))
                pct = weighted_pct(items)
            elif a["activity_type"] == "MILESTONE":
                pct = D(100) if (head and head["actual_finish"]) else D(0)
            else:
                pct = D(head["reported_pct"]) if head and head["reported_pct"] is not None else D(0)
            w = {"MANHOURS": a["mh"], "DURATION": a["baseline_duration"], "UNIT": 1}[basis] if a["activity_type"] != "MILESTONE" or basis == "UNIT" else 0
            if basis == "DURATION" and a["activity_type"] == "MILESTONE":
                w = a["baseline_duration"]
            pairs.append((D(w), pct))
    return weighted_mean(pairs)


def assert_consistent(kit):
    """every invariant of the approved ledgers, checked from raw rows"""
    with connect() as c:
        for tbl, key in (("approved_resource_progress", "assignment_uid"), ("approved_activity_progress", "activity_uid")):
            rows = c.execute(f"select * from {tbl} order by {key}, entry_seq").fetchall()
            by = {}
            for r in rows:
                by.setdefault(r[key], []).append(r)
            for k, chain in by.items():
                assert chain[0]["prev_entry_id"] is None, f"{tbl}: chain of {k} has no single root"
                for prev, cur in zip(chain, chain[1:]):
                    assert cur["prev_entry_id"] == prev["entry_id"], f"{tbl}: chain of {k} forks or skips"
                if tbl == "approved_resource_progress":
                    assert sum(r["incremental_qty"] for r in chain) == chain[-1]["cumulative_qty"], f"increments of {k} do not add up to the cumulative (double counting)"
                    for prev, cur in zip(chain, chain[1:]):
                        assert cur["prev_cumulative_qty"] == prev["cumulative_qty"]
                        if cur.get("supersedes_entry_id") is None:          # an ordinary entry never lowers the cumulative or goes back in time
                            assert cur["cumulative_qty"] >= prev["cumulative_qty"] and cur["as_of_date"] >= prev["as_of_date"]
                        else:                                                # a governed-reopen correction supersedes exactly its predecessor, which stays on record
                            assert cur["supersedes_entry_id"] == prev["entry_id"]
        for d in c.execute("select decision_id, action, event_id from planner_decisions").fetchall():
            assert c.execute("select count(*) n from notifications where decision_id=%s", (d["decision_id"],)).fetchone()["n"] == 1
            assert c.execute("select count(*) n from audit_logs where entity_type='PLANNER_DECISION' and entity_id=%s", (str(d["decision_id"]),)).fetchone()["n"] == 1
            n_led = c.execute("select (select count(*) from approved_resource_progress where decision_id=%s) + (select count(*) from approved_activity_progress where decision_id=%s) n", (d["decision_id"], d["decision_id"])).fetchone()["n"]
            assert (n_led > 0) == (d["action"] in ("APPROVE", "EDIT")), "ledger rows exist exactly for approvals"
        assert c.execute("select count(*) n from (select event_id from planner_decisions where action in ('APPROVE','EDIT','REJECT') group by event_id having count(*) > 1) x").fetchone()["n"] == 0
        # a claim that is not APPROVED has no ledger row at all
        assert c.execute("select count(*) n from approved_resource_progress r join planner_decisions d on d.decision_id = r.decision_id join execution_events e on e.event_id = d.event_id where e.status <> 'APPROVED'").fetchone()["n"] == 0
        from backend.v2.audit import verify_chain
        assert verify_chain(c)["valid"]
    from backend.v2.domain.progress_math import D
    assert recompute_project_pct(kit) == kit.project_pct(), "SQL rollup disagrees with an independent recomputation from the raw ledger"
