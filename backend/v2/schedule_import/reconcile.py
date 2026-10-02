"""Schedule reconciliation: decide which activity in a NEW file is the same real-world activity as one in the previous version, so
its stable `activity_uid` (and therefore its approved progress) carries over.

Pure functions. Nothing is merged on a fuzzy match without the PM confirming it; only an identical external id with a compatible name is
automatic. Outcomes per new activity: SAME | ID_REUSED (same id, clearly different activity) | PROPOSED_RENAME | AMBIGUOUS | NEW.
Old activities that disappear: REMOVED (no approved progress) or UNRESOLVED_PROGRESS (must be retired / split / merged by decision).
A SPLIT or MERGE keeps the old progress on the OLD identity; it is never silently transferred. See `decisions` format below.

decisions = {
  "accept": {new_external_id: old_activity_uid},          # confirm a rename / reused-id as the same activity
  "new":    [new_external_id, ...],                         # force a brand-new identity (reject a proposal)
  "retire": [old_activity_uid, ...],                        # out of scope now; history kept
  "split":  [{"from": old_uid, "to": [{"ext": new_id, "fraction": 0.5}, ...]}],
  "merge":  [{"from": [old_uid, ...], "to": new_id}],
}
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

AUTO_NAME_FLOOR = 0.35         # same external id but names this dissimilar => treated as a REUSED id, needs confirmation
PROPOSE_AT = 0.80
AMBIGUOUS_AT = 0.55
QTY_TOLERANCE = 0.10
MAX_FUZZY_SIDE = 4000          # more unmatched activities than this on either side: no fuzzy matching (exact ids + explicit decisions only)
CANDIDATES_PER_ITEM = 30       # fuzzy scoring looks at the best few candidates per activity, found through an inverted index
MAX_POSTING = 300              # a word / WBS shared by more activities than this carries no signal and is not indexed
DEFAULT_TIME_BUDGET_S = 15.0
_STOP = {"and", "the", "for", "with", "from", "into", "all", "per"}


class _OutOfBudget(Exception):
    pass


def _sig_words(name: str) -> set:
    return {w for w in _words(name) if len(w) > 2 and w not in _STOP}


class _Index:
    """inverted index so that fuzzy scoring never compares every old activity with every new one"""

    def __init__(self, items):
        self.items = items
        self.by_word, self.by_wbs = defaultdict(list), defaultdict(list)
        for i, it in enumerate(items):
            for w in _sig_words(it.name):
                self.by_word[w].append(i)
            if it.wbs_code:
                self.by_wbs[it.wbs_code].append(i)

    def candidates(self, probe, k=CANDIDATES_PER_ITEM):
        hits: Counter = Counter()
        for w in _sig_words(probe.name):
            post = self.by_word.get(w, ())
            if len(post) <= MAX_POSTING:
                for i in post:
                    hits[i] += 2
        post = self.by_wbs.get(probe.wbs_code, ()) if probe.wbs_code else ()
        if len(post) <= MAX_POSTING:
            for i in post:
                hits[i] += 1
        return [self.items[i] for i, _ in hits.most_common(k)]


@dataclass
class OldActivity:
    uid: str
    external_id: str
    name: str
    wbs_code: Optional[str]
    discipline: Optional[str]
    start: Optional[date]
    finish: Optional[date]
    duration: Optional[float]
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # resource_code -> {assignment_uid, qty, uom}
    has_progress: bool = False
    progress_assignments: set = field(default_factory=set)                 # assignment_uids with approved quantities


@dataclass
class NewActivity:
    external_id: str
    name: str
    wbs_code: Optional[str]
    discipline: Optional[str]
    start: Optional[date]
    finish: Optional[date]
    duration: Optional[float]
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # resource_code -> {qty, uom}


def _tokens(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _words(s: str) -> set:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _tokens(s).split()}


def name_similarity(a: str, b: str) -> float:
    """Word-overlap based (character-level fuzziness alone rates unrelated names ~0.4). A name that CONTAINS the other's words
    (e.g. 'Pipe stringing' vs 'Pipe stringing - section 1') scores high; two names sharing no words score low."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return 0.0
    inter = len(x & y)
    jac = inter / len(x | y)
    sort = fuzz.token_sort_ratio(_tokens(a), _tokens(b)) / 100.0
    contained = 0.9 * inter / min(len(x), len(y)) if inter else 0.0
    return round(max(0.5 * jac + 0.5 * sort * (1 if inter else 0.5), contained), 4)


def _overlap(a0, a1, b0, b1) -> float:
    if not (a0 and a1 and b0 and b1):
        return 0.0
    lo, hi = max(a0, b0), min(a1, b1)
    inter = max(0, (hi - lo).days + 1)
    union = (max(a1, b1) - min(a0, b0)).days + 1
    return inter / union if union > 0 else 0.0


def _qty_similarity(o: OldActivity, n: NewActivity) -> float:
    common = [c for c in o.assignments if c in n.assignments]
    if not o.assignments and not n.assignments:
        return 1.0
    if not common:
        return 0.0
    ok = 0
    for c in common:
        qo, qn = o.assignments[c].get("qty"), n.assignments[c].get("qty")
        if qo and qn and abs(qo - qn) <= QTY_TOLERANCE * max(qo, qn):
            ok += 1
    return (ok / len(common)) * (len(common) / max(len(o.assignments), len(n.assignments)))


def score(o: OldActivity, n: NewActivity) -> float:
    nm = name_similarity(o.name, n.name)
    wbs = 1.0 if (o.wbs_code and o.wbs_code == n.wbs_code) else 0.0
    disc = 1.0 if (o.discipline and o.discipline == n.discipline) else 0.0
    dt = _overlap(o.start, o.finish, n.start, n.finish)
    return round(0.50 * nm + 0.15 * wbs + 0.10 * disc + 0.15 * dt + 0.10 * _qty_similarity(o, n), 4)


def _primary(a) -> Optional[Tuple[str, str, float]]:
    """largest non-effort quantity as (resource_code, uom, qty) for split/merge arithmetic"""
    best = None
    for c, v in a.assignments.items():
        if v.get("uom") in (None, "MH", "HR"):
            continue
        if v.get("qty") and (best is None or v["qty"] > best[2]):
            best = (c, v["uom"], v["qty"])
    return best


def reconcile(old: List[OldActivity], new: List[NewActivity], decisions: Optional[Dict[str, Any]] = None,
              time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> Dict[str, Any]:
    d = decisions or {}
    deadline = time.monotonic() + time_budget_s
    fuzzy: Dict[str, Any] = {"skipped": False, "reason": None, "pairs_scored": 0}
    accept, force_new = dict(d.get("accept") or {}), set(d.get("new") or [])
    old_by_ext = {o.external_id: o for o in old}
    old_by_uid = {o.uid: o for o in old}

    status: Dict[str, Dict[str, Any]] = {}      # new external id -> item
    taken_old: set = set()

    # 1. exact external id
    for n in new:
        o = old_by_ext.get(n.external_id)
        if o is None or n.external_id in force_new:
            continue
        sim = name_similarity(o.name, n.name)
        if sim >= AUTO_NAME_FLOOR or accept.get(n.external_id) == o.uid:
            status[n.external_id] = dict(new=n.external_id, outcome="SAME", uid=o.uid, old_external_id=o.external_id, score=round(sim, 4))
            taken_old.add(o.uid)
        else:
            status[n.external_id] = dict(new=n.external_id, outcome="ID_REUSED", uid=None, old_uid=o.uid, old_external_id=o.external_id,
                                         score=round(sim, 4), message=f"{n.external_id} existed as '{o.name}' but is now '{n.name}': same activity or a reused id?")

    # 2. confirmed renames / explicit accepts
    for n in new:
        if n.external_id in status and status[n.external_id]["outcome"] == "SAME":
            continue
        uid = accept.get(n.external_id)
        if uid and uid in old_by_uid and uid not in taken_old:
            o = old_by_uid[uid]
            status[n.external_id] = dict(new=n.external_id, outcome="RENAMED", uid=uid, old_external_id=o.external_id, score=score(o, n), confirmed=True)
            taken_old.add(uid)

    # 3. decided splits / merges (fresh identities for the new side; old identity keeps its history)
    split_from, merge_from, split_to, merge_to = {}, {}, set(), set()
    for s in d.get("split") or []:
        split_from[s["from"]] = s
        taken_old.add(s["from"])
        split_to.update(t["ext"] for t in s["to"])
    for m in d.get("merge") or []:
        for f in m["from"]:
            merge_from[f] = m
            taken_old.add(f)
        merge_to.add(m["to"])

    # 4. high-confidence unique rename proposals among the rest (candidate pairs come from the inverted index, never from all pairs)
    pending_new = [n for n in new if n.external_id not in status and n.external_id not in force_new
                   and n.external_id not in split_to and n.external_id not in merge_to]
    free_old = [o for o in old if o.uid not in taken_old]
    if len(free_old) > MAX_FUZZY_SIDE or len(pending_new) > MAX_FUZZY_SIDE:
        fuzzy.update(skipped=True, reason="TOO_MANY_UNMATCHED", old_unmatched=len(free_old), new_unmatched=len(pending_new))
    pairs: List[Tuple[float, OldActivity, NewActivity]] = []
    if not fuzzy["skipped"]:
        try:
            idx = _Index(pending_new)
            for i, o in enumerate(free_old):
                if i % 100 == 0 and time.monotonic() > deadline:
                    raise _OutOfBudget()
                for n in idx.candidates(o):
                    pairs.append((score(o, n), o, n))
                    fuzzy["pairs_scored"] += 1
        except _OutOfBudget:
            pairs = []                                            # a partial search would give unfair proposals: drop fuzzy entirely
            fuzzy.update(skipped=True, reason="TIME_BUDGET")
    pairs.sort(key=lambda t: -t[0])
    used_old, used_new = set(), set()
    cands: Dict[str, List[Tuple[float, OldActivity]]] = {}
    for sc, o, n in pairs:
        if sc >= AMBIGUOUS_AT:
            cands.setdefault(n.external_id, []).append((sc, o))
    scores_by_old: Dict[str, List[float]] = defaultdict(list)
    for sc, o, n in pairs:
        scores_by_old[o.uid].append(sc)
    for sc, o, n in pairs:
        if sc < PROPOSE_AT or o.uid in used_old or n.external_id in used_new:
            continue
        floor = max(AMBIGUOUS_AT, sc - 0.08)
        rival_olds = [c for c in cands.get(n.external_id, []) if c[1].uid != o.uid and c[0] >= sc - 0.08]       # another old activity fits this new one
        rival_news = sum(1 for x in scores_by_old[o.uid] if x >= floor) > 1                                    # this old one fits several new ones (a split?)
        if rival_olds or rival_news:
            continue                                              # a rename must be unambiguous on both sides
        status[n.external_id] = dict(new=n.external_id, outcome="PROPOSED_RENAME", uid=None, old_uid=o.uid, old_external_id=o.external_id, score=sc,
                                     message=f"'{n.name}' ({n.external_id}) looks like '{o.name}' ({o.external_id}): confirm to keep its progress history")
        used_old.add(o.uid); used_new.add(n.external_id)

    # 5. split / merge PROPOSALS among what is still unmatched (same bounded candidate search)
    left_old = [o for o in free_old if o.uid not in used_old]
    left_new = [n for n in pending_new if n.external_id not in used_new]
    splits, merges = [], []
    if not fuzzy["skipped"]:
        new_idx, old_idx = _Index(left_new), _Index(left_old)
        for o in left_old:
            po = _primary(o)
            kids = [n for n in new_idx.candidates(o) if name_similarity(o.name, n.name) >= 0.55 and (o.wbs_code == n.wbs_code or o.discipline == n.discipline)]
            if po and len(kids) >= 2:
                ks = [(n, (n.assignments.get(po[0]) or {}).get("qty")) for n in kids]
                ks = [(n, q) for n, q in ks if q]
                tot = sum(q for _, q in ks)
                if len(ks) >= 2 and abs(tot - po[2]) <= QTY_TOLERANCE * po[2]:
                    splits.append(dict(from_uid=o.uid, from_external_id=o.external_id, resource=po[0], uom=po[1],
                                       to=[dict(ext=n.external_id, fraction=round(q / tot, 6)) for n, q in ks]))
        for n in left_new:
            pn = _primary(n)
            parents = [o for o in old_idx.candidates(n) if name_similarity(o.name, n.name) >= 0.55 and (o.wbs_code == n.wbs_code or o.discipline == n.discipline)]
            if pn and len(parents) >= 2:
                ps_ = [(o, (o.assignments.get(pn[0]) or {}).get("qty")) for o in parents]
                ps_ = [(o, q) for o, q in ps_ if q]
                if len(ps_) >= 2 and abs(sum(q for _, q in ps_) - pn[2]) <= QTY_TOLERANCE * pn[2]:
                    merges.append(dict(from_uids=[o.uid for o, _ in ps_], from_external_ids=[o.external_id for o, _ in ps_], to=n.external_id))

    # 6. ambiguous / new
    proposed_new = {s["to"] for s in merges} | {t["ext"] for s in splits for t in s["to"]}
    for n in new:
        if n.external_id in status:
            continue
        if n.external_id in force_new or n.external_id in split_to or n.external_id in merge_to:
            status[n.external_id] = dict(new=n.external_id, outcome="NEW", uid=None,
                                         via="SPLIT" if n.external_id in split_to else "MERGE" if n.external_id in merge_to else "DECISION")
            continue
        cs = sorted(cands.get(n.external_id, []), key=lambda t: -t[0])
        if n.external_id in proposed_new:
            status[n.external_id] = dict(new=n.external_id, outcome="AMBIGUOUS", uid=None, via="SPLIT_OR_MERGE_PROPOSAL", candidates=[],
                                         message="part of a proposed split/merge; confirm it or mark as new")
        elif cs:
            status[n.external_id] = dict(new=n.external_id, outcome="AMBIGUOUS", uid=None,
                                         candidates=[dict(uid=o.uid, external_id=o.external_id, name=o.name, score=sc) for sc, o in cs[:3]],
                                         message=f"{n.external_id} may be one of {len(cs)} earlier activities")
        else:
            status[n.external_id] = dict(new=n.external_id, outcome="NEW", uid=None)

    matched_uids = {v["uid"] for v in status.values() if v.get("uid")}
    proposal_old = ({v["old_uid"] for v in status.values() if v.get("old_uid")} | {s_["from_uid"] for s_ in splits}
                    | {u for m_ in merges for u in m_["from_uids"]}
                    | {c_["uid"] for v in status.values() for c_ in v.get("candidates", [])})
    # a PROPOSED/AMBIGUOUS candidate is "claimed" for removal purposes only once accepted, so old ones stay open until then
    removed = []
    for o in old:
        if o.uid in matched_uids or o.uid in (d.get("retire") or []) or o.uid in split_from or o.uid in merge_from:
            continue
        removed.append(dict(uid=o.uid, external_id=o.external_id, name=o.name, has_progress=o.has_progress,
                            pending_proposal=o.uid in proposal_old))

    # changes on matched activities + assignment diffs
    newmap = {n.external_id: n for n in new}
    changes, assign_issues = [], []
    for ext, v in status.items():
        if not v.get("uid"):
            continue
        o, n = old_by_uid[v["uid"]], newmap[ext]
        fields = []
        if o.name != n.name: fields.append("name")
        if o.start != n.start or o.finish != n.finish: fields.append("dates")
        if o.duration is not None and n.duration is not None and abs(o.duration - n.duration) > 1e-6: fields.append("duration")
        if o.discipline != n.discipline: fields.append("discipline")
        if o.wbs_code != n.wbs_code: fields.append("wbs")
        if o.external_id != n.external_id: fields.append("external_id")
        qty = []
        for c, ov in o.assignments.items():
            nv = n.assignments.get(c)
            if nv is None:
                if ov["assignment_uid"] in o.progress_assignments:
                    assign_issues.append(dict(new=ext, resource=c, code="ASSIGNMENT_DROPPED_WITH_PROGRESS",
                                              message=f"{ext}: resource {c} has approved quantities but is missing from the new file"))
                else:
                    qty.append(dict(resource=c, change="REMOVED", old=ov["qty"]))
            elif ov["qty"] != nv["qty"] or ov["uom"] != nv["uom"]:
                qty.append(dict(resource=c, change="QTY_CHANGED", old=ov["qty"], new=nv["qty"], old_uom=ov["uom"], new_uom=nv["uom"]))
        for c, nv in n.assignments.items():
            if c not in o.assignments:
                qty.append(dict(resource=c, change="ADDED", new=nv["qty"]))
        if qty: fields.append("quantities")
        if fields:
            changes.append(dict(new=ext, uid=o.uid, old_external_id=o.external_id, fields=fields, quantities=qty))

    # blockers (what must be decided before the draft can be built)
    blockers: List[Dict[str, Any]] = []
    for v in status.values():
        if v["outcome"] in ("ID_REUSED", "PROPOSED_RENAME", "AMBIGUOUS"):
            blockers.append(dict(code="UNRESOLVED_MATCH", ref=v["new"], message=v.get("message", "needs a decision")))
    for r in removed:
        if r["has_progress"]:
            blockers.append(dict(code="UNRESOLVED_PROGRESS", ref=r["external_id"], uid=r["uid"],
                                 message=f"{r['external_id']} '{r['name']}' has approved progress but is not in the new file: retire, split, merge or match it"))
    blockers.extend(dict(code=i["code"], ref=i["new"], message=i["message"]) for i in assign_issues)
    for s in d.get("split") or []:
        tot = sum(t["fraction"] for t in s["to"])
        if abs(tot - 1) > 0.001:
            blockers.append(dict(code="SPLIT_FRACTIONS", ref=s["from"], message=f"split fractions add up to {tot:g}, not 1"))
    retired_with_progress = [u for u in (d.get("retire") or []) if u in old_by_uid and old_by_uid[u].has_progress]
    return dict(items=list(status.values()), removed=removed, changes=changes, split_proposals=splits, merge_proposals=merges,
                blockers=blockers, retired_with_progress=retired_with_progress, fuzzy=fuzzy,
                summary=dict(same=sum(v["outcome"] == "SAME" for v in status.values()),
                             renamed=sum(v["outcome"] in ("RENAMED", "PROPOSED_RENAME") for v in status.values()),
                             new=sum(v["outcome"] == "NEW" for v in status.values()), removed=len(removed), changed=len(changes),
                             needs_decision=len(blockers)))
