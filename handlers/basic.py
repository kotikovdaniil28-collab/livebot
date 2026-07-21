"""Базовые команды: /start, /help, настройки, дайджесты."""

import re
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import db as store
from config import WEBAPP_URL
from handlers.keyboards import main_menu
from handlers.life import mood_keyboard
from services import build_digest, build_evening, get_weather

router = Router()

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
WELCOME_IMG = ASSETS_DIR / "welcome.png"
FEATURES_IMG = ASSETS_DIR / "features.png"

HELP_TEXT = (
    "💬 <b>Просто пиши обычными словами — я пойму</b>\n"
    "\n"
    "📌 <i>«напомни завтра в 9 позвонить маме»</i>\n"
    "🛒 <i>«купить молоко и хлеб»</i>\n"
    "💸 <i>«кофе 250»</i> — запишу трату\n"
    "🧊 <i>«в холодильнике курица до 25.07»</i>\n"
    "👨‍🍳 <i>«курица, картошка»</i> или 📸 фото еды — подберу рецепты\n"
    "\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    "\n"
    "⌨️ <b>Кнопки внизу</b> — задачи, покупки, холодильник, привычки, расходы и меню недели\n"
    "\n"
    "⚙️ <b>Настройка</b>\n"
    "• /city Москва — погода в брифинге\n"
    "• /time 07:30 — утренний брифинг\n"
    "• /eveningtime 21:00 — вечерний итог\n"
    "\n"
    "✨ <b>Ещё</b>\n"
    "• /share — общий список покупок на двоих\n"
    "• /mood — дневник настроения\n"
    "• /app — мини-апп со всем на одном экране"
)

WELCOME_CAPTION = (
    "👋 <b>Привет! Я — твой Личный Ассистент Дня</b>\n"
    "\n"
    "Помогу ничего не забыть: задачи, покупки, рецепты,\n"
    "расходы и привычки — всё в одном чате.\n"
    "\n"
    "🌅 Утром — брифинг с погодой и планами\n"
    "🌙 Вечером — итоги дня и оценка настроения"
)


def _webapp_kb() -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть мини-апп", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    store.upsert_user(message.chat.id)
    if WELCOME_IMG.exists():
        await message.answer_photo(
            FSInputFile(WELCOME_IMG),
            caption=WELCOME_CAPTION,
            parse_mode="HTML",
            reply_markup=_webapp_kb(),
        )
    else:
        await message.answer(WELCOME_CAPTION, parse_mode="HTML", reply_markup=_webapp_kb())
    await message.answer(HELP_TEXT, reply_markup=main_menu(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu(), parse_mode="HTML")


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    if not WEBAPP_URL:
        await message.answer(
            "Мини-апп не настроен: задай WEBAPP_URL в .env (публичный HTTPS-адрес сервера бота)."
        )
        return
    await message.answer(
        "<b>Мини-апп</b> — всё в одном экране: задачи, покупки, холодильник, привычки и расходы.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Открыть мини-апп", web_app=WebAppInfo(url=WEBAPP_URL))]
            ]
        ),
    )


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
