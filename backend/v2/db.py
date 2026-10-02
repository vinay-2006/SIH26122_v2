"""Connection handling for the v2 API.

SAFETY
* Reads DB_V2_URL only (never DATABASE_URL). The target must pass db/target_guard.py (local setuai_v2_* database, or an explicitly
  allow-listed Supabase project over TLS that is not the legacy project) and, once connected, the DATABASE'S OWN fingerprint marker must
  match - checked on every new physical connection. A mismatch refuses the connection.
POOLER SAFETY (works through a transaction-mode pooler such as Supavisor/pgbouncer, and equally on a direct connection)
* No session state is ever set: the acting user, timeouts and read-only mode are applied with set_config(..., true) / SET LOCAL, which
  end with the transaction. So a connection handed to the next request carries nothing from the previous one.
* Server-side prepared statements are disabled (prepare_threshold=None) - they break across pooled backends.
* Advisory locks are transaction-level (pg_advisory_xact_lock) only.
POOL
* A small bounded pool (no extra dependency). A connection is returned only after its transaction ended; one whose transaction is
  still open is rolled back, and one that is broken or past its lifetime is discarded and replaced. Callers wait up to
  `acquire_timeout` for a free connection, then get a clear error instead of hanging.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

import psycopg
import psycopg.rows
from psycopg import pq

from db import target_guard as tg


class PoolTimeout(RuntimeError):
    pass


def database_url() -> str:
    url = os.environ.get("DB_V2_URL") or os.environ.get("DATABASE_URL_V2")
    if not url:
        raise RuntimeError("DB_V2_URL is not set (the v2 API never uses DATABASE_URL)")
    try:
        tg.check_target(url)
    except tg.GuardError as exc:
        raise RuntimeError(f"refusing database target: {exc}") from exc
    return url


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Pool:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 8, acquire_timeout: float = 10.0, max_lifetime: float = 1800.0,
                 connect_timeout: int = 10):
        self.target = tg.check_target(url)
        self.url, self.min_size, self.max_size = url, min_size, max_size
        self.acquire_timeout, self.max_lifetime, self.connect_timeout = acquire_timeout, max_lifetime, connect_timeout
        self._idle: "queue.LifoQueue[tuple]" = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(max_size)
        self._lock = threading.Lock()
        self._closed = False
        self.created = 0
        self.discarded = 0

    # ------------------------------------------------------------------ physical connections
    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.url, row_factory=psycopg.rows.dict_row, prepare_threshold=None, connect_timeout=self.connect_timeout,
                               application_name="sih-v2-api", keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
        try:
            tg.verify_fingerprint(conn, self.target)               # the database must be who we think it is, every time
            conn.rollback()
        except Exception:
            conn.close()
            raise
        with self._lock:
            self.created += 1
        return conn

    def _usable(self, conn: psycopg.Connection, born: float) -> bool:
        return (not conn.closed and conn.info.transaction_status == pq.TransactionStatus.IDLE
                and conn.pgconn.status == pq.ConnStatus.OK and time.monotonic() - born < self.max_lifetime)

    # ------------------------------------------------------------------ checkout / return
    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if self._closed:
            raise RuntimeError("pool is closed")
        if not self._slots.acquire(timeout=self.acquire_timeout):
            raise PoolTimeout(f"no database connection available within {self.acquire_timeout:g}s (pool of {self.max_size} exhausted)")
        conn, born = None, time.monotonic()
        try:
            while True:
                try:
                    conn, born = self._idle.get_nowait()
                except queue.Empty:
                    conn = self._connect()
                    born = time.monotonic()
                    break
                if self._usable(conn, born):
                    try:
                        conn.execute("select 1")                   # cheap liveness check; also surfaces a server-side kill
                        conn.rollback()
                        break
                    except psycopg.Error:
                        pass
                self._discard(conn)
                conn = None
            yield conn
        finally:                                                   # application errors roll back and reuse; a broken connection is replaced
            if conn is not None:
                self._release(conn, born)
            self._slots.release()

    def _release(self, conn: psycopg.Connection, born: float) -> None:
        try:
            if not conn.closed and conn.info.transaction_status != pq.TransactionStatus.IDLE:
                conn.rollback()                                    # never hand back an open transaction
            if self._usable(conn, born) and not self._closed:
                self._idle.put((conn, born))
                return
        except psycopg.Error:
            pass
        self._discard(conn)

    def _discard(self, conn: psycopg.Connection) -> None:
        with self._lock:
            self.discarded += 1
        try:
            conn.close()
        except Exception:
            pass

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn, _ = self._idle.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:
                pass

    def stats(self) -> Dict[str, int]:
        return {"idle": self._idle.qsize(), "created": self.created, "discarded": self.discarded, "max": self.max_size}


# ---------------------------------------------------------------------------------------------- process-wide pool
_pool: Optional[Pool] = None
_pool_lock = threading.Lock()


def get_pool() -> Pool:
    global _pool
    url = database_url()
    with _pool_lock:
        if _pool is None or _pool.url != url or _pool._closed:
            if _pool is not None:
                _pool.close()
            _pool = Pool(url, max_size=_int("V2_DB_POOL_MAX", 8), acquire_timeout=float(_int("V2_DB_ACQUIRE_TIMEOUT_S", 10)))
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def tx(actor_id: Optional[uuid.UUID | str] = None, statement_timeout_ms: int = 30_000, readonly: bool = False) -> Iterator[psycopg.Connection]:
    """One transaction on a pooled connection; commit on success, roll back on any error. Everything session-like is LOCAL to it:
    the acting user (app.actor_id, which the database's own role guards read), statement / lock / idle-in-transaction timeouts."""
    with get_pool().connection() as conn:
        try:
            conn.execute(
                "select set_config('app.actor_id', %s, true), set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', '5000', true), set_config('idle_in_transaction_session_timeout', '60000', true)",
                (str(actor_id) if actor_id is not None else "", str(int(statement_timeout_ms))))
            if readonly:
                conn.execute("set transaction read only")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
