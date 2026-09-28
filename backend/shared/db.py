"""
Shared DB connection — Supabase Postgres, direct connection (PRD v5,
"Database connection method"). Every router imports get_db() or
get_connection() from here rather than opening its own connection.

The project uses psycopg (v3) for synchronous PostgreSQL access with a
dict-style row factory. Most routers (audit.py, actuals.py, checks.py,
dashboard.py, activities.py, export.py, schedule.py, schedule_repository.py)
call conn.execute(...)/conn.transaction() directly, which is psycopg v3's
connection-level API (psycopg2 connections have no .execute()/.transaction()
method) — v3 is therefore the one driver this module must return connections
from. conn.cursor() (used by intake.py/schedules.py) works identically under
v3, so this is a single-file, non-breaking change for those callers.

supabase-py is NOT used here for table access, per the doc: it's reserved
for auth only. This connects straight to Postgres with the service-role
connection string.
"""

import os
from pathlib import Path

import psycopg
import psycopg.rows
from dotenv import load_dotenv

# Loaded here, not just in main.py: any standalone entry point that touches
# the DB (smoke_test.py, shared/seed.py run directly, a bare pytest
# collection) imports this module before it imports main.py, if it imports
# main.py at all -- so this is the one place guaranteed to run before
# DATABASE_URL is read below. Idempotent and harmless to call again from
# main.py.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "models" / "schema.sql"
)


def get_connection():
    """
    Create and return a PostgreSQL connection using the Supabase
    DATABASE_URL.

    Rows are returned as dictionaries.
    """
    db_url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Add it to the project .env file."
        )

    return psycopg.connect(
        db_url,
        row_factory=psycopg.rows.dict_row,
    )


def get_conn():
    """
    Backwards-compatible alias for get_connection().
    """
    return get_connection()


def get_db():
    """
    FastAPI dependency — yields a database connection and closes it
    after the request.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Run once at application startup.

    Creates all database tables defined in schema.sql if they do not
    already exist.
    """
    if not DATABASE_URL:
        print(
            "Warning: DATABASE_URL is not configured; "
            "database initialization skipped."
        )
        return

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            with open(SCHEMA_PATH, "r") as f:
                cur.execute(f.read())

        conn.commit()

    finally:
        conn.close()
