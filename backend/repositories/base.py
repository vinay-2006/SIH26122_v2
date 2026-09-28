"""
Base Repository supporting PostgreSQL RLS session context and project scoping.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Generator

import psycopg

from backend.shared.db import get_connection


class BaseRepository:
    """
    Base repository providing defense-in-depth database connections.
    Enforces PostgreSQL RLS by setting the local authenticated role and
    caller identity (request.jwt.claim.sub) on each connection.
    """

    @staticmethod
    @contextmanager
    def rls_connection(user_id: uuid.UUID) -> Generator[psycopg.Connection, None, None]:
        """
        Yields a PostgreSQL connection configured for RLS execution under
        the authenticated user's identity.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE authenticated;")
                cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true);", (str(user_id),))
            yield conn
