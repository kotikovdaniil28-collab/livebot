"""Базовые команды: /start, /help, настройки, дайджесты."""

import re
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import FSInputFile, Message

import db as store
from handlers.keyboards import main_menu
from handlers.life import mood_keyboard
from services import build_digest, build_evening, get_weather

router = Router()

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
WELCOME_IMG = ASSETS_DIR / "welcome.png"
FEATURES_IMG = ASSETS_DIR / "features.png"

HELP_TEXT = (
    "<b>Пиши обычными словами — я пойму:</b>\n"
    "• «напомни завтра в 9 позвонить маме»\n"
    "• «купить молоко и хлеб»\n"
    "• «кофе 250» — запишу трату\n"
    "• «в холодильнике курица до 25.07»\n"
    "• «курица, картошка» или 📸 фото еды — подберу рецепты\n\n"
    "<b>Кнопки внизу</b> — задачи, покупки, холодильник и всё остальное.\n\n"
    "<b>Настройка:</b> /city Москва — погода, /time 07:30 — утренний брифинг, "
    "/eveningtime 21:00 — вечерний итог\n"
    "<b>Ещё:</b> /share — общий список покупок на двоих, /mood — дневник настроения"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    store.upsert_user(message.chat.id)
    if WELCOME_IMG.exists():
        await message.answer_photo(
            FSInputFile(WELCOME_IMG),
            caption="<b>Привет! Я — Личный Ассистент Дня</b> 👋",
            parse_mode="HTML",
        )
    await message.answer(HELP_TEXT, reply_markup=main_menu(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu(), parse_mode="HTML")


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
    await message.answer(await build_digest(message.chat.id), parse_mode="HTML")


@router.message(Command("evening"))
async def cmd_evening(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer(
        build_evening(message.chat.id), reply_markup=mood_keyboard(), parse_mode="HTML"
    )
