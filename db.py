"""SQLite: схема и все функции работы с базой."""

import json
import sqlite3
from datetime import datetime

from config import DB_PATH, TZ


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="minutes")


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def init_db() -> None:
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                city TEXT DEFAULT '',
                brief_time TEXT DEFAULT '07:30',
                evening_time TEXT DEFAULT '21:00',
                last_digest TEXT DEFAULT '',
                last_evening TEXT DEFAULT '',
                list_owner INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT,
                reminded INTEGER DEFAULT 0,
                done INTEGER DEFAULT 0,
                done_at TEXT DEFAULT '',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS shopping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner INTEGER NOT NULL,
                item TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                amount REAL NOT NULL,
                date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                remind_time TEXT DEFAULT '',
                last_remind TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                UNIQUE(habit_id, date)
            );
            CREATE TABLE IF NOT EXISTS fridge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                product TEXT NOT NULL,
                expires_at TEXT DEFAULT '',
                warned INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS moods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                score INTEGER,
                note TEXT DEFAULT '',
                UNIQUE(chat_id, date)
            );
            CREATE TABLE IF NOT EXISTS pending (
                chat_id INTEGER PRIMARY KEY,
                items_json TEXT DEFAULT '[]'
            );
            """
        )
    # миграция старой базы (добавляем недостающие колонки, если бот обновился)
    with db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "evening_time" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN evening_time TEXT DEFAULT '21:00'")
        if "last_evening" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN last_evening TEXT DEFAULT ''")
        if "list_owner" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN list_owner INTEGER DEFAULT 0")
        tcols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
        if "done_at" not in tcols:
            c.execute("ALTER TABLE tasks ADD COLUMN done_at TEXT DEFAULT ''")


# --- users -----------------------------------------------------------------

def upsert_user(chat_id: int) -> None:
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))


USER_FIELDS = {"city", "brief_time", "evening_time", "last_digest", "last_evening", "list_owner"}


def set_user(chat_id: int, field: str, value) -> None:
    assert field in USER_FIELDS
    with db() as c:
        c.execute(f"UPDATE users SET {field} = ? WHERE chat_id = ?", (value, chat_id))


def get_user(chat_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()


# --- tasks -----------------------------------------------------------------

def add_task(chat_id: int, text: str, remind_at: str | None) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks (chat_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, text, remind_at, now_iso()),
        )
        return cur.lastrowid


def open_tasks(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM tasks WHERE chat_id = ? AND done = 0 ORDER BY id", (chat_id,)
        ).fetchall()


def complete_task(chat_id: int, task_id: int) -> bool:
    with db() as c:
        cur = c.execute(
            "UPDATE tasks SET done = 1, done_at = ? WHERE id = ? AND chat_id = ? AND done = 0",
            (now_iso(), task_id, chat_id),
        )
        return cur.rowcount > 0


def tasks_done_today(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM tasks WHERE chat_id = ? AND done = 1 AND done_at LIKE ? ORDER BY id",
            (chat_id, today() + "%"),
        ).fetchall()


# --- shopping list ---------------------------------------------------------

def list_owner(chat_id: int) -> int:
    user = get_user(chat_id)
    return user["list_owner"] or chat_id if user else chat_id


def add_shopping(chat_id: int, items: list[str]) -> int:
    owner = list_owner(chat_id)
    added = 0
    with db() as c:
        existing = {
            r["item"].lower()
            for r in c.execute("SELECT item FROM shopping WHERE owner = ?", (owner,))
        }
        for item in items:
            item = item.strip()
            if item and item.lower() not in existing:
                c.execute(
                    "INSERT INTO shopping (owner, item, created_at) VALUES (?, ?, ?)",
                    (owner, item, now_iso()),
                )
                existing.add(item.lower())
                added += 1
    return added


def get_shopping(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM shopping WHERE owner = ? ORDER BY id", (list_owner(chat_id),)
        ).fetchall()


def remove_shopping(chat_id: int, item_id: int) -> bool:
    with db() as c:
        cur = c.execute(
            "DELETE FROM shopping WHERE id = ? AND owner = ?", (item_id, list_owner(chat_id))
        )
        return cur.rowcount > 0


def clear_shopping(chat_id: int) -> int:
    with db() as c:
        cur = c.execute("DELETE FROM shopping WHERE owner = ?", (list_owner(chat_id),))
        return cur.rowcount


# --- expenses ----------------------------------------------------------------

def add_expense(chat_id: int, item: str, amount: float) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO expenses (chat_id, item, amount, date) VALUES (?, ?, ?, ?)",
            (chat_id, item, amount, today()),
        )


def expenses_since(chat_id: int, date_from: str) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM expenses WHERE chat_id = ? AND date >= ? ORDER BY id",
            (chat_id, date_from),
        ).fetchall()


# --- habits ------------------------------------------------------------------

def add_habit(chat_id: int, name: str, remind_time: str = "") -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO habits (chat_id, name, remind_time) VALUES (?, ?, ?)",
            (chat_id, name, remind_time),
        )
        return cur.lastrowid


def get_habits(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM habits WHERE chat_id = ? ORDER BY id", (chat_id,)
        ).fetchall()


def delete_habit(chat_id: int, habit_id: int) -> bool:
    with db() as c:
        cur = c.execute(
            "DELETE FROM habits WHERE id = ? AND chat_id = ?", (habit_id, chat_id)
        )
        c.execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit_id,))
        return cur.rowcount > 0


def log_habit(chat_id: int, habit_id: int) -> bool:
    with db() as c:
        habit = c.execute(
            "SELECT id FROM habits WHERE id = ? AND chat_id = ?", (habit_id, chat_id)
        ).fetchone()
        if not habit:
            return False
        c.execute(
            "INSERT OR IGNORE INTO habit_logs (habit_id, date) VALUES (?, ?)",
            (habit_id, today()),
        )
        return True


def habit_done_today(habit_id: int) -> bool:
    with db() as c:
        return (
            c.execute(
                "SELECT 1 FROM habit_logs WHERE habit_id = ? AND date = ?",
                (habit_id, today()),
            ).fetchone()
            is not None
        )


def habit_streak(habit_id: int) -> int:
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM habit_logs WHERE habit_id = ?", (habit_id,)
        ).fetchone()["n"]


# --- fridge (виртуальный холодильник + сроки годности) -----------------------

def add_fridge(chat_id: int, product: str, expires_at: str = "") -> None:
    with db() as c:
        c.execute(
            "INSERT INTO fridge (chat_id, product, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, product, expires_at, now_iso()),
        )


def get_fridge(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM fridge WHERE chat_id = ? ORDER BY CASE WHEN expires_at = '' THEN 1 ELSE 0 END, expires_at",
            (chat_id,),
        ).fetchall()


def remove_fridge(chat_id: int, item_id: int) -> bool:
    with db() as c:
        cur = c.execute(
            "DELETE FROM fridge WHERE id = ? AND chat_id = ?", (item_id, chat_id)
        )
        return cur.rowcount > 0


def clear_fridge(chat_id: int) -> int:
    with db() as c:
        cur = c.execute("DELETE FROM fridge WHERE chat_id = ?", (chat_id,))
        return cur.rowcount


def expiring_products(date_limit: str) -> list[sqlite3.Row]:
    """Продукты, срок которых истекает не позже date_limit и о которых ещё не предупреждали."""
    with db() as c:
        return c.execute(
            "SELECT * FROM fridge WHERE warned = 0 AND expires_at != '' AND expires_at <= ?",
            (date_limit,),
        ).fetchall()


def mark_warned(item_id: int) -> None:
    with db() as c:
        c.execute("UPDATE fridge SET warned = 1 WHERE id = ?", (item_id,))


# --- moods (дневник настроения) ----------------------------------------------

def set_mood(chat_id: int, score: int) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO moods (chat_id, date, score) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, date) DO UPDATE SET score = excluded.score",
            (chat_id, today(), score),
        )


def set_mood_note(chat_id: int, note: str) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO moods (chat_id, date, note) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, date) DO UPDATE SET note = excluded.note",
            (chat_id, today(), note),
        )


def recent_moods(chat_id: int, limit: int = 7) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM moods WHERE chat_id = ? ORDER BY date DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()


# --- pending (недостающие ингредиенты для кнопки «в список покупок») ----------

def set_pending(chat_id: int, items: list[str]) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO pending (chat_id, items_json) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET items_json = excluded.items_json",
            (chat_id, json.dumps(items, ensure_ascii=False)),
        )


def pop_pending(chat_id: int) -> list[str]:
    with db() as c:
        row = c.execute(
            "SELECT items_json FROM pending WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        c.execute("DELETE FROM pending WHERE chat_id = ?", (chat_id,))
    if not row:
        return []
    try:
        return json.loads(row["items_json"])
    except Exception:
        return []
