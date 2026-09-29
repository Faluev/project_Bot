import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from config import BACKUP_PATH, DATABASE_PATH, TIMEZONE


BACKUP_DIR = BACKUP_PATH


def backup_database():
    """Создаёт консистентную копию рабочей SQLite-базы."""
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DATABASE_PATH}")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = BACKUP_DIR / f"delivery_{timestamp}.db"

    with sqlite3.connect(DATABASE_PATH) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)

    return backup_path


if __name__ == "__main__":
    path = backup_database()
    print(f"Резервная копия создана: {path}")
