"""Connection handling for the v2 API.

* Reads DB_V2_URL only (never DATABASE_URL), and refuses anything but a local setuai_v2_* database until hosted use is approved.
* Every write transaction runs as the authenticated user: `SET LOCAL app.actor_id` is set first, so the database's own role guards
  apply. Application code never sets `app.system`.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg
import psycopg.rows

from db.migrate import GuardError, check_target


def database_url() -> str:
    url = os.environ.get("DB_V2_URL") or os.environ.get("DATABASE_URL_V2")
    if not url:
        raise RuntimeError("DB_V2_URL is not set (the v2 API never uses DATABASE_URL)")
    try:
        check_target(url)
    except GuardError as exc:
        raise RuntimeError(f"refusing database target: {exc}") from exc
    return url


@contextmanager
def tx(actor_id: Optional[uuid.UUID | str] = None) -> Iterator[psycopg.Connection]:
    """One transaction; commit on success, roll back on any error. `actor_id` becomes app.actor_id for the whole transaction."""
    conn = psycopg.connect(database_url(), row_factory=psycopg.rows.dict_row)
    try:
        if actor_id is not None:
            conn.execute("select set_config('app.actor_id', %s, true)", (str(actor_id),))
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
