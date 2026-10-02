"""Reconciliation scenarios: unchanged, renamed, new, removed, split, merged, changed dates, changed quantities, reused ids,
dropped assignments, ambiguity. Pure (no database)."""
from datetime import date

import pytest

from backend.v2.schedule_import.reconcile import NewActivity, OldActivity, reconcile

D = date


def A(uid, ext, name, wbs="W1", disc="PIPING", s=D(2026, 3, 1), f=D(2026, 3, 31), dur=26, res=None, prog=False, prog_asg=()):
    return OldActivity(uid=uid, external_id=ext, name=name, wbs_code=wbs, discipline=disc, start=s, finish=f, duration=dur,
                       assignments={c: dict(assignment_uid=f"as-{uid}-{c}", qty=q, uom=u) for c, (q, u) in (res or {}).items()},
                       has_progress=prog, progress_assignments={f"as-{uid}-{c}" for c in prog_asg})


def N(ext, name, wbs="W1", disc="PIPING", s=D(2026, 3, 1), f=D(2026, 3, 31), dur=26, res=None):
    return NewActivity(external_id=ext, name=name, wbs_code=wbs, discipline=disc, start=s, finish=f, duration=dur,
                       assignments={c: dict(qty=q, uom=u) for c, (q, u) in (res or {}).items()})


def by_new(r):
    return {i["new"]: i for i in r["items"]}


def test_unchanged_activities_keep_identity_automatically():
    old = [A("u1", "A1", "Weld mainline", res={"WELD": (2000, "JOINT")})]
    r = reconcile(old, [N("A1", "Weld mainline", res={"WELD": (2000, "JOINT")})])
    assert by_new(r)["A1"]["outcome"] == "SAME" and by_new(r)["A1"]["uid"] == "u1"
    assert r["blockers"] == [] and r["changes"] == [] and r["summary"]["same"] == 1


def test_new_activity_gets_a_new_identity():
    r = reconcile([A("u1", "A1", "Weld mainline")], [N("A1", "Weld mainline"), N("Z9", "Cathodic protection installation", wbs="W9", disc="ELECTRICAL")])
    assert by_new(r)["Z9"]["outcome"] == "NEW" and by_new(r)["Z9"]["uid"] is None


def test_removed_activity_without_progress_is_informational_with_progress_it_blocks():
    old = [A("u1", "A1", "Weld mainline"), A("u2", "A2", "Backfill trench", prog=False)]
    r = reconcile(old, [N("A1", "Weld mainline")])
    assert [x["external_id"] for x in r["removed"]] == ["A2"] and r["blockers"] == []
    old[1].has_progress = True
    r = reconcile(old, [N("A1", "Weld mainline")])
    assert [b["code"] for b in r["blockers"]] == ["UNRESOLVED_PROGRESS"]


def test_retiring_resolves_the_block_and_keeps_history_flag():
    old = [A("u1", "A1", "Weld mainline"), A("u2", "A2", "Backfill trench", prog=True)]
    r = reconcile(old, [N("A1", "Weld mainline")], {"retire": ["u2"]})
    assert r["blockers"] == [] and r["retired_with_progress"] == ["u2"]


def test_changed_external_id_is_proposed_as_rename_not_applied():
    old = [A("u1", "A1010", "Clear and grade right of way", res={"KM": (24, "KM")})]
    r = reconcile(old, [N("CG-1010", "Clear and grade right of way", res={"KM": (24, "KM")})])
    it = by_new(r)["CG-1010"]
    assert it["outcome"] == "PROPOSED_RENAME" and it["old_uid"] == "u1" and it["uid"] is None and it["score"] >= 0.8
    assert [b["code"] for b in r["blockers"]] == ["UNRESOLVED_MATCH"]
    ok = reconcile(old, [N("CG-1010", "Clear and grade right of way", res={"KM": (24, "KM")})], {"accept": {"CG-1010": "u1"}})
    assert by_new(ok)["CG-1010"]["outcome"] == "RENAMED" and by_new(ok)["CG-1010"]["uid"] == "u1" and ok["blockers"] == []
    assert "external_id" in ok["changes"][0]["fields"]


def test_rejecting_a_rename_makes_it_new_and_orphans_the_old_one():
    old = [A("u1", "A1010", "Clear and grade right of way", prog=True)]
    r = reconcile(old, [N("CG-1010", "Clear and grade right of way")], {"new": ["CG-1010"]})
    assert by_new(r)["CG-1010"]["outcome"] == "NEW"
    assert [b["code"] for b in r["blockers"]] == ["UNRESOLVED_PROGRESS"]


def test_same_id_but_different_activity_is_a_reused_id_that_needs_a_decision():
    old = [A("u1", "A1", "Excavate pump station foundation", wbs="W1", disc="CIVIL")]
    r = reconcile(old, [N("A1", "Hydrostatic test of section three", wbs="W7", disc="PROCESS")])
    assert by_new(r)["A1"]["outcome"] == "ID_REUSED"
    ok = reconcile(old, [N("A1", "Hydrostatic test of section three", wbs="W7", disc="PROCESS")], {"accept": {"A1": "u1"}})
    assert by_new(ok)["A1"]["outcome"] == "SAME"
    forced = reconcile(old, [N("A1", "Hydrostatic test of section three", wbs="W7", disc="PROCESS")], {"new": ["A1"]})
    assert by_new(forced)["A1"]["outcome"] == "NEW"


def test_split_is_proposed_with_fractions_from_quantities():
    old = [A("u1", "A2000", "Pipe stringing", res={"KM": (24, "KM")}, prog=True)]
    new = [N("A2000-1", "Pipe stringing - section 1", res={"KM": (9, "KM")}), N("A2000-2", "Pipe stringing - section 2", res={"KM": (15, "KM")})]
    r = reconcile(old, new)
    assert len(r["split_proposals"]) == 1
    sp = r["split_proposals"][0]
    assert sp["from_uid"] == "u1" and {t["ext"]: t["fraction"] for t in sp["to"]} == {"A2000-1": 0.375, "A2000-2": 0.625}
    assert any(b["code"] == "UNRESOLVED_PROGRESS" for b in r["blockers"])           # nothing is applied until the PM decides


def test_confirmed_split_gives_children_fresh_identities_and_clears_the_block():
    old = [A("u1", "A2000", "Pipe stringing", res={"KM": (24, "KM")}, prog=True)]
    new = [N("A2000-1", "Pipe stringing - section 1", res={"KM": (12, "KM")}), N("A2000-2", "Pipe stringing - section 2", res={"KM": (12, "KM")})]
    dec = {"split": [{"from": "u1", "to": [{"ext": "A2000-1", "fraction": 0.5}, {"ext": "A2000-2", "fraction": 0.5}]}]}
    r = reconcile(old, new, dec)
    assert r["blockers"] == [] and all(by_new(r)[e]["outcome"] == "NEW" and by_new(r)[e]["via"] == "SPLIT" for e in ("A2000-1", "A2000-2"))


def test_split_fractions_must_add_up():
    old = [A("u1", "A2000", "Pipe stringing", prog=True)]
    new = [N("A2000-1", "Pipe stringing - section 1"), N("A2000-2", "Pipe stringing - section 2")]
    r = reconcile(old, new, {"split": [{"from": "u1", "to": [{"ext": "A2000-1", "fraction": 0.5}, {"ext": "A2000-2", "fraction": 0.2}]}]})
    assert [b["code"] for b in r["blockers"]] == ["SPLIT_FRACTIONS"]


def test_no_split_proposed_when_quantities_do_not_add_up():
    old = [A("u1", "A2000", "Pipe stringing", res={"KM": (24, "KM")})]
    new = [N("A2000-1", "Pipe stringing - section 1", res={"KM": (5, "KM")}), N("A2000-2", "Pipe stringing - section 2", res={"KM": (5, "KM")})]
    assert reconcile(old, new)["split_proposals"] == []


def test_merge_is_proposed_and_confirmed_merge_clears_blocks():
    old = [A("u1", "A3000", "Hydrostatic test section 1", wbs="S3", disc="PROCESS", res={"T": (2, "NOS")}, prog=True),
           A("u2", "A3001", "Hydrostatic test section 2", wbs="S3", disc="PROCESS", res={"T": (2, "NOS")}, prog=True)]
    new = [N("A3000M", "Hydrostatic test sections 1 and 2", wbs="S3", disc="PROCESS", res={"T": (4, "NOS")})]
    r = reconcile(old, new)
    assert r["merge_proposals"] == [dict(from_uids=["u1", "u2"], from_external_ids=["A3000", "A3001"], to="A3000M")]
    ok = reconcile(old, new, {"merge": [{"from": ["u1", "u2"], "to": "A3000M"}]})
    assert ok["blockers"] == [] and by_new(ok)["A3000M"]["via"] == "MERGE"


def test_changed_dates_and_quantities_are_reported_per_resource():
    old = [A("u1", "A1020", "Pour foundations", s=D(2026, 2, 16), f=D(2026, 3, 7), res={"C": (480, "M3"), "S": (36, "TONNE")})]
    new = [N("A1020", "Pour foundations", s=D(2026, 2, 23), f=D(2026, 3, 14), res={"C": (600, "M3"), "S": (36, "TONNE"), "M": (10, "MH")})]
    ch = reconcile(old, new)["changes"][0]
    assert "dates" in ch["fields"] and "quantities" in ch["fields"]
    q = {x["resource"]: x for x in ch["quantities"]}
    assert q["C"]["change"] == "QTY_CHANGED" and (q["C"]["old"], q["C"]["new"]) == (480, 600) and q["M"]["change"] == "ADDED" and "S" not in q


def test_dropping_a_resource_that_has_approved_quantity_blocks():
    old = [A("u1", "A1", "Weld", res={"W": (2000, "JOINT"), "MH": (900, "MH")}, prog=True, prog_asg=("W",))]
    r = reconcile(old, [N("A1", "Weld", res={"MH": (900, "MH")})])
    assert [b["code"] for b in r["blockers"]] == ["ASSIGNMENT_DROPPED_WITH_PROGRESS"]
    r2 = reconcile(old, [N("A1", "Weld", res={"W": (2000, "JOINT")})])           # MH has no progress: dropping it is only a change
    assert r2["blockers"] == [] and {x["change"] for x in r2["changes"][0]["quantities"]} == {"REMOVED"}


def test_ambiguous_match_lists_candidates_and_never_auto_merges():
    old = [A("u1", "A5", "Install pump skid east"), A("u2", "A6", "Install pump skid west")]
    r = reconcile(old, [N("X1", "Install pump skid")])
    it = by_new(r)["X1"]
    assert it["outcome"] in ("AMBIGUOUS", "PROPOSED_RENAME", "NEW")
    if it["outcome"] == "AMBIGUOUS":
        assert {c["external_id"] for c in it["candidates"]} <= {"A5", "A6"} and it["uid"] is None
    assert it.get("uid") is None


def test_one_old_activity_can_only_be_claimed_once():
    old = [A("u1", "A1", "Lay pipe")]
    r = reconcile(old, [N("B1", "Lay pipe"), N("B2", "Lay pipe")])
    claimed = [i for i in r["items"] if i.get("uid") == "u1" or i.get("old_uid") == "u1" and i["outcome"] == "PROPOSED_RENAME"]
    assert len([i for i in r["items"] if i["outcome"] == "PROPOSED_RENAME"]) <= 1
    assert not any(i["outcome"] == "SAME" for i in r["items"])


def test_accepting_into_an_already_matched_activity_is_ignored():
    old = [A("u1", "A1", "Lay pipe")]
    r = reconcile(old, [N("A1", "Lay pipe"), N("B1", "Lay pipe")], {"accept": {"B1": "u1"}})
    assert by_new(r)["A1"]["uid"] == "u1" and by_new(r)["B1"]["outcome"] != "RENAMED"


def test_fixture_revision_end_to_end_classification():
    """base vs revised fixture schedules (the same scenarios the API tests run with a database)."""
    from helpers import REF, load
    from backend.v2.schedule_import.mapping import resolve_discipline, resolve_uom, wbs_ancestor_names

    def mk(tag):
        ps, res, out = load(tag, "csv"), None, []
        res = {r.code: r for r in ps.resources}
        for a in ps.activities:
            d, _ = resolve_discipline(a.discipline_label, wbs_ancestor_names(ps, a.wbs_code), REF)
            asg = {x.resource_code: (x.qty, resolve_uom(x.uom_label, res[x.resource_code].resource_class, REF)) for x in ps.assignments if x.activity_external_id == a.external_id}
            out.append((a, d, asg))
        return out
    old = [A("u-" + a.external_id, a.external_id, a.name, a.wbs_code, d, a.start, a.finish, a.duration_days, res=asg) for a, d, asg in mk("nsp")]
    new = [N(a.external_id, a.name, a.wbs_code, d, a.start, a.finish, a.duration_days, res=asg) for a, d, asg in mk("nsp_rev1")]
    r = reconcile(old, new)
    o = by_new(r)
    assert {k: o[k]["outcome"] for k in ("A1000", "A1020", "A2010", "A3010", "A3020", "A3030", "A0100")} == {k: "SAME" for k in ("A1000", "A1020", "A2010", "A3010", "A3020", "A3030", "A0100")}
    assert o["CG-1010"]["outcome"] == "PROPOSED_RENAME" and o["CG-1010"]["old_external_id"] == "A1010"
    assert o["A4000"]["outcome"] == "NEW"
    assert [s["from_external_id"] for s in r["split_proposals"]] == ["A2000"]
    assert [m["to"] for m in r["merge_proposals"]] == ["A3000M"]
    assert "A2030" in {x["external_id"] for x in r["removed"]}
    chg = {c["new"]: c for c in r["changes"]}
    assert "dates" in chg["A1000"]["fields"] and chg["A1020"]["quantities"][0]["new"] == 600
