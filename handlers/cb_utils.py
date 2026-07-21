"""Утилиты для callback-обработчиков."""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

log = logging.getLogger("bot.cb")


async def safe_answer(callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Ответ на нажатие кнопки, не падающий на устаревших запросах.

    Telegram даёт ~15 секунд на ответ. Если бот был занят (LLM и т.п.)
    и не успел — «query is too old». Это не ошибка логики: тихо логируем
    и продолжаем работу обработчика.
    """
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            log.info("callback query устарел, пропускаю answer: %s", callback.data)
        else:
            raise
