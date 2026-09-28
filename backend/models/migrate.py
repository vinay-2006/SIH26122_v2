"""
CLI script to apply V7 migrations.
Usage:
    python backend/models/migrate.py
"""

from backend.shared.migrator import run_migrations, get_applied_migrations

if __name__ == "__main__":
    print("========================================")
    print("SETUAI V7 Migration Runner")
    print("========================================")
    applied = run_migrations()
    print(f"Applied {len(applied)} migration(s).")
    all_applied = get_applied_migrations()
    print(f"Total applied migrations: {len(all_applied)}")
