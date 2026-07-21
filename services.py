"""Внешние сервисы и сборка дайджестов: погода, новости, утро/вечер."""

import logging
import re
from datetime import datetime, timedelta
from html import escape

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
# Двойной фильтр: блок-лист по словам + LLM отбирает только добрые новости.
# ---------------------------------------------------------------------------

# Война, политика, криминал, катастрофы — такому не место в милом боте
_NEWS_BLOCKLIST = re.compile(
    r"убил|убий|погиб|смерт|умер|умир|войн|обстрел|ракет|дрон|бпла|взрыв|теракт"
    r"|днр|лнр|сво\b|фронт|мобилиз|санкци|арест|задерж|суд|приговор|тюрьм|колони"
    r"|авари|катастроф|крушени|пожар|наводнени|землетрясени|эпидеми|вирус"
    r"|путин|зеленск|трамп|байден|нато|кремл|минобороны|всу\b|госдум|депутат"
    r"|атак|удар|ранен|жертв|росгвард|уголовн|мошенн|насил|избил|стрельб"
    r"|оруж|бомб|штурм|плен|гранат|мигрант|протест|митинг|оппозици|выбор",
    re.I,
)


def _clean_titles(titles: list[str]) -> list[str]:
    """Убирает дубли и всё, что попало в блок-лист."""
    seen: set[str] = set()
    out = []
    for t in titles:
        t = t.strip()
        if not t or t.lower() in seen or _NEWS_BLOCKLIST.search(t):
            continue
        seen.add(t.lower())
        out.append(t)
    return out


async def _pick_positive(titles: list[str]) -> list[str]:
    """LLM выбирает только добрые/интересные заголовки. При сбое — первые N."""
    from llm import llm, parse_llm_json

    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    prompt = (
        "Вот заголовки новостей:\n" + numbered + "\n\n"
        f"Выбери до {NEWS_COUNT} самых добрых и интересных: наука, космос, "
        "животные, культура, технологии, спорт, забавные истории. "
        "СТРОГО исключи: политику, войну, криминал, катастрофы, смерти, "
        "происшествия, экономические угрозы. Если подходящих нет — пустой список.\n"
        'Ответ СТРОГО одним JSON: {"pick": [номера]}'
    )
    try:
        data = parse_llm_json(await llm([{"role": "user", "content": prompt}]))
        picked = [titles[i] for i in data.get("pick", []) if isinstance(i, int) and 0 <= i < len(titles)]
        return picked[:NEWS_COUNT]
    except Exception as e:
        log.warning("news LLM filter failed: %s", e)
        return titles[:NEWS_COUNT]


async def get_news() -> list[str]:
    if not NEWS_RSS_URL:
        return []
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(NEWS_RSS_URL) as resp:
                xml = await resp.text()
        raw = re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", xml, flags=re.S)
        titles = _clean_titles(raw[:30])
        if not titles:
            return []
        return await _pick_positive(titles)
    except Exception as e:
        log.warning("news failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Утренний брифинг
# ---------------------------------------------------------------------------

async def build_digest(chat_id: int) -> str:
    user = store.get_user(chat_id)
    now = datetime.now(TZ)
    lines = [f"<b>☀️ Доброе утро!</b> {now.strftime('%d.%m')}"]

    if user and user["city"]:
        weather = await get_weather(user["city"])
        if weather:
            lines.append(f"🌤 {escape(weather)}")

    tasks = store.open_tasks(chat_id)
    if tasks:
        lines.append("\n<b>📋 Задачи</b>")
        for t in tasks:
            when = f" <i>⏰ {t['remind_at'][11:16]}</i>" if t["remind_at"] else ""
            lines.append(f"• {escape(t['text'])}{when}")
    else:
        lines.append("\n📋 Задач нет — свободный день!")

    # продукты, у которых скоро выйдет срок годности (2 дня вперёд)
    limit = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    expiring = [
        p for p in store.get_fridge(chat_id)
        if p["expires_at"] and p["expires_at"] <= limit
    ]
    if expiring:
        names = ", ".join(
            f"{escape(p['product'])} (до {p['expires_at'][8:10]}.{p['expires_at'][5:7]})"
            for p in expiring
        )
        lines.append(f"\n⚠️ Скоро испортится: {names}")

    shopping = store.get_shopping(chat_id)
    if shopping:
        lines.append(f"🛒 В списке покупок: {len(shopping)} поз.")

    if user and user["budget"]:
        spent = store.month_spent(chat_id)
        left = user["budget"] - spent
        icon = "💰" if left >= 0 else "🚨"
        lines.append(f"{icon} Бюджет: осталось {left:g} из {user['budget']:g}")

    news = await get_news()
    if news:
        lines.append("\n<b>📰 Новости</b>")
        for n in news:
            lines.append(f"• {escape(n)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Вечерний итог
# ---------------------------------------------------------------------------

def build_evening(chat_id: int) -> str:
    lines = ["<b>🌙 Итог дня</b>"]

    done = store.tasks_done_today(chat_id)
    open_ = store.open_tasks(chat_id)
    if done:
        lines.append(f"\n<b>✅ Выполнено: {len(done)}</b>")
        for t in done:
            lines.append(f"• {escape(t['text'])}")
    if open_:
        lines.append(f"\n<b>📋 На завтра: {len(open_)}</b>")
        for t in open_:
            lines.append(f"• {escape(t['text'])}")
    if not done and not open_:
        lines.append("Задач сегодня не было — день без суеты 🙂")

    habits = store.get_habits(chat_id)
    if habits:
        done_h = sum(1 for h in habits if store.habit_done_today(h["id"]))
        lines.append(f"\n<b>🔁 Привычки: {done_h}/{len(habits)}</b>")
        for h in habits:
            streak = store.habit_streak(h["id"])
            month_done, month_days = store.habit_month_stats(h["id"])
            pct = round(month_done / month_days * 100) if month_days else 0
            mark = "✅" if store.habit_done_today(h["id"]) else "⬜"
            fire = f" 🔥{streak}" if streak >= 2 else ""
            lines.append(f"{mark} {escape(h['name'])}{fire} · {pct}% за месяц")

    extras = []
    expenses = store.expenses_since(chat_id, store.today())
    if expenses:
        extras.append(f"💸 За сегодня: {sum(e['amount'] for e in expenses):g}")
    user = store.get_user(chat_id)
    if user and user["budget"]:
        left = user["budget"] - store.month_spent(chat_id)
        extras.append(f"💰 Остаток бюджета: {left:g}")
    if extras:
        lines.append("\n" + " · ".join(extras))

    lines.append("\n<i>Оцени день от 1 до 10</i> 👇")
    return "\n".join(lines)
