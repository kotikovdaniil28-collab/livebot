"""Базовые команды: /start, /help, настройки, дайджесты."""

import re

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

import db as store
from handlers.keyboards import main_menu
from handlers.life import mood_keyboard
from services import build_digest, build_evening, get_weather

router = Router()

HELP_TEXT = (
    "🤖 Я — Личный Ассистент Дня. Что умею:\n\n"
    "📝 Задачи — «напомни завтра в 9 позвонить маме»\n"
    "📸 Фото холодильника — скажу, что приготовить\n"
    "🍳 Список продуктов текстом — подберу рецепты\n"
    "🛒 «купить молоко и хлеб» — добавлю в список покупок\n"
    "💸 «кофе 250» — запишу трату\n"
    "🧊 «в холодильнике курица до 25.07» — запомню срок годности\n\n"
    "Команды:\n"
    "/tasks — задачи • /done N — закрыть задачу\n"
    "/list — список покупок • /bought N — вычеркнуть • /clearlist — очистить\n"
    "/share — код списка • /join КОД — общий список с близким\n"
    "/spent — расходы за день и неделю\n"
    "/habit НАЗВАНИЕ [ЧЧ:ММ] — привычка • /habits — отметить\n"
    "/fridge — виртуальный холодильник\n"
    "/menu — меню на неделю + список покупок\n"
    "/digest — утренний брифинг сейчас • /evening — итог дня сейчас\n"
    "/city ГОРОД — погода • /time ЧЧ:ММ — время брифинга\n"
    "/eveningtime ЧЧ:ММ — время вечернего итога\n"
    "/help — эта справка"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer(
        "Привет! 👋\n\n" + HELP_TEXT + "\n\n"
        "Начни с настройки: отправь /city Москва и /time 07:30",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("city"))
async def cmd_city(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    if not command.args:
        await message.answer("Напиши город после команды, например: /city Москва")
        return
    city = command.args.strip()
    store.set_user(message.chat.id, "city", city)
    weather = await get_weather(city)
    extra = f"\n🌤 Сейчас: {weather}" if weather else ""
    await message.answer(f"Город сохранён: {city} ✅{extra}")


TIME_RE = r"([01]?\d|2[0-3]):[0-5]\d"


def normalize_time(args: str) -> str | None:
    args = args.strip()
    if not re.fullmatch(TIME_RE, args):
        return None
    hh, mm = args.split(":")
    return f"{int(hh):02d}:{mm}"


@router.message(Command("time"))
async def cmd_time(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    t = normalize_time(command.args or "")
    if not t:
        await message.answer("Формат: /time 07:30")
        return
    store.set_user(message.chat.id, "brief_time", t)
    await message.answer(f"Утренний брифинг будет приходить в {t} ✅")


@router.message(Command("eveningtime"))
async def cmd_eveningtime(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    t = normalize_time(command.args or "")
    if not t:
        await message.answer("Формат: /eveningtime 21:00")
        return
    store.set_user(message.chat.id, "evening_time", t)
    await message.answer(f"Вечерний итог будет приходить в {t} ✅")


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer(await build_digest(message.chat.id))


@router.message(Command("evening"))
async def cmd_evening(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer(build_evening(message.chat.id), reply_markup=mood_keyboard())
