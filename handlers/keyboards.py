"""Общие клавиатуры: главное reply-меню."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_TASKS = "📋 Задачи"
BTN_LIST = "🛒 Покупки"
BTN_FRIDGE = "🧊 Холодильник"
BTN_MORE = "➕ Ещё"
# Старые кнопки — оставляем обработчики, у кого-то они ещё на экране
BTN_HABITS = "🔁 Привычки"
BTN_SPENT = "💸 Расходы"
BTN_MENU = "📅 Меню недели"
BTN_DIGEST = "☀️ Брифинг"
BTN_EVENING = "🌙 Итог дня"
BTN_HELP = "❓ Помощь"

MENU_BUTTONS = {
    BTN_TASKS, BTN_LIST, BTN_FRIDGE, BTN_MORE, BTN_HABITS,
    BTN_SPENT, BTN_MENU, BTN_DIGEST, BTN_EVENING, BTN_HELP,
}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TASKS), KeyboardButton(text=BTN_LIST)],
            [KeyboardButton(text=BTN_FRIDGE), KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши задачу, трату или продукты…",
    )
