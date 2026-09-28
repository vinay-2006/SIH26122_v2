from pathlib import Path
from backend.shared.db import get_connection

def apply_migration():
    sql_path = Path(__file__).parent / "models" / "migrations" / "legacy" / "001_security_rls_and_foreign_keys.sql"
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()

    print(f"Applying migration from {sql_path}...")
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
    print("Migration applied successfully!")

if __name__ == "__main__":
    apply_migration()
