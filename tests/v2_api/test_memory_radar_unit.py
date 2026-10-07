"""DB-free: the Lessons Radar is deterministic, explainable, and says nothing it cannot back with a recorded lesson."""
from datetime import date

from backend.v2 import memory_radar as mr

REF = date(2026, 10, 1)


def lesson(i, title, **kw):
    d = {"memory_id": i, "title": title, "root_cause": None, "corrective_action": None, "lessons_learned": "x lessons text", "outcome": None, "narrative": None, "category": "TECHNICAL",
         "discipline": None, "delay_days": None, "project_name": "Other Project", "own_project": False}
    d.update(kw)
    return d


def act(i, name, start, finish, state="NOT_STARTED", stage="Hydrotreater", disc="CIVIL"):
    return {"activity_id": i, "name": name, "stage": stage, "discipline": disc, "state": state, "planned_start": start, "planned_finish": finish}


def test_stemming_joins_word_forms():
    assert len({mr.stem(w) for w in ("piling", "piles", "pile")}) == 1
    assert mr.stem("crossings") == mr.stem("Crossing") and mr.stem("approvals") == "approval"


def test_a_lesson_matches_the_work_it_is_about_and_says_why():
    l = lesson("m1", "Six piles failed integrity testing", corrective_action="Test every pile before cap casting", delay_days=17.0)
    r = mr.relevance(act("A1", "Hydrotreater - bored piling", date(2026, 10, 5), date(2026, 12, 1), disc="CIVIL"), l)
    assert r and r["score"] >= 0.5 and any("shared terms" in x and "pil" in x for x in r["reasons"])
    assert mr.relevance(act("A2", "Pipeline line-fill", date(2026, 10, 5), date(2026, 12, 1)), l) is None            # nothing in common: no lesson is invented


def test_generic_words_and_testing_alone_do_not_match():
    l = lesson("m1", "Six piles failed integrity testing")
    assert mr.relevance(act("A3", "Hydrostatic testing - Spread 1", date(2026, 10, 5), date(2026, 12, 1)), l) is None


def test_discipline_and_typical_category_add_explained_bonuses():
    l = lesson("m2", "Structural steel delivery slipped again", category="MATERIAL_DELIVERY_DELAY", discipline="STRUCTURAL")
    r = mr.relevance(act("A4", "Hydrotreater - structural steel fabrication and erection", date(2026, 10, 5), date(2026, 12, 1), disc="STRUCTURAL"), l)
    assert r and "same discipline" in r["reasons"] and any("typically exposed to material delivery delays" in x for x in r["reasons"])


def test_a_semantic_hint_can_carry_a_match_but_only_above_the_floor():
    l = lesson("m3", "Frac-out during the river pilot hole")
    a = act("A5", "Directional drilling under the Ganga", date(2026, 10, 5), date(2026, 12, 1))
    assert mr.relevance(a, l, sem=0.30) is None
    r = mr.relevance(a, l, sem=0.62)
    assert r and r["score"] >= 0.6 and any("semantic similarity 0.62" in x for x in r["reasons"])


def test_the_radar_windows_orders_and_summarises():
    lessons = [lesson("m1", "Six piles failed integrity testing", corrective_action="Test every pile", delay_days=17.0),
               lesson("m2", "Pile cap rebar congestion delayed casting", corrective_action="Test every pile", delay_days=9.0, own_project=True)]
    acts = [act("P1", "Bored piling", date(2026, 9, 1), date(2026, 11, 1), state="IN_PROGRESS"), act("P2", "Pile caps and foundations", date(2026, 12, 1), date(2027, 1, 1)),
            act("P3", "Bored piling zone B", date(2028, 1, 1), date(2028, 3, 1)), act("P4", "Bored piling zone C", date(2026, 1, 1), date(2026, 2, 1)),
            act("P5", "Bored piling zone D", date(2026, 9, 1), date(2026, 11, 1), state="COMPLETED")]
    out = mr.build(acts, lessons, reference=REF, horizon_days=120)
    ids = [i["activity_id"] for i in out["items"]]
    assert ids == ["P1", "P2"] and out["activities_in_window"] == 2 and out["lessons_considered"] == 2          # P3 is too far ahead, P4 finished before, P5 is complete
    p1 = out["items"][0]
    assert p1["timing"] == "IN_PROGRESS" and p1["expected_delay"] == {"samples": 2, "median_days": 13.0, "max_days": 17.0} and p1["preventive_actions"] == ["Test every pile"]
    assert out["items"][1]["timing"] == "UPCOMING" and out["items"][1]["starts_in_days"] == (date(2026, 12, 1) - REF).days
    assert [l["from_other_project"] for l in p1["lessons"]] == [False, True] or {l["memory_id"] for l in p1["lessons"]} == {"m1", "m2"}
    assert mr.build(acts, lessons, reference=REF, horizon_days=120) == out                                       # deterministic


def test_no_lessons_means_an_empty_radar_not_a_guess():
    out = mr.build([act("A", "Anything", date(2026, 10, 5), date(2026, 11, 1))], [], reference=REF, horizon_days=90)
    assert out["items"] == [] and out["lessons_considered"] == 0 and out["activities_in_window"] == 1
