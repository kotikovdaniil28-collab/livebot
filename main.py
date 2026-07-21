"""
Бот «Личный Ассистент Дня» — MVP

Функции:
- Задачи и напоминания свободной фразой («напомни завтра в 9 позвонить маме»)
- Фото холодильника → список продуктов + 2–3 рецепта (LLM vision)
- Список продуктов текстом → рецепты
- Утренний брифинг: погода + задачи (время настраивается: /time 07:30)

Запуск: python3 main.py (предварительно заполни .env — см. README.md)
"""

import asyncio
import base64
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# AgentRouter — OpenAI-совместимый шлюз (agentrouter.org). Тот же формат API, другой base URL.
LLM_API_KEY = os.getenv("AGENTROUTER_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://agentrouter.org/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
DB_PATH = os.getenv("DB_PATH", "assistant.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

router = Router()

# ---------------------------------------------------------------------------
# База данных (SQLite — простой файл рядом с ботом)
# ---------------------------------------------------------------------------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                city TEXT DEFAULT '',
                brief_time TEXT DEFAULT '07:30',
                last_digest TEXT DEFAULT ''
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT,
                reminded INTEGER DEFAULT 0,
                done INTEGER DEFAULT 0,
                created_at TEXT
            )"""
        )


def upsert_user(chat_id: int) -> None:
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))


def set_user(chat_id: int, field: str, value: str) -> None:
    assert field in {"city", "brief_time", "last_digest"}
    with db() as c:
        c.execute(f"UPDATE users SET {field} = ? WHERE chat_id = ?", (value, chat_id))


def get_user(chat_id: int) -> sqlite3.Row | None:
    with db() as c:
        return c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()


def add_task(chat_id: int, text: str, remind_at: str | None) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks (chat_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, text, remind_at, datetime.now(TZ).isoformat(timespec="minutes")),
        )
        return cur.lastrowid


def open_tasks(chat_id: int) -> list[sqlite3.Row]:
    with db() as c:
        return c.execute(
            "SELECT * FROM tasks WHERE chat_id = ? AND done = 0 ORDER BY id", (chat_id,)
        ).fetchall()


# ---------------------------------------------------------------------------
# LLM (AgentRouter, OpenAI-совместимый эндпоинт /v1/chat/completions)
# Важно: gpt-5 — рассуждающая модель, параметр temperature она не
# поддерживает (кроме значения по умолчанию), поэтому мы его не отправляем.
# ---------------------------------------------------------------------------

async def llm(messages: list[dict]) -> str:
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    payload = {"model": LLM_MODEL, "messages": messages}
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers
        ) as resp:
            data = await resp.json()
    if "error" in data:
        raise RuntimeError(f"LLM error: {data['error'].get('message', data['error'])}")
    return data["choices"][0]["message"]["content"]


def parse_llm_json(text: str) -> dict:
    """Вытаскивает JSON из ответа модели (убирает ```-обёртки)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(match.group(0) if match else text)


ROUTER_PROMPT = """Ты — модуль-маршрутизатор Telegram-бота «Личный Ассистент Дня».
Сейчас: {now} ({tz}).
Определи намерение пользователя и верни СТРОГО один JSON без пояснений:
{{"intent": "task" | "recipes" | "chat",
  "task_text": "суть задачи без слов 'напомни' и без времени" | null,
  "remind_at": "YYYY-MM-DDTHH:MM" | null,
  "products": ["продукт", ...] | null,
  "reply": "ответ, если intent=chat" | null}}
Правила:
- "task": пользователь хочет добавить дело или напоминание. Если указано время — заполни remind_at в будущем относительно текущего момента («завтра в 9», «через 20 минут»).
- "recipes": пользователь перечисляет продукты или спрашивает, что приготовить. Заполни products.
- "chat": всё остальное — дай короткий полезный ответ на русском в поле reply."""

RECIPES_PROMPT = (
    "Ты — опытный повар. Из этих продуктов предложи 2–3 рецепта: {products}.\n"
    "Для каждого: название с эмодзи, время готовки, короткие пошаговые инструкции.\n"
    "Используй в основном указанные продукты + базовые (соль, масло, специи).\n"
    "Если чего-то не хватает — добавь в конце блок «🛒 Докупить». Отвечай компактно, по-русски."
)

VISION_PROMPT = (
    "На фото — продукты (холодильник, стол или пакеты с едой).\n"
    "1) Перечисли распознанные продукты списком (начни с '🧊 Вижу:').\n"
    "2) Предложи 2–3 рецепта из них: название, время, короткие шаги.\n"
    "3) Если на фото нет еды — так и скажи. Отвечай по-русски, компактно, с эмодзи."
)


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
# Утренний брифинг
# ---------------------------------------------------------------------------

async def build_digest(chat_id: int) -> str:
    user = get_user(chat_id)
    lines = [f"☀️ Доброе утро! Сегодня {datetime.now(TZ).strftime('%d.%m.%Y')}"]
    if user and user["city"]:
        weather = await get_weather(user["city"])
        if weather:
            lines.append(f"🌤 {user['city']}: {weather}")
    tasks = open_tasks(chat_id)
    if tasks:
        lines.append("\n📋 Задачи:")
        for t in tasks:
            when = f" ⏰ {t['remind_at'][11:16]}" if t["remind_at"] else ""
            lines.append(f"  {t['id']}. {t['text']}{when}")
    else:
        lines.append("\n📋 Открытых задач нет — свободный день!")
    lines.append("\nХорошего дня! 🚀")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Хендлеры команд
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🤖 Я — Личный Ассистент Дня. Что умею:\n\n"
    "📝 Задачи — просто напиши: «напомни завтра в 9 позвонить маме»\n"
    "📸 Фото холодильника — скажу, что приготовить\n"
    "🍳 Список продуктов текстом — подберу рецепты\n\n"
    "Команды:\n"
    "/tasks — список задач\n"
    "/done НОМЕР — отметить выполненной\n"
    "/city ГОРОД — город для погоды\n"
    "/time ЧЧ:ММ — время утреннего брифинга\n"
    "/digest — показать брифинг прямо сейчас\n"
    "/help — эта справка"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    upsert_user(message.chat.id)
    await message.answer(
        "Привет! 👋\n\n" + HELP_TEXT + "\n\n"
        "Начни с настройки: отправь /city Москва и /time 07:30"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("city"))
async def cmd_city(message: Message, command: CommandObject) -> None:
    upsert_user(message.chat.id)
    if not command.args:
        await message.answer("Напиши город после команды, например: /city Москва")
        return
    city = command.args.strip()
    set_user(message.chat.id, "city", city)
    weather = await get_weather(city)
    extra = f"\n🌤 Сейчас: {weather}" if weather else ""
    await message.answer(f"Город сохранён: {city} ✅{extra}")


@router.message(Command("time"))
async def cmd_time(message: Message, command: CommandObject) -> None:
    upsert_user(message.chat.id)
    args = (command.args or "").strip()
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", args):
        await message.answer("Формат: /time 07:30")
        return
    hh, mm = args.split(":")
    brief_time = f"{int(hh):02d}:{mm}"
    set_user(message.chat.id, "brief_time", brief_time)
    await message.answer(f"Утренний брифинг будет приходить в {brief_time} ✅")


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    tasks = open_tasks(message.chat.id)
    if not tasks:
        await message.answer("Открытых задач нет 🎉")
        return
    lines = ["📋 Твои задачи:"]
    for t in tasks:
        when = f" ⏰ {t['remind_at'].replace('T', ' ')[:16]}" if t["remind_at"] else ""
        lines.append(f"{t['id']}. {t['text']}{when}")
    lines.append("\nЗакрыть: /done НОМЕР")
    await message.answer("\n".join(lines))


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Укажи номер задачи: /done 3 (список — /tasks)")
        return
    with db() as c:
        cur = c.execute(
            "UPDATE tasks SET done = 1 WHERE id = ? AND chat_id = ? AND done = 0",
            (int(args), message.chat.id),
        )
    if cur.rowcount:
        await message.answer(f"Задача {args} выполнена ✅")
    else:
        await message.answer("Не нашёл такую открытую задачу. Список — /tasks")


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    upsert_user(message.chat.id)
    await message.answer(await build_digest(message.chat.id))


# ---------------------------------------------------------------------------
# Фото → продукты + рецепты
# ---------------------------------------------------------------------------

@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    upsert_user(message.chat.id)
    note = await message.answer("🔍 Смотрю, что у тебя есть...")
    try:
        photo = message.photo[-1]  # самое большое разрешение
        buf = await bot.download(photo)
        image_b64 = base64.b64encode(buf.read()).decode()
        user_text = (message.caption or "").strip()
        content = [
            {"type": "text", "text": VISION_PROMPT + (f"\nКомментарий пользователя: {user_text}" if user_text else "")},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
        answer = await llm([{"role": "user", "content": content}])
        await note.edit_text(answer)
    except Exception as e:
        log.exception("photo handler failed")
        await note.edit_text(f"Не получилось разобрать фото 😢 ({e})")


# ---------------------------------------------------------------------------
# Свободный текст → LLM-роутер (задача / рецепты / болтовня)
# ---------------------------------------------------------------------------

@router.message(F.text)
async def on_text(message: Message) -> None:
    upsert_user(message.chat.id)
    try:
        system = ROUTER_PROMPT.format(
            now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A"), tz=str(TZ)
        )
        raw = await llm(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": message.text},
            ]
        )
        data = parse_llm_json(raw)
    except Exception as e:
        log.exception("router failed")
        await message.answer(f"Что-то пошло не так 😢 ({e})")
        return

    intent = data.get("intent")

    if intent == "task" and data.get("task_text"):
        remind_at = data.get("remind_at")
        task_id = add_task(message.chat.id, data["task_text"], remind_at)
        if remind_at:
            nice = remind_at.replace("T", " в ")
            await message.answer(f"✅ Задача {task_id}: «{data['task_text']}»\n⏰ Напомню {nice}")
        else:
            await message.answer(f"✅ Задача {task_id}: «{data['task_text']}»\nСписок — /tasks")
        return

    if intent == "recipes" and data.get("products"):
        note = await message.answer("👨‍🍳 Подбираю рецепты...")
        try:
            answer = await llm(
                [{"role": "user", "content": RECIPES_PROMPT.format(products=", ".join(data["products"]))}]
            )
            await note.edit_text(answer)
        except Exception as e:
            log.exception("recipes failed")
            await note.edit_text(f"Не получилось подобрать рецепты 😢 ({e})")
        return

    await message.answer(data.get("reply") or "Принял! Чем ещё помочь? 😊")


# ---------------------------------------------------------------------------
# Фоновый цикл: напоминания + утренние брифинги
# ---------------------------------------------------------------------------

async def background_loop(bot: Bot) -> None:
    while True:
        try:
            now = datetime.now(TZ)
            now_str = now.strftime("%Y-%m-%dT%H:%M")
            today = now.strftime("%Y-%m-%d")
            hhmm = now.strftime("%H:%M")

            # Напоминания
            with db() as c:
                due = c.execute(
                    "SELECT * FROM tasks WHERE done = 0 AND reminded = 0 "
                    "AND remind_at IS NOT NULL AND remind_at <= ?",
                    (now_str,),
                ).fetchall()
            for t in due:
                try:
                    await bot.send_message(
                        t["chat_id"],
                        f"⏰ Напоминание: {t['text']}\nВыполнено? → /done {t['id']}",
                    )
                finally:
                    with db() as c:
                        c.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (t["id"],))

            # Утренние брифинги
            with db() as c:
                users = c.execute(
                    "SELECT * FROM users WHERE brief_time = ? AND last_digest != ?",
                    (hhmm, today),
                ).fetchall()
            for u in users:
                try:
                    await bot.send_message(u["chat_id"], await build_digest(u["chat_id"]))
                finally:
                    set_user(u["chat_id"], "last_digest", today)
        except Exception:
            log.exception("background loop error")
        await asyncio.sleep(20)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан BOT_TOKEN — заполни файл .env (см. README.md)")
    if not LLM_API_KEY:
        raise SystemExit("❌ Не задан AGENTROUTER_API_KEY — заполни файл .env (см. README.md)")
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(background_loop(bot))
    log.info("Бот запущен (polling)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
