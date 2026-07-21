"""Внешние сервисы и сборка дайджестов: погода, новости, утро/вечер."""

import logging
import re
from datetime import datetime, timedelta

import aiohttp

import db as store
from config import NEWS_COUNT, NEWS_RSS_URL, OPENWEATHER_API_KEY, TZ

log = logging.getLogger("bot.services")


# ---------------------------------------------------------------------------
# Погода (OpenWeatherMap)
# ---------------------------------------------------------------------------

async def get_weather(city: str) -> str | None:
    if not (OPENWEATHER_API_KEY and city):
        return None
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "ru"}
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.openweathermap.org/data/2.5/weather", params=params
            ) as resp:
                data = await resp.json()
        if int(data.get("cod", 0)) != 200:
            return None
        desc = data["weather"][0]["description"].capitalize()
        temp = round(data["main"]["temp"])
        feels = round(data["main"]["feels_like"])
        return f"{desc}, {temp:+d}°C (ощущается {feels:+d}°C)"
    except Exception as e:  # не роняем бота из-за погоды
        log.warning("weather failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Новости (простой парсинг RSS без сторонних библиотек)
# ---------------------------------------------------------------------------

async def get_news() -> list[str]:
    if not NEWS_RSS_URL:
        return []
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(NEWS_RSS_URL) as resp:
                xml = await resp.text()
        titles = re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", xml, flags=re.S)
        return [t.strip() for t in titles[:NEWS_COUNT] if t.strip()]
    except Exception as e:
        log.warning("news failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Утренний брифинг
# ---------------------------------------------------------------------------

async def build_digest(chat_id: int) -> str:
    user = store.get_user(chat_id)
    now = datetime.now(TZ)
    lines = [f"☀️ Доброе утро! Сегодня {now.strftime('%d.%m.%Y')}"]

    if user and user["city"]:
        weather = await get_weather(user["city"])
        if weather:
            lines.append(f"🌤 {user['city']}: {weather}")

    tasks = store.open_tasks(chat_id)
    if tasks:
        lines.append("\n📋 Задачи:")
        for t in tasks:
            when = f" ⏰ {t['remind_at'][11:16]}" if t["remind_at"] else ""
            lines.append(f"  {t['id']}. {t['text']}{when}")
    else:
        lines.append("\n📋 Открытых задач нет — свободный день!")

    # продукты, у которых скоро выйдет срок годности (2 дня вперёд)
    limit = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    expiring = [
        p for p in store.get_fridge(chat_id)
        if p["expires_at"] and p["expires_at"] <= limit
    ]
    if expiring:
        lines.append("\n⚠️ Скоро испортится:")
        for p in expiring:
            lines.append(f"  • {p['product']} (до {p['expires_at'][8:10]}.{p['expires_at'][5:7]})")

    shopping = store.get_shopping(chat_id)
    if shopping:
        lines.append(f"\n🛒 В списке покупок: {len(shopping)} поз. — /list")

    news = await get_news()
    if news:
        lines.append("\n📰 Новости:")
        for n in news:
            lines.append(f"  • {n}")

    lines.append("\nХорошего дня! 🚀")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Вечерний итог
# ---------------------------------------------------------------------------

def build_evening(chat_id: int) -> str:
    lines = ["🌙 Итог дня"]

    done = store.tasks_done_today(chat_id)
    open_ = store.open_tasks(chat_id)
    if done:
        lines.append(f"\n✅ Выполнено сегодня: {len(done)}")
        for t in done:
            lines.append(f"  • {t['text']}")
    if open_:
        lines.append(f"\n📋 Переносится на завтра: {len(open_)}")
        for t in open_:
            lines.append(f"  • {t['text']}")
    if not done and not open_:
        lines.append("\nЗадач сегодня не было — день без суеты 🙂")

    habits = store.get_habits(chat_id)
    if habits:
        done_h = [h for h in habits if store.habit_done_today(h["id"])]
        lines.append(f"\n🔁 Привычки: {len(done_h)}/{len(habits)} за сегодня")

    expenses = store.expenses_since(chat_id, store.today())
    if expenses:
        total = sum(e["amount"] for e in expenses)
        lines.append(f"\n💸 Потрачено сегодня: {total:g} — /spent")

    lines.append("\nКак прошёл день? Оцени от 1 до 10 👇")
    return "\n".join(lines)
