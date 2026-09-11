"""
Database schema auto-migrator for SQLite / Postgres.
Ensures new columns added to models are present in the active database.
"""

import sqlite3
import os

def migrate():
    db_path = "trading_system.db"
    if not os.path.exists(db_path):
        print(f"{db_path} does not exist yet. It will be created by SQLAlchemy on startup.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(users)")
        cols = [row[1] for row in cursor.fetchall()]
        print("Existing columns in users table:", cols)

        if "hashed_password" not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN hashed_password VARCHAR(255)")
            print("Successfully added 'hashed_password' column to users table.")

        if "role" not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'admin'")
            print("Successfully added 'role' column to users table.")

        conn.commit()
    except Exception as exc:
        print("Migration error:", exc)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
