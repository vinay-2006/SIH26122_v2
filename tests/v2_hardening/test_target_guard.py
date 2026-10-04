"""Which database may be touched: the policy for local and hosted targets, and the on-database fingerprint check. Pure (no connection)."""
import pytest

from db import target_guard as tg

REF = "abcdefghijklmnopqrst"          # a made-up 20-char project ref
OLD = "zyxwvutsrqponmlkjihg"
ENV = {"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": REF, "V2_DENIED_PROJECT_REFS": OLD}
NOOLD = dict(old_host_fn=lambda: None, old_refs_fn=lambda: set())
DIRECT = f"postgresql://postgres:s3cr3t-pw@db.{REF}.supabase.co:5432/postgres?sslmode=require"
POOLER = f"postgresql://postgres.{REF}:s3cr3t-pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require"


def refuses(url, match, env=ENV, **kw):
    with pytest.raises(tg.GuardError, match=match) as e:
        tg.check_target(url, env=env, **{**NOOLD, **kw})
    assert "s3cr3t-pw" not in str(e.value) and url not in str(e.value)          # an error never repeats the URL or password
    return e.value


# ------------------------------------------------------------------------------------------------ local
def test_local_databases_need_no_opt_in_but_must_be_named_for_v2():
    for host in ("127.0.0.1", "localhost"):
        t = tg.check_target(f"postgresql://postgres@{host}:54329/setuai_v2_integ", env={}, **NOOLD)
        assert (t.kind, t.dbname, t.ref) == ("local", "setuai_v2_integ", None)
    refuses("postgresql://postgres@127.0.0.1:54329/setuai_integ_demo", "must start with", env={})
    refuses("postgresql://postgres@127.0.0.1:54329/postgres", "must start with", env={})
    refuses("postgresql://postgres@127.0.0.1:54329/", "host and database", env={})


def test_other_hosts_are_refused():
    refuses("postgresql://u:s3cr3t-pw@10.0.0.5:5432/setuai_v2_x", "not loopback and not a Supabase host", env=ENV)
    refuses("postgresql://u:s3cr3t-pw@mydb.abc.rds.amazonaws.com:5432/setuai_v2_x", "not loopback and not a Supabase host", env=ENV)
    refuses("postgresql://u:s3cr3t-pw@supabase.co.evil.example/postgres", "not loopback and not a Supabase host", env=ENV)


def test_the_old_shared_host_is_refused_even_if_it_were_local():
    refuses("postgresql://postgres@127.0.0.1:54329/setuai_v2_integ", "old shared database host", env={}, old_host_fn=lambda: "127.0.0.1")


# ------------------------------------------------------------------------------------------------ hosted
def test_hosted_needs_every_condition():
    t = tg.check_target(DIRECT, env=ENV, **NOOLD)
    assert (t.kind, t.ref, t.sslmode) == ("supabase", REF, "require")
    assert tg.check_target(POOLER, env=ENV, **NOOLD).ref == REF                       # pooler user 'postgres.<ref>'
    refuses(DIRECT, "V2_ALLOW_HOSTED=1", env={"V2_ALLOWED_PROJECT_REFS": REF})
    refuses(DIRECT, "V2_ALLOW_HOSTED=1", env={"V2_ALLOW_HOSTED": "true", "V2_ALLOWED_PROJECT_REFS": REF})     # exactly "1"
    refuses(DIRECT, "not in V2_ALLOWED_PROJECT_REFS", env={"V2_ALLOW_HOSTED": "1"})
    refuses(DIRECT, "not in V2_ALLOWED_PROJECT_REFS", env={"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": OLD})
    refuses(DIRECT.replace("?sslmode=require", ""), "sslmode")
    refuses(DIRECT.replace("sslmode=require", "sslmode=disable"), "sslmode")
    refuses(DIRECT.replace("sslmode=require", "sslmode=prefer"), "sslmode")
    for ok in ("require", "verify-ca", "verify-full"):
        assert tg.check_target(DIRECT.replace("require", ok), env=ENV, **NOOLD).sslmode == ok


def test_the_allowlist_is_a_list_and_matches_exactly():
    env = {"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": f" {OLD} , {REF.upper()} ", "V2_DENIED_PROJECT_REFS": "none"}
    assert tg.check_target(DIRECT, env=env, **NOOLD).ref == REF                       # whitespace / case tolerated
    refuses(DIRECT.replace(REF, REF[:-1] + "x"), "not in V2_ALLOWED_PROJECT_REFS", env=env)      # near-miss ref
    refuses(DIRECT.replace(REF, REF + "x").replace("db." + REF + "x", "db." + REF + "x"), "cannot determine|not in V2", env=env)


def test_the_ref_cannot_be_spoofed_through_the_host_or_the_user():
    refuses(f"postgresql://postgres:s3cr3t-pw@db.{REF}.supabase.co.evil.example/postgres?sslmode=require", "not loopback and not a Supabase host")
    refuses(f"postgresql://postgres.{REF}:s3cr3t-pw@evil.example/postgres?sslmode=require", "not loopback and not a Supabase host")
    refuses("postgresql://postgres:s3cr3t-pw@aws-0-x.pooler.supabase.com:6543/postgres?sslmode=require", "cannot determine the Supabase project ref")   # no ref in the pooler user
    refuses(f"postgresql://anything:s3cr3t-pw@db.other.supabase.co/postgres?sslmode=require", "cannot determine the Supabase project ref")


def test_the_legacy_shared_project_can_never_be_allowed():
    env = {"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": f"{REF},{OLD}"}
    old_url = DIRECT.replace(REF, OLD)
    refuses(old_url, "OLD shared Supabase project", env=env, old_refs_fn=lambda: {OLD})
    refuses(DIRECT, "OLD shared Supabase project", env=env, old_refs_fn=lambda: {REF})
    refuses(POOLER.replace(REF, OLD), "OLD shared Supabase project", env=env, old_refs_fn=lambda: {OLD})
    refuses(old_url, "old shared database host", env=env, old_host_fn=lambda: f"db.{OLD}.supabase.co")


def test_the_explicit_deny_list_refuses_a_project_even_if_it_is_allow_listed():
    env = {"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": f"{REF},{OLD}", "V2_DENIED_PROJECT_REFS": f" {OLD.upper()} "}
    refuses(DIRECT.replace(REF, OLD), "OLD shared Supabase project", env=env)
    refuses(POOLER.replace(REF, OLD), "OLD shared Supabase project", env=env)
    assert tg.check_target(DIRECT, env=env, **NOOLD).ref == REF                       # the new project is unaffected


def test_a_hosted_target_needs_the_deny_list_to_be_stated_when_no_legacy_env_is_known():
    env = {"V2_ALLOW_HOSTED": "1", "V2_ALLOWED_PROJECT_REFS": REF}
    refuses(DIRECT, "V2_DENIED_PROJECT_REFS", env=env)                                # nothing known to protect and nothing stated: fail closed
    assert tg.check_target(DIRECT, env={**env, "V2_DENIED_PROJECT_REFS": "none"}, **NOOLD).ref == REF
    assert tg.check_target(DIRECT, env=env, old_host_fn=lambda: None, old_refs_fn=lambda: {OLD}).ref == REF     # a known legacy .env also satisfies it
    t = tg.check_target("postgresql://postgres@127.0.0.1:54329/setuai_v2_integ", env={}, **NOOLD)                  # local targets are not affected
    assert t.kind == "local"


def test_unexpected_url_parameters_are_refused_before_anything_else():
    for q in ("host=evil.example", "hostaddr=10.0.0.5", "HOST=evil.example", "service=x", "options=-csearch_path%3Dx", "dbname=postgres", "port=1", "user=x", "password=x"):
        refuses(DIRECT + "&" + q, "unexpected connection parameter")
        refuses(f"postgresql://postgres@127.0.0.1:54329/setuai_v2_integ?{q}", "unexpected connection parameter", env={})
    refuses(DIRECT + "&sslmode=require", "repeated")
    refuses(f"postgresql://postgres@a.example,127.0.0.1:5432/setuai_v2_integ", "multi-host", env={})
    ok = tg.check_target(DIRECT + "&connect_timeout=10&application_name=setuai", env=ENV, **NOOLD)
    assert ok.ref == REF
    assert tg.check_target("postgresql://postgres@127.0.0.1:54329/setuai_v2_integ?connect_timeout=5", env={}, **NOOLD).kind == "local"


def test_the_legacy_env_is_found_through_a_git_worktree(tmp_path, monkeypatch):
    main = tmp_path / "main"; (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "wt"; wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n")
    (main / ".env").write_text(f"SUPABASE_URL=https://{OLD}.supabase.co\n")
    monkeypatch.setattr(tg, "ROOT", wt)
    assert tg.old_project_refs() == {OLD}
    (wt / ".env").write_text(f"SUPABASE_URL=https://{REF}.supabase.co\n")          # the worktree's own .env wins for a key it defines
    assert tg.old_project_refs() == {REF}


def test_legacy_refs_are_read_from_the_repo_env_without_leaking_it(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        f"DATABASE_URL=postgresql://postgres.{OLD}:legacy-pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres\n"
        f"SUPABASE_URL=https://{OLD}.supabase.co\n# comment\nGROQ_API_KEY=should-not-matter\n")
    monkeypatch.setattr(tg, "ROOT", tmp_path)
    assert tg.old_project_refs() == {OLD}
    assert tg.old_shared_host() == "aws-0-ap-south-1.pooler.supabase.com"
    monkeypatch.setattr(tg, "ROOT", tmp_path / "nowhere")
    assert tg.old_project_refs() == set() and tg.old_shared_host() is None


# ------------------------------------------------------------------------------------------------ database fingerprint
class FakeConn:
    def __init__(self, marker=None, broken=False, tables=0):
        self.marker, self.broken, self.tables = marker or {}, broken, tables
    def execute(self, sql, *a):
        if self.broken:
            raise RuntimeError('relation "public._setuai_env" does not exist')
        class R:
            def __init__(s, rows): s.rows = rows
            def fetchall(s): return s.rows
            def fetchone(s): return s.rows[0]
        return R(list(self.marker.items())) if "_setuai_env" in sql else R([(self.tables,)])
    def rollback(self): pass


LOCAL = tg.Target("local", "127.0.0.1", 54329, "setuai_v2_integ", "postgres", None, None)
HOSTED = tg.Target("supabase", f"db.{REF}.supabase.co", 5432, "postgres", "postgres", REF, "require")


def test_a_local_database_must_say_integration():
    assert tg.verify_fingerprint(FakeConn({"env": "integration"}), LOCAL)["env"] == "integration"
    for bad in ({}, {"env": "hosted"}, {"env": "production"}):
        with pytest.raises(tg.GuardError):
            tg.verify_fingerprint(FakeConn(bad), LOCAL)


def test_a_hosted_database_must_prove_it_is_the_configured_project():
    good = {"env": "hosted", "schema": tg.SCHEMA_ID, "project_ref": REF}
    assert tg.verify_fingerprint(FakeConn(good), HOSTED)
    for bad in ({**good, "project_ref": OLD}, {**good, "env": "integration"}, {**good, "schema": "legacy"}, {"env": "hosted"}, {}):
        with pytest.raises(tg.GuardError, match="does not match the configured project"):
            tg.verify_fingerprint(FakeConn(bad), HOSTED)
    with pytest.raises(tg.GuardError, match="unreadable"):                           # the OLD shared database has no marker table at all
        tg.verify_fingerprint(FakeConn(broken=True), HOSTED)


def test_only_an_empty_database_is_ever_stampable():
    assert tg.database_is_empty(FakeConn(tables=0)) and not tg.database_is_empty(FakeConn(tables=1))
