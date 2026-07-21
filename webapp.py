"""Telegram Mini App: встроенный веб-сервер (aiohttp) + REST API.

Безопасность: каждый запрос к /api/* приходит с initData из Telegram.WebApp,
подпись проверяется по HMAC с ключом бота (официальный алгоритм Telegram).
Все запросы к базе выполняются только от имени проверенного chat_id.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import db
from config import BOT_TOKEN, TZ, WEB_PORT

log = logging.getLogger("webapp")

HTML_PATH = Path(__file__).parent / "assets" / "webapp.html"


def _check_init_data(init_data: str) -> int | None:
    """Проверяет подпись initData; возвращает chat_id или None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        if not received_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        user = json.loads(pairs.get("user", "{}"))
        return int(user["id"])
    except Exception:
        return None


def _fmt_date(iso: str) -> str:
    """2025-03-08 → 08.03"""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return iso


def _overview(chat_id: int) -> dict:
    today = db.today()
    soon = (datetime.now(TZ) + timedelta(days=2)).strftime("%Y-%m-%d")
    week_ago = (datetime.now(TZ) - timedelta(days=6)).strftime("%Y-%m-%d")

    tasks = [
        {"id": r["id"], "text": r["text"], "remind": (r["remind_at"] or "")[11:16]}
        for r in db.open_tasks(chat_id)
    ]
    done_today = len(db.tasks_done_today(chat_id))

    shopping = [{"id": r["id"], "item": r["item"]} for r in db.get_shopping(chat_id)]

    fridge = []
    for r in db.get_fridge(chat_id):
        exp = r["expires_at"] or ""
        fridge.append(
            {
                "id": r["id"],
                "product": r["product"],
                "expires": _fmt_date(exp) if exp else "",
                "expiring": bool(exp) and exp <= soon,
                "expired": bool(exp) and exp < today,
            }
        )

    habits = [
        {
            "id": r["id"],
            "name": r["name"],
            "time": r["remind_time"] or "",
            "done": db.habit_done_today(r["id"]),
            "streak": db.habit_streak(r["id"]),
        }
        for r in db.get_habits(chat_id)
    ]

    exp_today = db.expenses_since(chat_id, today)
    exp_week = db.expenses_since(chat_id, week_ago)
    expenses = {
        "today": round(sum(r["amount"] for r in exp_today), 2),
        "week": round(sum(r["amount"] for r in exp_week), 2),
        "recent": [
            {"item": r["item"], "amount": r["amount"], "date": _fmt_date(r["date"])}
            for r in list(reversed(exp_week))[:10]
        ],
    }

    return {
        "tasks": tasks,
        "doneToday": done_today,
        "shopping": shopping,
        "fridge": fridge,
        "habits": habits,
        "expenses": expenses,
    }


async def _api(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    chat_id = _check_init_data(body.get("initData", ""))
    if not chat_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    action = body.get("action", "overview")
    p = body.get("payload") or {}

    try:
        if action == "task_done":
            db.complete_task(chat_id, int(p["id"]))
        elif action == "task_add":
            text = str(p.get("text", "")).strip()[:200]
            if text:
                db.add_task(chat_id, text, None)
        elif action == "shop_add":
            items = [s.strip()[:100] for s in str(p.get("text", "")).split(",") if s.strip()]
            if items:
                db.add_shopping(chat_id, items)
        elif action == "shop_del":
            db.remove_shopping(chat_id, int(p["id"]))
        elif action == "shop_clear":
            db.clear_shopping(chat_id)
        elif action == "fridge_add":
            product = str(p.get("product", "")).strip()[:100]
            expires = str(p.get("expires", "")).strip()[:10]
            if product:
                db.add_fridge(chat_id, product, expires)
        elif action == "fridge_del":
            db.remove_fridge(chat_id, int(p["id"]))
        elif action == "habit_log":
            db.log_habit(chat_id, int(p["id"]))
        elif action == "habit_add":
            name = str(p.get("name", "")).strip()[:100]
            if name:
                db.add_habit(chat_id, name)
        elif action == "habit_del":
            db.delete_habit(chat_id, int(p["id"]))
        elif action == "expense_add":
            item = str(p.get("item", "")).strip()[:100]
            amount = float(p.get("amount", 0))
            if item and amount > 0:
                db.add_expense(chat_id, item, amount)
        elif action != "overview":
            return web.json_response({"error": "unknown action"}, status=400)
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": "bad payload"}, status=400)

    return web.json_response(_overview(chat_id))


async def _index(_: web.Request) -> web.Response:
    return web.Response(
        text=HTML_PATH.read_text(encoding="utf-8"),
        content_type="text/html",
        charset="utf-8",
    )


async def start_webapp() -> web.AppRunner:
    """Поднимает aiohttp-сервер мини-аппа; возвращает runner для остановки."""
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_post("/api", _api)
    app.router.add_post("/api/", _api)  # прокси Vercel добавляет слэш в конце
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info("Мини-апп поднят на порту %s", WEB_PORT)
    return runner
