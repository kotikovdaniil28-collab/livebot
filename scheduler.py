"""Фоновый цикл: напоминания о задачах и привычках, утро/вечер, сроки годности."""

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db as store
import notion
from config import TZ
from handlers.life import mood_keyboard
from handlers.tasks import reminder_keyboard
from handlers.ui import EVENING_IMG, EXPIRING_IMG, MORNING_IMG, send_pretty
from llm import SAVE_PRODUCT_PROMPT, llm
from services import build_digest, build_evening

log = logging.getLogger("bot.scheduler")


def _next_remind(remind_at: str, repeat: str) -> str:
    """Следующее срабатывание повторяющегося напоминания."""
    dt = datetime.fromisoformat(remind_at)
    now = datetime.now(TZ).replace(tzinfo=None) if dt.tzinfo is None else datetime.now(TZ)
    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}[repeat]
    while dt <= now:
        dt += step
    return dt.strftime("%Y-%m-%dT%H:%M")


async def _task_reminders(bot: Bot, now_str: str) -> None:
    with store.db() as c:
        due = c.execute(
            "SELECT * FROM tasks WHERE done = 0 AND reminded = 0 "
            "AND remind_at IS NOT NULL AND remind_at <= ?",
            (now_str,),
        ).fetchall()
    for t in due:
        repeat = t["repeat"] if "repeat" in t.keys() else ""
        try:
            prefix = "🔁 " if repeat else "⏰ "
            await bot.send_message(
                t["chat_id"],
                f"{prefix}Напоминание: {t['text']}",
                reply_markup=None if repeat else reminder_keyboard(t["id"]),
            )
        except Exception:
            log.exception("task reminder failed chat=%s", t["chat_id"])
        finally:
            with store.db() as c:
                if repeat:
                    # повторяющееся: переносим на следующий раз, не закрываем
                    try:
                        nxt = _next_remind(t["remind_at"], repeat)
                        c.execute("UPDATE tasks SET remind_at = ? WHERE id = ?", (nxt, t["id"]))
                    except Exception:
                        c.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (t["id"],))
                else:
                    c.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (t["id"],))


async def _morning_digests(bot: Bot, hhmm: str, today: str) -> None:
    with store.db() as c:
        users = c.execute(
            "SELECT * FROM users WHERE brief_time = ? AND last_digest != ?",
            (hhmm, today),
        ).fetchall()
    for u in users:
        try:
            await send_pretty(bot, u["chat_id"], await build_digest(u["chat_id"]), MORNING_IMG)
        except Exception:
            log.exception("morning digest failed chat=%s", u["chat_id"])
        finally:
            store.set_user(u["chat_id"], "last_digest", today)


async def _evening_digests(bot: Bot, hhmm: str, today: str) -> None:
    with store.db() as c:
        users = c.execute(
            "SELECT * FROM users WHERE evening_time = ? AND last_evening != ?",
            (hhmm, today),
        ).fetchall()
    for u in users:
        try:
            await send_pretty(
                bot, u["chat_id"], build_evening(u["chat_id"]), EVENING_IMG, mood_keyboard()
            )
        except Exception:
            log.exception("evening digest failed chat=%s", u["chat_id"])
        finally:
            store.set_user(u["chat_id"], "last_evening", today)


async def _habit_reminders(bot: Bot, hhmm: str, today: str) -> None:
    with store.db() as c:
        habits = c.execute(
            "SELECT * FROM habits WHERE remind_time = ? AND last_remind != ?",
            (hhmm, today),
        ).fetchall()
    for h in habits:
        try:
            if not store.habit_done_today(h["id"]):
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Сделано", callback_data=f"habit:{h['id']}")]
                    ]
                )
                await bot.send_message(
                    h["chat_id"],
                    f"🔁 Напоминание о привычке: {h['name']}",
                    reply_markup=kb,
                )
        except Exception:
            log.exception("habit reminder failed chat=%s", h["chat_id"])
        finally:
            with store.db() as c:
                c.execute("UPDATE habits SET last_remind = ? WHERE id = ?", (today, h["id"]))


async def _expiry_alerts(bot: Bot, now: datetime) -> None:
    """Продукты, срок которых истекает в ближайшие 2 дня, + рецепт «спасения»."""
    limit = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    for p in store.expiring_products(limit):
        try:
            text = f"⚠️ «{p['product']}» испортится до {p['expires_at'][8:10]}.{p['expires_at'][5:7]}!"
            try:
                recipe = await llm(
                    [{"role": "user", "content": SAVE_PRODUCT_PROMPT.format(product=p["product"])}]
                )
                text += f"\n\n{recipe}"
            except Exception:
                log.warning("save-product recipe failed, sending plain alert")
            await send_pretty(bot, p["chat_id"], text, EXPIRING_IMG, parse_mode=None)
        except Exception:
            log.exception("expiry alert failed chat=%s", p["chat_id"])
        finally:
            store.mark_warned(p["id"])


async def background_loop(bot: Bot) -> None:
    tick = 0
    while True:
        try:
            now = datetime.now(TZ)
            now_str = now.strftime("%Y-%m-%dT%H:%M")
            today = now.strftime("%Y-%m-%d")
            hhmm = now.strftime("%H:%M")

            await _task_reminders(bot, now_str)
            await _morning_digests(bot, hhmm, today)
            await _evening_digests(bot, hhmm, today)
            await _habit_reminders(bot, hhmm, today)
            # сроки годности проверяем раз в час, чтобы не спамить LLM
            if now.minute == 0 or now.strftime("%H:%M") == "10:00":
                await _expiry_alerts(bot, now)
            # синхронизация с Notion — раз в ~2 минуты (каждый 6-й тик)
            if notion.enabled() and tick % 6 == 0:
                await notion.sync()
        except Exception:
            log.exception("background loop error")
        tick += 1
        await asyncio.sleep(20)
