"""An activity with several measured quantities has a baseline for each. The original "no planned quantity" warning came from a read model that exposes one planned quantity
only for single-measure activities; a claim whose unit binds to a measured quantity must not be told the baseline is missing."""
import pytest

pytestmark = pytest.mark.db_write
RULE = "VAL_UNSUPPORTED_ACCUMULATION"


def submit(lg, kit, text):
    r = lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": text, "input_channel": "TYPED_TEXT"})
    assert r.status_code == 200, r.text
    return r.json()


def check(lg, kit, text):
    ev = submit(lg, kit, text)
    k = lg.post(f"/api/v1/claims/{ev['event_id']}/check", kit.world.se)
    assert k.status_code == 200, k.text
    return ev, k.json()


def test_a_bound_unit_on_a_multi_measure_activity_is_not_told_the_plan_is_missing(kit, lg):
    # A1020 measures concrete (m3) and steel (tonne): two baselines, no single planned quantity in the read model
    ev, body = check(lg, kit, "Pour pump station foundations A1020: 10 m3 concrete poured today")
    assert ev["matched_activity_id"] == "A1020", ev
    assert RULE not in [i["rule_code"] for i in body["validation_issues"]], body["validation_issues"]


def test_an_unbound_unit_keeps_the_warning_because_nothing_could_be_compared(kit, lg):
    ev, body = check(lg, kit, "Pour pump station foundations A1020: 7 joints poured today")
    assert ev["matched_activity_id"] == "A1020", ev
    assert RULE in [i["rule_code"] for i in body["validation_issues"]], body["validation_issues"]


def test_a_single_measure_activity_is_unchanged(kit, lg):
    # A2010 measures only WELD_JOINTS, so the read model carries its planned quantity and the original rule works as before
    _, body = check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    assert RULE not in [i["rule_code"] for i in body["validation_issues"]]
