"""
SETUAI V7 Migration Framework.

Manages ordered, reviewable database migrations for V7 Supabase/PostgreSQL.
Tracks applied versions in `schema_migrations` table.
Executes each migration within an isolated database transaction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "models" / "migrations"


def ensure_migrations_table() -> None:
    """Ensure the schema_migrations tracking table exists."""
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ DEFAULT now()
                    );
                    """
                )


def get_applied_migrations() -> Dict[str, str]:
    """Return dictionary of version -> applied_at string for all applied migrations."""
    ensure_migrations_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version ASC;")
            rows = cur.fetchall()
            return {
                (r["version"] if isinstance(r, dict) else r[0]): str(r["applied_at"] if isinstance(r, dict) else r[1])
                for r in rows
            }


def get_available_migrations() -> List[Path]:
    """Find and return all .sql migration files in the migrations directory in sorted order."""
    if not MIGRATIONS_DIR.exists():
        return []
    # Only top-level .sql files in migrations dir, excluding subdirectories
    sql_files = [f for f in MIGRATIONS_DIR.glob("*.sql") if f.is_file()]
    return sorted(sql_files, key=lambda p: p.name)


def apply_migration_file(file_path: Path) -> str:
    """Apply a single migration file within a database transaction."""
    version = file_path.name
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                logger.info(f"Applying migration: {version}")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING;",
                    (version,),
                )
    return version


def run_migrations(target_version: Optional[str] = None) -> List[str]:
    """
    Run all pending migrations in order up to target_version (or all available if None).
    Returns list of newly applied migration versions.
    """
    ensure_migrations_table()
    applied = get_applied_migrations()
    available = get_available_migrations()

    newly_applied = []
    for m in available:
        v = m.name
        if v in applied:
            continue
        print(f"Applying migration: {v} ...")
        apply_migration_file(m)
        newly_applied.append(v)
        print(f"Applied migration: {v} successfully.")
        if target_version and v == target_version:
            break

    return newly_applied


if __name__ == "__main__":
    import sys

    print("Running SETUAI V7 migrations...")
    applied_list = run_migrations()
    print(f"Migration completed. {len(applied_list)} migrations applied.")
    for a in applied_list:
        print(f"  + {a}")
