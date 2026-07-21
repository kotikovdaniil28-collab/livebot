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
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT NOT NULL
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
        if "budget" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN budget REAL DEFAULT 0")
        if "budget_warned" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN budget_warned TEXT DEFAULT ''")
        tcols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
        if "done_at" not in tcols:
            c.execute("ALTER TABLE tasks ADD COLUMN done_at TEXT DEFAULT ''")
        if "repeat" not in tcols:
            c.execute("ALTER TABLE tasks ADD COLUMN repeat TEXT DEFAULT ''")


# --- users -----------------------------------------------------------------

def upsert_user(chat_id: int) -> None:
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))


USER_FIELDS = {
    "city", "brief_time", "evening_time", "last_digest", "last_evening",
    "list_owner", "budget", "budget_warned",
}


def set_user(chat_id: int, field: str, value) -> None:
    assert field in USER_FIELDS
    with db() as c:
        c.execute(f"UPDATE users SET {field} = ? WHERE chat_id = ?", (value, chat_id))


def get_user(chat_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()


# --- tasks -----------------------------------------------------------------

def add_task(chat_id: int, text: str, remind_at: str | None, repeat: str = "") -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks (chat_id, text, remind_at, repeat, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, text, remind_at, repeat, now_iso()),
        )
        return cur.lastrowid


def delete_task(chat_id: int, task_id: int) -> bool:
    with db() as c:
        cur = c.execute(
            "DELETE FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id)
        )
        return cur.rowcount > 0


def delete_last_expense(chat_id: int) -> sqlite3.Row | None:
    with db() as c:
        row = c.execute(
            "SELECT * FROM expenses WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()
        if row:
            c.execute("DELETE FROM expenses WHERE id = ?", (row["id"],))
        return row


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
    """Текущая серия: сколько дней подряд (включая сегодня или вчера) привычка выполнялась."""
    from datetime import timedelta
    with db() as c:
        dates = {
            r["date"]
            for r in c.execute("SELECT date FROM habit_logs WHERE habit_id = ?", (habit_id,))
        }
    if not dates:
        return 0
    day = datetime.now(TZ).date()
    # серия не рвётся, если сегодня ещё не отмечено, но вчера было
    if day.strftime("%Y-%m-%d") not in dates:
        day = day - timedelta(days=1)
    streak = 0
    while day.strftime("%Y-%m-%d") in dates:
        streak += 1
        day = day - timedelta(days=1)
    return streak


def habit_month_stats(habit_id: int) -> tuple[int, int]:
    """(выполнено дней, прошло дней) за текущий месяц."""
    now = datetime.now(TZ)
    month_start = now.strftime("%Y-%m-01")
    with db() as c:
        done = c.execute(
            "SELECT COUNT(*) AS n FROM habit_logs WHERE habit_id = ? AND date >= ?",
            (habit_id, month_start),
        ).fetchone()["n"]
    return done, now.day


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


# --- chat history (память диалога для LLM-роутера) ----------------------------

def add_history(chat_id: int, role: str, content: str) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO chat_history (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (chat_id, role, content[:1000], now_iso()),
        )
        # держим только последние 20 записей на чат
        c.execute(
            "DELETE FROM chat_history WHERE chat_id = ? AND id NOT IN "
            "(SELECT id FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT 20)",
            (chat_id, chat_id),
        )


def get_history(chat_id: int, limit: int = 10) -> list[dict]:
    """Последние сообщения диалога в формате OpenAI messages (старые → новые)."""
    with db() as c:
        rows = c.execute(
            "SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# --- expenses: суммы по дням и бюджет ------------------------------------------

def expenses_by_day(chat_id: int, days: int = 7) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, сумма), ...] за последние N дней (включая нулевые дни)."""
    from datetime import timedelta
    now = datetime.now(TZ)
    date_from = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with db() as c:
        rows = c.execute(
            "SELECT date, SUM(amount) AS total FROM expenses "
            "WHERE chat_id = ? AND date >= ? GROUP BY date",
            (chat_id, date_from),
        ).fetchall()
    totals = {r["date"]: r["total"] for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        out.append((d, round(totals.get(d, 0), 2)))
    return out


def month_spent(chat_id: int) -> float:
    month_start = datetime.now(TZ).strftime("%Y-%m-01")
    with db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE chat_id = ? AND date >= ?",
            (chat_id, month_start),
        ).fetchone()
    return round(row["total"], 2)
