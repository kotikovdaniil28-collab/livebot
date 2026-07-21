"""Синхронизация с Notion: задачи, покупки и расходы зеркалятся в базы Notion.

Как подключить:
1. Создай интеграцию на https://www.notion.so/my-integrations и скопируй токен (ntn_...)
2. В Notion создай (или выбери) страницу, куда бот сложит базы, и дай интеграции
   доступ: на странице ⋯ → Connections → выбери свою интеграцию
3. В .env добавь NOTION_TOKEN=ntn_... и перезапусти бота

Бот сам создаст на этой странице четыре базы: «Задачи», «Покупки», «Расходы»,
«Настроение» — и будет обновлять их в фоне (раз в ~2 минуты).

Синхронизация двусторонняя:
- отметил задачу выполненной в Notion → она закроется и в боте (и наоборот);
- удалил/архивировал товар в Notion → он исчезнет из списка покупок бота.
"""

import logging
import os

import aiohttp

import db as store

log = logging.getLogger("bot.notion")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_API = "https://api.notion.com/v1"
_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# id баз Notion (заполняются при первом запуске и хранятся в SQLite)
_DB_TASKS = "notion_db_tasks"
_DB_SHOPPING = "notion_db_shopping"
_DB_EXPENSES = "notion_db_expenses"
_DB_MOODS = "notion_db_moods"

_setup_failed_logged = False


def enabled() -> bool:
    return bool(NOTION_TOKEN)


# --- служебные таблицы -------------------------------------------------------

def _init_tables() -> None:
    with store.db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS notion_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notion_map (
                entity TEXT NOT NULL,
                local_id INTEGER NOT NULL,
                page_id TEXT NOT NULL,
                extra TEXT DEFAULT '',
                PRIMARY KEY (entity, local_id)
            );
            """
        )


def _meta_get(key: str) -> str:
    with store.db() as c:
        row = c.execute("SELECT value FROM notion_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _meta_set(key: str, value: str) -> None:
    with store.db() as c:
        c.execute(
            "INSERT INTO notion_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _map_get(entity: str, local_id: int) -> tuple[str, str] | None:
    with store.db() as c:
        row = c.execute(
            "SELECT page_id, extra FROM notion_map WHERE entity = ? AND local_id = ?",
            (entity, local_id),
        ).fetchone()
    return (row["page_id"], row["extra"]) if row else None


def _map_set(entity: str, local_id: int, page_id: str, extra: str = "") -> None:
    with store.db() as c:
        c.execute(
            "INSERT INTO notion_map (entity, local_id, page_id, extra) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(entity, local_id) DO UPDATE SET page_id = excluded.page_id, extra = excluded.extra",
            (entity, local_id, page_id, extra),
        )


def _map_all(entity: str) -> dict[int, str]:
    with store.db() as c:
        rows = c.execute(
            "SELECT local_id, page_id FROM notion_map WHERE entity = ?", (entity,)
        ).fetchall()
    return {r["local_id"]: r["page_id"] for r in rows}


def _map_del(entity: str, local_id: int) -> None:
    with store.db() as c:
        c.execute(
            "DELETE FROM notion_map WHERE entity = ? AND local_id = ?", (entity, local_id)
        )


# --- HTTP --------------------------------------------------------------------

async def _req(session: aiohttp.ClientSession, method: str, path: str, payload: dict | None = None) -> dict:
    async with session.request(method, f"{NOTION_API}{path}", json=payload, headers=_HEADERS) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"Notion {resp.status}: {data.get('message', data)}")
        return data


# --- первичная настройка: находим страницу и создаём базы ---------------------

async def _find_parent_page(session: aiohttp.ClientSession) -> str:
    """Первая страница, к которой у интеграции есть доступ."""
    data = await _req(
        session, "POST", "/search",
        {"filter": {"property": "object", "value": "page"}, "page_size": 10},
    )
    for r in data.get("results", []):
        if r.get("object") == "page":
            return r["id"]
    raise RuntimeError(
        "интеграции не выдан доступ ни к одной странице Notion "
        "(открой страницу → ⋯ → Connections → добавь интеграцию)"
    )


async def _create_db(session: aiohttp.ClientSession, parent_id: str, title: str, props: dict) -> str:
    data = await _req(
        session, "POST", "/databases",
        {
            "parent": {"type": "page_id", "page_id": parent_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": props,
        },
    )
    return data["id"]


async def _ensure_databases(session: aiohttp.ClientSession) -> bool:
    """Создаёт базы при первом запуске. Возвращает True, если всё готово."""
    global _setup_failed_logged
    if (
        _meta_get(_DB_TASKS) and _meta_get(_DB_SHOPPING)
        and _meta_get(_DB_EXPENSES) and _meta_get(_DB_MOODS)
    ):
        return True
    try:
        parent = await _find_parent_page(session)
        if not _meta_get(_DB_TASKS):
            db_id = await _create_db(session, parent, "📌 Задачи", {
                "Задача": {"title": {}},
                "Выполнено": {"checkbox": {}},
                "Напоминание": {"date": {}},
                "Создано": {"date": {}},
            })
            _meta_set(_DB_TASKS, db_id)
        if not _meta_get(_DB_SHOPPING):
            db_id = await _create_db(session, parent, "🛒 Покупки", {
                "Товар": {"title": {}},
                "Добавлено": {"date": {}},
            })
            _meta_set(_DB_SHOPPING, db_id)
        if not _meta_get(_DB_EXPENSES):
            db_id = await _create_db(session, parent, "💸 Расходы", {
                "Покупка": {"title": {}},
                "Сумма": {"number": {"format": "ruble"}},
                "Дата": {"date": {}},
            })
            _meta_set(_DB_EXPENSES, db_id)
        if not _meta_get(_DB_MOODS):
            db_id = await _create_db(session, parent, "🌙 Настроение", {
                "День": {"title": {}},
                "Оценка": {"number": {}},
                "Заметка": {"rich_text": {}},
                "Дата": {"date": {}},
            })
            _meta_set(_DB_MOODS, db_id)
        log.info("Notion: базы созданы/найдены")
        _setup_failed_logged = False
        return True
    except Exception as e:
        if not _setup_failed_logged:
            log.warning("Notion: настройка не удалась: %s", e)
            _setup_failed_logged = True
        return False


# --- синхронизация -----------------------------------------------------------

def _title(prop: str, text: str) -> dict:
    return {prop: {"title": [{"type": "text", "text": {"content": text[:2000]}}]}}


async def _sync_tasks(session: aiohttp.ClientSession) -> None:
    db_id = _meta_get(_DB_TASKS)
    with store.db() as c:
        rows = c.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    for t in rows:
        mapped = _map_get("task", t["id"])
        done = bool(t["done"])
        if mapped is None:
            props = _title("Задача", t["text"]) | {"Выполнено": {"checkbox": done}}
            if t["remind_at"]:
                props["Напоминание"] = {"date": {"start": t["remind_at"]}}
            if t["created_at"]:
                props["Создано"] = {"date": {"start": t["created_at"]}}
            page = await _req(session, "POST", "/pages", {
                "parent": {"database_id": db_id}, "properties": props,
            })
            _map_set("task", t["id"], page["id"], "done" if done else "open")
        elif (mapped[1] == "done") != done:
            await _req(session, "PATCH", f"/pages/{mapped[0]}", {
                "properties": {"Выполнено": {"checkbox": done}},
            })
            _map_set("task", t["id"], mapped[0], "done" if done else "open")


async def _sync_shopping(session: aiohttp.ClientSession) -> None:
    db_id = _meta_get(_DB_SHOPPING)
    with store.db() as c:
        rows = c.execute("SELECT * FROM shopping ORDER BY id").fetchall()
    current_ids = set()
    for s in rows:
        current_ids.add(s["id"])
        if _map_get("shop", s["id"]) is None:
            props = _title("Товар", s["item"])
            if s["created_at"]:
                props["Добавлено"] = {"date": {"start": s["created_at"]}}
            page = await _req(session, "POST", "/pages", {
                "parent": {"database_id": db_id}, "properties": props,
            })
            _map_set("shop", s["id"], page["id"])
    # купленное/удалённое — архивируем в Notion
    for local_id, page_id in _map_all("shop").items():
        if local_id not in current_ids:
            try:
                await _req(session, "PATCH", f"/pages/{page_id}", {"archived": True})
            except Exception:
                pass
            _map_del("shop", local_id)


async def _sync_expenses(session: aiohttp.ClientSession) -> None:
    db_id = _meta_get(_DB_EXPENSES)
    with store.db() as c:
        rows = c.execute("SELECT * FROM expenses ORDER BY id").fetchall()
    for e in rows:
        if _map_get("exp", e["id"]) is None:
            props = _title("Покупка", e["item"]) | {
                "Сумма": {"number": e["amount"]},
                "Дата": {"date": {"start": e["date"]}},
            }
            page = await _req(session, "POST", "/pages", {
                "parent": {"database_id": db_id}, "properties": props,
            })
            _map_set("exp", e["id"], page["id"])


async def _sync_moods(session: aiohttp.ClientSession) -> None:
    db_id = _meta_get(_DB_MOODS)
    with store.db() as c:
        rows = c.execute("SELECT * FROM moods ORDER BY id").fetchall()
    for m in rows:
        state = f"{m['score']}|{(m['note'] or '')[:100]}"
        mapped = _map_get("mood", m["id"])
        score_txt = f"{m['score']}/10" if m["score"] else "—"
        props = _title("День", f"{m['date']} · {score_txt}") | {
            "Оценка": {"number": m["score"]},
            "Дата": {"date": {"start": m["date"]}},
            "Заметка": {"rich_text": [{"type": "text", "text": {"content": (m["note"] or "")[:2000]}}]},
        }
        if mapped is None:
            page = await _req(session, "POST", "/pages", {
                "parent": {"database_id": db_id}, "properties": props,
            })
            _map_set("mood", m["id"], page["id"], state)
        elif mapped[1] != state:
            # оценку или заметку обновили в боте — обновляем страницу
            await _req(session, "PATCH", f"/pages/{mapped[0]}", {"properties": props})
            _map_set("mood", m["id"], mapped[0], state)


# --- обратная синхронизация: Notion → бот --------------------------------------

async def _pull_tasks(session: aiohttp.ClientSession) -> None:
    """Задачи, отмеченные выполненными в Notion, закрываются и в боте."""
    db_id = _meta_get(_DB_TASKS)
    page_to_local = {v: k for k, v in _map_all("task").items()}
    if not page_to_local:
        return
    cursor = None
    checked: dict[str, bool] = {}
    for _ in range(10):  # максимум 10 страниц по 100
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = await _req(session, "POST", f"/databases/{db_id}/query", payload)
        for page in data.get("results", []):
            cb = page.get("properties", {}).get("Выполнено", {}).get("checkbox")
            if cb is not None:
                checked[page["id"]] = bool(cb)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    with store.db() as c:
        for page_id, done in checked.items():
            local_id = page_to_local.get(page_id)
            if local_id is None:
                continue
            mapped = _map_get("task", local_id)
            local_state = mapped[1] if mapped else ""
            if done and local_state == "open":
                # отметили в Notion → закрываем в боте
                c.execute(
                    "UPDATE tasks SET done = 1, done_at = ? WHERE id = ? AND done = 0",
                    (store.now_iso(), local_id),
                )
                _map_set("task", local_id, page_id, "done")
            elif not done and local_state == "done":
                # сняли галочку в Notion → снова открываем
                c.execute(
                    "UPDATE tasks SET done = 0, done_at = '' WHERE id = ?", (local_id,)
                )
                _map_set("task", local_id, page_id, "open")


async def _pull_shopping(session: aiohttp.ClientSession) -> None:
    """Товары, архивированные/удалённые в Notion, убираются из списка бота."""
    mapping = _map_all("shop")
    for local_id, page_id in mapping.items():
        try:
            page = await _req(session, "GET", f"/pages/{page_id}")
        except Exception:
            continue
        if page.get("archived") or page.get("in_trash"):
            with store.db() as c:
                c.execute("DELETE FROM shopping WHERE id = ?", (local_id,))
            _map_del("shop", local_id)


async def sync() -> None:
    """Одна итерация синхронизации (вызывается из фонового цикла)."""
    if not enabled():
        return
    _init_tables()
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if not await _ensure_databases(session):
            return
        try:
            # сначала подтягиваем изменения из Notion, потом отправляем свои
            await _pull_tasks(session)
            await _pull_shopping(session)
            await _sync_tasks(session)
            await _sync_shopping(session)
            await _sync_expenses(session)
            await _sync_moods(session)
        except Exception:
            log.exception("Notion: ошибка синхронизации")
