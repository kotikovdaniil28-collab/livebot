"""Общие помощники для «красивых» ответов с тематическими картинками."""

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

log = logging.getLogger("bot.ui")

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

SHOPPING_IMG = ASSETS_DIR / "shopping.png"
TASKS_IMG = ASSETS_DIR / "tasks.png"
HABITS_IMG = ASSETS_DIR / "habits.png"
MOOD_IMG = ASSETS_DIR / "mood.png"
EXPENSES_IMG = ASSETS_DIR / "expenses.png"
MORNING_IMG = ASSETS_DIR / "morning.png"
EVENING_IMG = ASSETS_DIR / "evening.png"
EXPIRING_IMG = ASSETS_DIR / "expiring.png"

# Картинки-настроения бота для живых chat-ответов (ключи = поле "mood" из LLM)
MOOD_IMGS = {
    "laugh": ASSETS_DIR / "mood_laugh.png",
    "cool": ASSETS_DIR / "mood_cool.png",
    "think": ASSETS_DIR / "mood_think.png",
    "shock": ASSETS_DIR / "mood_shock.png",
}

_CAPTION_LIMIT = 1024


async def answer_pretty(
    message: Message,
    text: str,
    img: Path,
    kb: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Ответ картинкой с подписью; при длинном тексте или ошибке — фолбэк на текст."""
    try:
        if img.exists() and len(text) <= _CAPTION_LIMIT:
            await message.answer_photo(
                FSInputFile(img), caption=text, parse_mode=parse_mode, reply_markup=kb
            )
            return
    except Exception:
        log.warning("pretty answer failed, falling back to text", exc_info=True)
    await message.answer(text, reply_markup=kb, parse_mode=parse_mode)


async def send_pretty(
    bot: Bot,
    chat_id: int,
    text: str,
    img: Path,
    kb: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """То же самое, но по chat_id (для шедулера)."""
    try:
        if img.exists() and len(text) <= _CAPTION_LIMIT:
            await bot.send_photo(
                chat_id, FSInputFile(img), caption=text, parse_mode=parse_mode, reply_markup=kb
            )
            return
    except Exception:
        log.warning("pretty send failed, falling back to text", exc_info=True)
    await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=parse_mode)


async def edit_view(
    msg: Message,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Обновление списка под callback: у фото правим подпись, у текста — текст."""
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass  # сообщение не изменилось или уже удалено
