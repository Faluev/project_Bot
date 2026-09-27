import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "delivery.db"


connection = sqlite3.connect(DATABASE_PATH)

try:
    cursor = connection.cursor()

    cursor.execute("DELETE FROM employees")

    deleted_count = cursor.rowcount

    connection.commit()

    print(f"✅ Сотрудники сброшены.")
    print(f"Удалено сотрудников: {deleted_count}")

finally:
    connection.close()