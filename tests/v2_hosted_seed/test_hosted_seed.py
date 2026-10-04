"""Hosted demo seed: the parts that need neither a network nor a database (Auth Admin API client, preflight, runner swap, static guarantees)."""
import io
import json
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from backend.v2.seed import hosted, runner
from backend.v2.seed.projects_spec import DOMAIN, PEOPLE

REF = "abcdefghijklmnopqrst"
OLD = "zyxwvutsrqponmlkjihg"
KEY = "service-key-should-never-appear"
PW = "a-long-demo-password"
URL = f"postgresql://postgres.{REF}:db-pw-should-never-appear@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require"
ENV = {"DB_V2_URL": URL, "V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": REF, "V2_DENIED_PROJECT_REFS": OLD,
       "SUPABASE_URL": f"https://{REF}.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": KEY, "V2_HOSTED_DEMO_PASSWORD": PW,
       "V2_EVIDENCE_DIR": str(Path(tempfile.gettempdir()) / "setuai-hosted-evidence-test")}


class FakeAuth:
    """an in-memory stand-in for the Auth Admin API, used as the urllib opener"""
    def __init__(self, users=None, fail=None):
        self.users = list(users or [])
        self.calls = []
        self.fail = fail

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, self.fail, "x", {}, io.BytesIO(b"secret body"))
        if req.get_method() == "GET":
            body = {"users": self.users}
        else:
            d = json.loads(req.data)
            assert d["email_confirm"] is True and d["password"] == PW
            u = {"id": str(uuid.uuid4()), "email": d["email"]}
            self.users.append(u)
            body = u
        resp = io.BytesIO(json.dumps(body).encode())
        resp.__enter__ = lambda s=resp: s
        resp.__exit__ = lambda *a: False
        return resp


def client(fake):
    return hosted.AdminClient(f"https://{REF}.supabase.co", KEY, opener=fake)


def test_users_are_created_through_the_admin_api_once_and_repeating_is_safe():
    fake = FakeAuth()
    logs = []
    ids = hosted.ensure_auth_users(client(fake), PW, logs.append)
    assert set(ids) == set(PEOPLE) and len(set(ids.values())) == len(PEOPLE)
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == len(PEOPLE) and all(p[1].endswith("/auth/v1/admin/users") for p in posts)
    again = hosted.ensure_auth_users(client(fake), PW, logs.append)            # second run: nothing new, same ids
    assert again == ids and len([c for c in fake.calls if c[0] == "POST"]) == len(PEOPLE)
    assert "0 created" in logs[-1]


def test_a_partly_created_set_is_completed_not_duplicated():
    fake = FakeAuth(users=[{"id": str(uuid.uuid4()), "email": f"anita.bora@{DOMAIN}"}, {"id": str(uuid.uuid4()), "email": f"ritu.baruah@{DOMAIN}"}])
    ids = hosted.ensure_auth_users(client(fake), PW, lambda m: None)
    assert len(ids) == len(PEOPLE) and len([c for c in fake.calls if c[0] == "POST"]) == len(PEOPLE) - 2


def test_foreign_users_and_weak_passwords_are_refused_before_anything_is_created():
    fake = FakeAuth(users=[{"id": str(uuid.uuid4()), "email": "someone@example.com"}])
    with pytest.raises(hosted.HostedSeedError, match="outside the demo domain"):
        hosted.ensure_auth_users(client(fake), PW, lambda m: None)
    assert not [c for c in fake.calls if c[0] == "POST"]
    with pytest.raises(hosted.HostedSeedError, match="at least"):
        hosted.ensure_auth_users(client(FakeAuth()), "short", lambda m: None)


def test_secrets_never_appear_in_errors_logs_or_repr():
    fake = FakeAuth(fail=401)
    with pytest.raises(hosted.HostedSeedError) as e:
        client(fake).list_users()
    assert KEY not in str(e.value) and "secret body" not in str(e.value) and "401" in str(e.value)
    assert KEY not in repr(client(fake))
    logs = []
    hosted.ensure_auth_users(client(FakeAuth()), PW, logs.append)
    assert PW not in " ".join(logs) and KEY not in " ".join(logs)


def test_preflight_accepts_a_consistent_environment_and_reports_the_plan():
    info = hosted.preflight(dict(ENV))
    assert info["project_ref"] == REF and info["users"] == 15 and info["grants"] == 3 and len(info["projects"]) == 4
    assert PW not in json.dumps(info) and KEY not in json.dumps(info)


@pytest.mark.parametrize("change,match", [
    ({"SUPABASE_URL": f"https://{OLD}.supabase.co"}, "SAME project"),
    ({"SUPABASE_URL": f"http://{REF}.supabase.co"}, "SAME project"),
    ({"SUPABASE_SERVICE_ROLE_KEY": ""}, "SERVICE_ROLE_KEY"),
    ({"V2_HOSTED_DEMO_PASSWORD": "short"}, "PASSWORD"),
    ({"V2_ALLOW_HOSTED": "0"}, "refusing database target"),
    ({"V2_ALLOWED_PROJECT_REFS": OLD}, "refusing database target"),
    ({"V2_DENIED_PROJECT_REFS": REF}, "refusing database target"),
    ({"DB_V2_URL": URL + "&host=evil.example"}, "refusing database target"),
    ({"DB_V2_URL": "postgresql://postgres@127.0.0.1:54329/setuai_v2_dev"}, "only runs against a hosted"),
    ({"DB_V2_URL": ""}, "DB_V2_URL"),
])
def test_preflight_refuses_inconsistent_or_unsafe_environments(change, match):
    with pytest.raises(hosted.HostedSeedError, match=match) as e:
        hosted.preflight({**ENV, **change})
    assert KEY not in str(e.value) and PW not in str(e.value) and "db-pw-should-never-appear" not in str(e.value)


def test_the_runner_swap_is_temporary_and_the_local_seed_keeps_its_guard():
    orig_lt, orig_uid = runner.local_target, runner.user_id
    t = hosted.tg.check_target(URL, env=ENV)
    uid = str(uuid.uuid4())
    with hosted._HostedRunner(t, {"anita.bora": uid}):
        assert runner.local_target() is t and str(runner.user_id("anita.bora")) == uid
    assert runner.local_target is orig_lt and runner.user_id is orig_uid
    assert runner.user_id("anita.bora") == orig_uid("anita.bora")
    # the local seed still refuses a hosted target (the opt-in variables are not set here)
    import os
    saved = {k: os.environ.pop(k, None) for k in ("V2_ALLOW_HOSTED",)}
    os.environ["DB_V2_URL"] = URL
    try:
        with pytest.raises(runner.SeedError):
            runner.local_target()
    finally:
        os.environ.pop("DB_V2_URL", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_static_guarantees_no_direct_auth_users_writes_and_local_seed_untouched():
    src = Path(hosted.__file__).read_text().lower()
    assert "insert into auth.users" not in src and "update auth.users" not in src and "delete from auth.users" not in src
    assert "truncate" not in src and "drop " not in src and "delete from" not in src          # nothing is ever deleted or reset on a hosted database
    assert "service_role" not in Path(hosted.__file__).read_text().replace("SERVICE_ROLE_KEY", "")   # no hardcoded key material


# ------------------------------------------------------------------------------------------------ evidence directory isolation
LOCAL_DEFAULT = hosted.LOCAL_EVIDENCE_DEFAULT


def test_the_evidence_directory_must_be_set_explicitly():
    env = {k: v for k, v in ENV.items() if k != "V2_EVIDENCE_DIR"}
    for bad in (None, "", "   "):
        e = dict(env) if bad is None else {**env, "V2_EVIDENCE_DIR": bad}
        with pytest.raises(hosted.HostedSeedError, match="V2_EVIDENCE_DIR must be set"):
            hosted.preflight(e)


def test_the_local_seeds_default_directory_and_anything_overlapping_it_is_refused(tmp_path):
    repo_root = LOCAL_DEFAULT.parents[1]
    for bad in (LOCAL_DEFAULT,                                   # the default itself
                LOCAL_DEFAULT / "some-project-id",               # inside it (the local reset empties these per project id)
                LOCAL_DEFAULT.parent,                            # its parent: .local
                repo_root,                                       # an ancestor of it
                str(LOCAL_DEFAULT) + "/../v2_evidence",          # the same place spelled differently
                "~/../.." + str(LOCAL_DEFAULT)):                 # via ~ and ..
        with pytest.raises(hosted.HostedSeedError, match="overlaps the local seed"):
            hosted.preflight({**ENV, "V2_EVIDENCE_DIR": str(bad)})


def test_a_symlink_into_the_local_directory_is_refused(tmp_path):
    LOCAL_DEFAULT.parent.mkdir(parents=True, exist_ok=True)
    link = tmp_path / "looks-dedicated"
    link.symlink_to(LOCAL_DEFAULT.parent, target_is_directory=True)
    with pytest.raises(hosted.HostedSeedError, match="overlaps"):
        hosted.preflight({**ENV, "V2_EVIDENCE_DIR": str(link)})


def test_a_dedicated_hosted_directory_is_accepted_and_not_created_by_the_preflight(tmp_path):
    d = tmp_path / "hosted-evidence"
    info = hosted.preflight({**ENV, "V2_EVIDENCE_DIR": str(d)})
    assert info["evidence_dir"] == str(d.resolve()) and not d.exists()
    d.mkdir()
    assert hosted.preflight({**ENV, "V2_EVIDENCE_DIR": str(d)})["evidence_dir"] == str(d.resolve())
    f = tmp_path / "a-file"; f.write_text("x")
    with pytest.raises(hosted.HostedSeedError, match="not a directory"):
        hosted.preflight({**ENV, "V2_EVIDENCE_DIR": str(f)})


# ------------------------------------------------------------------------------------------------ failure paths
def test_a_failing_seed_closes_the_pool_restores_the_runner_and_the_evidence_store(monkeypatch, tmp_path):
    from backend.v2 import storage
    events = []
    monkeypatch.setattr(hosted, "get_pool", lambda: events.append("open"))
    monkeypatch.setattr(hosted, "close_pool", lambda: events.append("close"))
    def boom(ctx):
        raise RuntimeError("seed step failed")
    monkeypatch.setattr(runner, "create_baseline", boom)
    orig_lt, orig_uid, orig_store = runner.local_target, runner.user_id, storage._store
    t = hosted.tg.check_target(URL, env=ENV)
    ids = {h: str(uuid.uuid4()) for h in PEOPLE}
    with pytest.raises(RuntimeError, match="seed step failed"):
        hosted._seed_projects(t, ids, tmp_path / "ev", lambda m: None)
    assert events == ["open", "close"]
    assert runner.local_target is orig_lt and runner.user_id is orig_uid and storage._store is orig_store
    assert not (tmp_path / "ev").exists() or not any((tmp_path / "ev").iterdir())          # nothing was written


def test_run_closes_the_pool_even_when_the_preflight_refuses(monkeypatch):
    events = []
    monkeypatch.setattr(hosted, "close_pool", lambda: events.append("close"))
    with pytest.raises(hosted.HostedSeedError):
        hosted.run({**ENV, "V2_EVIDENCE_DIR": ""})
    assert events == ["close"]


# ---------------------------------------------------------------------------------------------------------------- renaming the demo accounts
def test_rename_plan_renames_old_addresses_skips_new_ones_and_refuses_strangers():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rename_mod", Path(__file__).resolve().parents[2] / "scripts" / "rename_demo_accounts_hosted.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    people = ["a.one", "b.two", "c.three"]
    users = [{"id": "1", "email": "a.one@seed.setuai.local"}, {"id": "2", "email": "b.two@anvyra.demo"}, {"id": "3", "email": "c.three@seed.setuai.local"}]
    assert mod.plan(users, people) == [("a.one", "RENAME", "1"), ("b.two", "ALREADY_RENAMED", "2"), ("c.three", "RENAME", "3")]
    with pytest.raises(hosted.HostedSeedError):
        mod.plan(users + [{"id": "9", "email": "stranger@example.com"}], people)
    assert mod.plan(users[:1], ["a.one", "z.zed"])[1] == ("z.zed", "MISSING", None)


def test_update_user_email_changes_only_the_email():
    calls = []

    class Opener:
        def __call__(self, req, timeout=None):
            import io
            calls.append((req.get_method(), req.full_url.split("/auth/v1")[1], json.loads(req.data)))
            r = io.BytesIO(b"{}"); r.__enter__ = lambda s=r: s; r.__exit__ = lambda *a: False
            return r

    hosted.AdminClient("https://abc.supabase.co", "key", opener=Opener()).update_user_email("uid-1", "x@anvyra.demo")
    assert calls == [("PUT", "/admin/users/uid-1", {"email": "x@anvyra.demo", "email_confirm": True})]
