"""Перенос данных из SQLite (assistant.db) в MySQL — запускается один раз.

1. Пропиши MYSQL_URL в .env
2. python migrate_to_mysql.py
3. Запусти бота — он будет работать уже с MySQL
"""

import sqlite3

from config import DB_PATH, MYSQL_URL

TABLES = [
    "users", "tasks", "shopping", "expenses", "habits", "habit_logs",
    "fridge", "moods", "pending", "chat_history",
]


def main() -> None:
    if not MYSQL_URL:
        raise SystemExit("Сначала пропиши MYSQL_URL в .env")

    import db as store

    if not store.USE_MYSQL:
        raise SystemExit("MYSQL_URL не подхватился — проверь .env")

    store.init_db()  # создаём таблицы в MySQL

    src = sqlite3.connect(DB_PATH)
    src.row_factory = sqlite3.Row

    for table in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"{table}: нет в SQLite, пропускаю")
            continue
        if not rows:
            print(f"{table}: пусто")
            continue
        cols = rows[0].keys()
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        with store.db() as c:
            for row in rows:
                c.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                    tuple(row),
                )
        print(f"{table}: перенесено {len(rows)}")

    src.close()
    print("\nГотово! Данные в MySQL, можно запускать бота.")


if __name__ == "__main__":
    main()
