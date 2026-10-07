"""Errors never leak internals: no tracebacks, no SQL, no constraint names, no file paths, no secrets."""
import json

from apikit import claim_body, file_claim, url
from v2api import connect


def body_text(r):
    return r.text


def test_an_unexpected_exception_returns_a_generic_500_with_a_request_id_only(kit, client_500, monkeypatch):
    from v2api import Api
    api = Api(client_500)
    from backend.v2.domain import claims

    def boom(*a, **k):
        raise RuntimeError("password=hunter2 /Users/someone/.env connection to db.abc.supabase.co failed")
    monkeypatch.setattr(claims, "list_my_claims", boom)
    r = api.get(url(kit, "/my-claims"), kit.world.se)
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "INTERNAL_ERROR" and err["message"] == "An unexpected error occurred" and list(err["details"]) == ["request_id"]
    for leak in ("hunter2", "/Users", ".env", "supabase", "Traceback", "RuntimeError"):
        assert leak not in r.text


def test_database_rule_violations_do_not_name_tables_or_constraints(kit, api):
    c = file_claim(kit, api, "A2010", 100)
    # a second live correction of one rejected claim is refused by the schema (uq_event_one_correction); the client must see a plain sentence
    api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "REJECT", "justification": "Figures do not match the register"})
    first = api.post(url(kit, f"/claims/{c['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 90, text="fix one"))
    assert first.status_code == 201
    second = api.post(url(kit, f"/claims/{c['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 80, text="fix two"))
    assert second.status_code == 409 and second.json()["error"]["code"] == "ALREADY_CORRECTED"
    for leak in ("uq_", "execution_events", "violates", "constraint", "public."):
        assert leak not in second.text, leak


def test_errors_from_the_database_guards_keep_their_human_message(kit, api):
    from backend.v2.errors import _msg

    class E(Exception):
        class diag:
            message_primary = "illegal claim status transition APPROVED -> REJECTED"
            constraint_name = None
            table_name = None
            column_name = None
    assert _msg(E()) == "illegal claim status transition APPROVED -> REJECTED"

    class F(Exception):
        class diag:
            message_primary = 'duplicate key value violates unique constraint "uq_x"'
            constraint_name = "uq_x"
            table_name = "execution_events"
            column_name = None
    assert "uq_x" not in _msg(F()) and "execution_events" not in _msg(F())


def test_validation_errors_are_structured_and_short(kit, api):
    r = api.post(url(kit, "/claims"), kit.world.se, json={"event_date": 5, "quantities": "many", "unknown": True})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "VALIDATION_ERROR" and all(set(d) == {"field", "problem", "type"} for d in err["details"]) and len(err["details"]) <= 25
    assert "Traceback" not in r.text and "pydantic" not in r.text.lower()


def test_unknown_routes_and_bad_ids_are_plain_client_errors(kit, api):
    assert api.get(url(kit, "/nothing-here"), kit.world.pm).status_code == 404
    r = api.get(url(kit, "/claims/not-a-uuid"), kit.world.sup)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.get("/api/v2/projects/not-a-uuid/dashboard/summary", kit.world.pm).status_code == 422
