"""The v2 connection pool and tx(): pooler-safe (no session state), bounded, self-healing, and fingerprint-checked."""
import os
import threading
import time
import uuid

import psycopg
import pytest

from backend.v2 import db as v2db
from v2world import server_base
from db import target_guard as tg

URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv("DB_V2_URL", URL)
    v2db.close_pool()
    p = v2db.Pool(URL, max_size=1, acquire_timeout=2)
    yield p
    p.close()
    v2db.close_pool()


def backend_pid(c):
    return c.execute("select pg_backend_pid() p").fetchone()["p"]


def test_one_connection_serves_many_transactions(pool):
    pids = set()
    for _ in range(25):
        with pool.connection() as c:
            pids.add(backend_pid(c))
    assert len(pids) == 1 and pool.stats()["created"] == 1 and pool.stats()["discarded"] == 0


def test_the_actor_never_leaks_to_the_next_transaction_on_the_same_connection(monkeypatch):
    monkeypatch.setenv("DB_V2_URL", URL)
    v2db.close_pool()
    monkeypatch.setenv("V2_DB_POOL_MAX", "1")
    a, b = uuid.uuid4(), uuid.uuid4()
    seen = []
    with v2db.tx(a) as c:
        seen.append((backend_pid(c), c.execute("select current_setting('app.actor_id') v").fetchone()["v"]))
    with v2db.tx(None) as c:
        seen.append((backend_pid(c), c.execute("select current_setting('app.actor_id', true) v").fetchone()["v"]))
    with v2db.tx(b) as c:
        seen.append((backend_pid(c), c.execute("select current_setting('app.actor_id') v").fetchone()["v"]))
    with v2db.get_pool().connection() as c:                                   # a raw checkout: nothing session-level survived
        raw = c.execute("select current_setting('app.actor_id', true) v").fetchone()["v"]
    assert len({p for p, _ in seen}) == 1                                    # really the same backend connection
    assert [v for _, v in seen] == [str(a), "", str(b)] and raw in ("", None)
    v2db.close_pool()


def test_timeouts_are_transaction_local_and_enforced(pool):
    with pool.connection() as c:
        default = c.execute("show statement_timeout").fetchone()["statement_timeout"]
    v2db.close_pool()
    os.environ["DB_V2_URL"] = URL
    with pytest.raises(psycopg.errors.QueryCanceled):
        with v2db.tx(statement_timeout_ms=150) as c:
            c.execute("select pg_sleep(2)")
    with v2db.tx() as c:                                                      # same pooled connection, healthy again
        assert c.execute("show statement_timeout").fetchone()["statement_timeout"] == "30s"
    with v2db.get_pool().connection() as c:
        assert c.execute("show statement_timeout").fetchone()["statement_timeout"] == default     # nothing leaked past the transaction
    v2db.close_pool()


def test_a_read_only_transaction_cannot_write(monkeypatch):
    monkeypatch.setenv("DB_V2_URL", URL)
    v2db.close_pool()
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with v2db.tx(readonly=True) as c:
            c.execute("create temp table t_ro (i int)")
    v2db.close_pool()


def test_no_server_side_prepared_statements_are_created(pool):
    with pool.connection() as c:
        for _ in range(30):
            c.execute("select 1 + %s as x", (1,)).fetchone()
        assert c.execute("select count(*) n from pg_prepared_statements").fetchone()["n"] == 0
        c.rollback()


def test_an_application_error_rolls_back_and_the_connection_is_reused(monkeypatch):
    monkeypatch.setenv("DB_V2_URL", URL)
    v2db.close_pool()
    monkeypatch.setenv("V2_DB_POOL_MAX", "1")
    class Boom(Exception): ...
    for _ in range(5):
        with pytest.raises(Boom):
            with v2db.tx() as c:
                c.execute("create temp table t_boom (i int)")
                raise Boom()
    with v2db.tx() as c:
        assert c.execute("select to_regclass('pg_temp.t_boom') is null n").fetchone()["n"]
    st = v2db.get_pool().stats()
    assert st["created"] == 1 and st["discarded"] == 0, st                    # handled errors do not churn connections
    v2db.close_pool()


def test_an_uncommitted_transaction_is_never_handed_to_the_next_user(pool):
    with pool.connection() as c:
        c.execute("create temp table t_left (i int)")                         # opens a transaction and forgets it
    with pool.connection() as c:
        assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert c.execute("select to_regclass('pg_temp.t_left') is null n").fetchone()["n"]


def test_a_connection_killed_by_the_server_is_replaced_transparently(pool):
    with pool.connection() as c:
        pid = backend_pid(c)
    with psycopg.connect(URL, autocommit=True) as killer:
        killer.execute("select pg_terminate_backend(%s)", (pid,))
    time.sleep(0.2)
    with pool.connection() as c:
        assert backend_pid(c) != pid and c.execute("select 1 x").fetchone()["x"] == 1
    assert pool.stats()["discarded"] == 1 and pool.stats()["created"] == 2


def test_a_connection_dying_mid_transaction_is_discarded_not_returned(pool):
    with pytest.raises(psycopg.OperationalError):
        with pool.connection() as c:
            pid = backend_pid(c)
            with psycopg.connect(URL, autocommit=True) as killer:
                killer.execute("select pg_terminate_backend(%s)", (pid,))
            time.sleep(0.2)
            c.execute("select 1")
    with pool.connection() as c:
        assert backend_pid(c) != pid


def test_the_pool_is_bounded_and_times_out_instead_of_hanging(pool):
    done = threading.Event()
    def hold():
        with pool.connection():
            done.wait(5)
    t = threading.Thread(target=hold); t.start()
    time.sleep(0.2)
    pool.acquire_timeout = 0.3
    t0 = time.monotonic()
    with pytest.raises(v2db.PoolTimeout):
        with pool.connection():
            pass
    assert time.monotonic() - t0 < 2
    done.set(); t.join()
    with pool.connection() as c:                                              # capacity returned
        assert c.execute("select 1 x").fetchone()["x"] == 1


def test_connections_are_recycled_after_their_lifetime(pool):
    pool.max_lifetime = 0.2
    with pool.connection() as c:
        first = backend_pid(c)
    time.sleep(0.3)
    with pool.connection() as c:
        assert backend_pid(c) != first


def test_concurrent_users_never_see_each_others_actor(monkeypatch):
    monkeypatch.setenv("DB_V2_URL", URL)
    monkeypatch.setenv("V2_DB_POOL_MAX", "4")
    v2db.close_pool()
    errors, ids = [], [uuid.uuid4() for _ in range(32)]
    def work(i):
        try:
            for _ in range(5):
                with v2db.tx(ids[i]) as c:
                    c.execute("select pg_sleep(0.005)")
                    got = c.execute("select current_setting('app.actor_id') v").fetchone()["v"]
                    if got != str(ids[i]):
                        errors.append((i, got))
        except Exception as e:                                                # noqa: BLE001
            errors.append((i, repr(e)))
    threads = [threading.Thread(target=work, args=(i,)) for i in range(32)]
    [t.start() for t in threads]; [t.join() for t in threads]
    st = v2db.get_pool().stats()
    assert errors == [] and st["created"] <= 4 and st["idle"] <= 4
    v2db.close_pool()


# ------------------------------------------------------------------------------------------------ fingerprint enforcement
@pytest.fixture
def scratch_url():
    admin = psycopg.connect(server_base() + "postgres", autocommit=True)
    name = "setuai_v2_integ_pool_scratch"
    admin.execute(f"drop database if exists {name} with (force)")
    admin.execute(f"create database {name}")
    yield server_base() + name, name
    admin.execute(f"drop database if exists {name} with (force)")
    admin.close()


@pytest.mark.parametrize("marker", [None, "production", "hosted"])
def test_the_pool_refuses_a_database_whose_fingerprint_is_wrong(scratch_url, marker):
    url, _ = scratch_url
    if marker:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("create table public._setuai_env (key text primary key, value text not null)")
            c.execute("insert into public._setuai_env values ('env', %s)", (marker,))
    p = v2db.Pool(url, max_size=1, acquire_timeout=0.5)
    for _ in range(3):                                                        # and it does not leak its slot while refusing
        with pytest.raises(tg.GuardError):
            with p.connection():
                pass
    p.close()


def test_the_api_target_must_be_a_v2_database(monkeypatch):
    for url in ("postgresql://postgres@127.0.0.1:54329/setuai_integ_demo", "postgresql://postgres@10.0.0.9:5432/setuai_v2_x"):
        monkeypatch.setenv("DB_V2_URL", url)
        with pytest.raises(RuntimeError, match="refusing database target"):
            v2db.database_url()
    monkeypatch.delenv("DB_V2_URL"); monkeypatch.delenv("DATABASE_URL_V2", raising=False)
    monkeypatch.setenv("DATABASE_URL", URL)                                   # the legacy variable is never consulted
    with pytest.raises(RuntimeError, match="DB_V2_URL is not set"):
        v2db.database_url()
