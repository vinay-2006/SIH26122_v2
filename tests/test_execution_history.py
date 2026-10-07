"""Unit tests (DB-free) for the execution-history generator: realistic, chronological, monotonic, activity-specific, deterministic."""
import re
from collections import Counter
from datetime import timedelta

import pytest

from backend.prototype_seed import data, history as H


def _history(p, a):
    st, fi, day = data.execution_window(p, a)
    issues = [H.IssueWindow(i.reported, i.resolved, i.title, i.blocks_work) for i in p.issues if i.activity == a.code]
    final_day, final_pct, edit = day, int(a.pct), next((c for c in p.claims if c.activity == a.code and c.action == "EDIT"), None)
    if edit:
        final_day = max(st, edit.day)
    claims = H.build_history(project_code=p.code, activity_code=a.code, name=a.name, description=a.description, discipline=a.discipline,
                             location=a.location, qty=a.qty, uom=a.uom, start=st, final_day=final_day, final_pct=final_pct,
                             data_date=p.data_date, issues=issues)
    return st, final_day, final_pct, claims


ALL = [(p, a) for p in data.PROJECTS for s in p.stages for a in s.acts]
STARTED = [(p, a) for p, a in ALL if a.pct > 0]


@pytest.mark.parametrize("p,a", STARTED, ids=[a.code for _, a in STARTED])
def test_every_started_activity_has_a_consistent_history(p, a):
    st, final_day, final_pct, claims = _history(p, a)
    assert H.validate_history(claims, final_pct, final_day, st) == []
    acc = [c for c in claims if c.accepted]
    assert acc[-1].pct == final_pct and acc[-1].day == final_day, "the latest claim IS the existing current state"
    assert all(c.day <= p.data_date for c in claims)
    assert all(c.pct <= 100 for c in claims)
    # supervisor decisions are made in claim order, never before the claim, never after the next claim
    for i, c in enumerate(claims[:-1]):
        assert c.decision_lag >= 0 and c.day + timedelta(days=c.decision_lag) < claims[i + 1].day + timedelta(days=1)


def test_unstarted_activities_get_no_history():
    for p, a in ALL:
        if a.pct == 0:
            assert H.build_history(project_code=p.code, activity_code=a.code, name=a.name, description=a.description, discipline=a.discipline,
                                   location=a.location, qty=a.qty, uom=a.uom, start=a.start, final_day=a.start, final_pct=0, data_date=p.data_date) == []
    upcoming = next(p for p in data.PROJECTS if p.lifecycle == "UPCOMING")
    assert all(a.pct == 0 for s in upcoming.stages for a in s.acts)


def test_histories_are_not_one_template():
    counts, paths, texts = Counter(), set(), []
    for p, a in STARTED:
        _, _, _, claims = _history(p, a)
        acc = [c for c in claims if c.accepted]
        counts[len(acc)] += 1
        paths.add(tuple(c.pct for c in acc[:-1]))
        texts.extend(c.text for c in claims)
    assert len(counts) >= 6, f"claim counts should vary widely: {sorted(counts.items())}"
    assert max(counts) - min(counts) >= 6
    assert len(paths) > len(STARTED) * 0.9, "progress paths are activity-specific, not a shared sequence"
    assert not {(30, 50, 75), (25, 50, 75)} & {tuple(sorted(x))[:3] for x in paths}
    # the defect being fixed: a bare one-clause "<Activity> completed" / "<Activity> N percent complete" / "Progress update N"
    generic = [t for t in texts if re.fullmatch(r"[^.;:,]{0,100}( completed| \d+ percent complete|Progress update \d+)\.?", t)]
    assert not generic, generic[:3]
    assert len(set(texts)) > len(texts) * 0.9


def test_longer_work_gets_more_updates_than_short_work():
    long_, short = [], []
    for p, a in STARTED:
        st, fd, _, claims = _history(p, a)
        (long_ if (fd - st).days >= 120 else short if (fd - st).days <= 30 else []).append(len([c for c in claims if c.accepted]))
    assert sum(long_) / len(long_) > sum(short) / len(short) + 2


def test_claim_text_matches_the_work_it_describes():
    by_code = {a.code: (p, a) for p, a in STARTED}
    def text_of(code):
        p, a = by_code[code]
        return " ".join(c.text.lower() for c in _history(p, a)[3])
    assert "excavat" in text_of("NNB-PLC-020") and "chainage" in text_of("NNB-PLC-020")
    assert "coat" in text_of("NNB-PLC-040") and "weld" not in text_of("NNB-PLC-040")
    assert "weld" in text_of("NNB-PLC-030")
    assert "authority" in text_of("NNB-SUR-040") or "approval" in text_of("NNB-SUR-040")
    assert "tonnes" in text_of("NRL-PRC-030") and any(w in text_of("NRL-PRC-030") for w in ("vendor", "dispatch", "lot", "inspection"))
    assert "pull-back" in text_of("NNB-PLC-060") or "pilot" in text_of("NNB-PLC-060")
    assert "lift" in text_of("NRL-ERC-020") or "erect" in text_of("NRL-ERC-020")


def test_issue_story_shows_up_in_the_history():
    p = next(x for x in data.PROJECTS if x.code == "NRL-EXP-01")
    a = next(a for s in p.stages for a in s.acts if a.code == "NRL-ERC-010")      # steel delivery issue still open
    text = " ".join(c.text for c in _history(p, a)[3])
    assert "held back" in text and "steel" in text.lower()
    a = next(a for s in p.stages for a in s.acts if a.code == "NRL-CIV-060")      # shuttering shortage, resolved
    claims = _history(p, a)[3]
    issue = next(i for i in p.issues if i.activity == "NRL-CIV-060")
    assert not [c for c in claims if issue.reported < c.day < issue.resolved and c is not claims[-1]], "no reporting inside the stoppage"


def test_generation_is_deterministic():
    for p, a in STARTED[:40]:
        one, two = _history(p, a)[3], _history(p, a)[3]
        assert [(c.day, c.pct, c.text, c.source) for c in one] == [(c.day, c.pct, c.text, c.source) for c in two]


def test_a_few_claims_were_adjusted_or_rejected_and_never_break_monotonicity():
    actions = Counter(c.action for p, a in STARTED for c in _history(p, a)[3])
    assert actions["APPROVE"] > 400 and 10 <= actions["EDIT"] and 10 <= actions["REJECT"]
    for p, a in STARTED:
        claims = _history(p, a)[3]
        for c in claims:
            if c.action == "EDIT":
                assert c.claimed > c.pct, "an EDIT means the supervisor approved less than reported"
