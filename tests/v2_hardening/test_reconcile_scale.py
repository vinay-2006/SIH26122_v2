"""Large-schedule safeguards: reconciliation stays fast and safe when thousands of activities are renumbered, and refuses to guess when
there are too many unmatched to compare."""
import random
import time

import pytest

from backend.v2.schedule_import import reconcile as R
from backend.v2.schedule_import.models import PActivity, PDependency, ParsedSchedule, PProject, PWbs
from backend.v2.schedule_import.reconcile import NewActivity, OldActivity, reconcile
from backend.v2.schedule_import.validate import validate
from backend.v2.schedule_import.mapping import RefData
from datetime import date, timedelta

WORDS = ("weld pipe trench backfill excavate concrete foundation pump skid cable tray conduit instrument loop valve flange hydrotest "
         "paint coat insulate scaffold survey grade clear fence culvert crossing river road rail pile cap column beam girder deck "
         "tank shell roof nozzle manway ladder platform stair duct fan filter chiller boiler turbine generator transformer panel").split()
D0 = date(2026, 1, 5)


def acts(n, seed=1, prefix="A", wbs_groups=40):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        name = " ".join(rnd.sample(WORDS, 3)) + f" unit {i}"
        s = D0 + timedelta(days=rnd.randint(0, 300))
        out.append((f"{prefix}{i:05d}", name, f"W{i % wbs_groups}", "PIPING" if i % 2 else "CIVIL", s, s + timedelta(days=rnd.randint(2, 30))))
    return out


def old_list(rows):
    return [OldActivity(uid=f"u{ext}", external_id=ext, name=nm, wbs_code=w, discipline=d, start=s, finish=f, duration=10) for ext, nm, w, d, s, f in rows]


def new_list(rows, prefix):
    return [NewActivity(external_id=prefix + ext[1:], name=nm, wbs_code=w, discipline=d, start=s, finish=f, duration=10) for ext, nm, w, d, s, f in rows]


def test_a_full_renumbering_of_thousands_of_activities_is_fast_and_finds_the_renames():
    rows = acts(3000)
    t0 = time.monotonic()
    r = reconcile(old_list(rows), new_list(rows, "N"))                  # every external id changed, everything else identical
    took = time.monotonic() - t0
    assert took < 10, f"reconciliation took {took:.1f}s"
    assert not r["fuzzy"]["skipped"] and r["fuzzy"]["pairs_scored"] <= 3000 * R.CANDIDATES_PER_ITEM
    outcomes = {i["outcome"] for i in r["items"]}
    assert outcomes <= {"PROPOSED_RENAME", "AMBIGUOUS", "NEW"}
    assert sum(i["outcome"] == "PROPOSED_RENAME" for i in r["items"]) > 2000          # still finds the bulk, each one a proposal needing confirmation
    assert all(i.get("uid") is None for i in r["items"])                              # and none is applied automatically


def test_pairs_scored_grow_linearly_not_quadratically():
    counts = {}
    for n in (500, 1000, 2000):
        rows = acts(n, seed=n)
        counts[n] = reconcile(old_list(rows), new_list(rows, "N"))["fuzzy"]["pairs_scored"]
    assert counts[2000] / counts[500] < 6, counts            # 4x the activities -> about 4x the work (not 16x)


def test_beyond_the_hard_cap_no_fuzzy_matching_is_attempted_and_executed_work_still_blocks(monkeypatch):
    monkeypatch.setattr(R, "MAX_FUZZY_SIDE", 100)
    rows = acts(300)
    old = old_list(rows)
    old[7].has_progress = True
    r = reconcile(old, new_list(rows, "N"))
    assert r["fuzzy"]["skipped"] and r["fuzzy"]["reason"] == "TOO_MANY_UNMATCHED" and r["fuzzy"]["pairs_scored"] == 0
    assert all(i["outcome"] == "NEW" for i in r["items"]) and r["split_proposals"] == [] and r["merge_proposals"] == []
    assert [b["code"] for b in r["blockers"]] == ["UNRESOLVED_PROGRESS"]             # the PM must map the executed activity explicitly
    uid = old[7].uid
    ok = reconcile(old, new_list(rows, "N"), {"accept": {"N00007": uid}})
    assert [i for i in ok["items"] if i["new"] == "N00007"][0]["outcome"] == "RENAMED" and ok["blockers"] == []        # explicit mapping still works


def test_the_time_budget_stops_the_search_and_drops_partial_results(monkeypatch):
    rows = acts(1500)
    clock = iter(range(0, 10_000_000, 1))
    monkeypatch.setattr(R.time, "monotonic", lambda: next(clock) * 10.0)
    r = reconcile(old_list(rows), new_list(rows, "N"), time_budget_s=50)             # the fake clock advances 10 "seconds" per look
    assert r["fuzzy"]["skipped"] and r["fuzzy"]["reason"] == "TIME_BUDGET"
    assert not any(i["outcome"] == "PROPOSED_RENAME" for i in r["items"])            # no half-baked proposals


def test_unchanged_ids_never_touch_the_fuzzy_stage_at_all():
    rows = acts(5000)
    t0 = time.monotonic()
    r = reconcile(old_list(rows), new_list(rows, "A"))
    assert time.monotonic() - t0 < 5 and r["fuzzy"]["pairs_scored"] == 0
    assert r["summary"]["same"] == 5000 and r["blockers"] == []


def test_common_words_do_not_make_everything_a_candidate():
    old = [OldActivity(uid=f"u{i}", external_id=f"A{i}", name=f"Installation of item {i}", wbs_code=None, discipline=None, start=D0, finish=D0, duration=1)
           for i in range(1200)]
    new = [NewActivity(external_id=f"B{i}", name=f"Installation of item {i}", wbs_code=None, discipline=None, start=D0, finish=D0, duration=1) for i in range(1200)]
    r = reconcile(old, new)
    assert r["fuzzy"]["pairs_scored"] <= 1200 * R.CANDIDATES_PER_ITEM


# ------------------------------------------------------------------------------------------------ validation at scale
def big_schedule(n):
    ps = ParsedSchedule(project=PProject(name="Big", planned_start=D0, planned_finish=D0 + timedelta(days=400), data_date=D0))
    ps.wbs.append(PWbs("R", "Root", None))
    for g in range(50):
        ps.wbs.append(PWbs(f"R.{g}", f"Area {g}", "R"))
    for i in range(n):
        ps.activities.append(PActivity(f"A{i}", f"Activity {i}", f"R.{i % 50}", D0 + timedelta(days=i % 300), D0 + timedelta(days=(i % 300) + 5), 5.0, 0.0, discipline_label="Civil Works"))
        if i:
            ps.dependencies.append(PDependency(f"A{i - 1}", f"A{i}"))
    return ps


def test_validating_a_20000_activity_schedule_with_a_long_chain_is_fast():
    ps = big_schedule(20000)
    t0 = time.monotonic()
    r = validate(ps, RefData.builtin())
    assert time.monotonic() - t0 < 10 and r["valid"] and r["stats"]["dependencies"] == 19999


def test_cycle_detection_in_a_large_network_is_fast_and_names_the_loop():
    ps = big_schedule(20000)
    ps.dependencies.append(PDependency("A19999", "A19990"))                           # a 10-activity loop at the end of a 20,000 chain
    t0 = time.monotonic()
    r = validate(ps, RefData.builtin())
    assert time.monotonic() - t0 < 10
    msg = next(e for e in r["errors"] if e["code"] == "DEPENDENCY_CYCLE")["message"]
    assert "A19995" in msg and "A19990" in msg
