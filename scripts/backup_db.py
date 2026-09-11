"""
Automated Database Backup Utility.

Creates timestamped snapshots of SQLite (or PostgreSQL dumps) in the `backups/` directory.
Scheduled to run automatically at 16:00 IST every trading day or on-demand.
"""

import os
import shutil
import sqlite3
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
BACKUP_DIR = "backups"
DB_FILE = "trading_system.db"
MAX_BACKUPS_RETAINED = 30


def backup_database():
    """Create a consistent snapshot of the active SQLite database."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    if not os.path.exists(DB_FILE):
        print(f"Database file '{DB_FILE}' not found. Skipping backup.")
        return None

    timestamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"trading_system_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    # Use SQLite online backup API for ACID safety even while server is running
    try:
        src = sqlite3.connect(DB_FILE)
        dst = sqlite3.connect(backup_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        file_size_kb = round(os.path.getsize(backup_path) / 1024, 2)
        print(f"[SUCCESS] Database snapshot created: {backup_path} ({file_size_kb} KB)")

        # Cleanup older backups exceeding retention limit
        backups = sorted(
            [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith("trading_system_backup_")],
            key=os.path.getmtime,
        )
        if len(backups) > MAX_BACKUPS_RETAINED:
            to_remove = backups[:-MAX_BACKUPS_RETAINED]
            for old_backup in to_remove:
                os.remove(old_backup)
                print(f"Purged expired backup: {old_backup}")

        return backup_path

    except Exception as exc:
        print(f"Database backup failed: {exc}")
        # Fallback to copy
        shutil.copy2(DB_FILE, backup_path)
        return backup_path


if __name__ == "__main__":
    backup_database()
