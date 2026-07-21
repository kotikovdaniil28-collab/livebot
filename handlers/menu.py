"""Обработка кнопок главного reply-меню. Регистрируется ДО text_router."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db as store
from handlers import kitchen, life, shopping, tasks
from handlers.cb_utils import safe_answer
from handlers.keyboards import (
    BTN_DIGEST,
    BTN_EVENING,
    BTN_FRIDGE,
    BTN_HABITS,
    BTN_HELP,
    BTN_LIST,
    BTN_MENU,
    BTN_MORE,
    BTN_SPENT,
    BTN_TASKS,
)
from handlers.ui import EVENING_IMG, MORNING_IMG, answer_pretty
from services import build_digest, build_evening

router = Router()


@router.message(F.text == BTN_TASKS)
async def menu_tasks(message: Message) -> None:
    store.upsert_user(message.chat.id)
    text, kb = tasks.tasks_view(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == BTN_LIST)
async def menu_list(message: Message) -> None:
    store.upsert_user(message.chat.id)
    text, kb = shopping.shopping_view(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == BTN_FRIDGE)
async def menu_fridge(message: Message) -> None:
    store.upsert_user(message.chat.id)
    text, kb = kitchen.fridge_view(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == BTN_HABITS)
async def menu_habits(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await life.cmd_habits(message)


@router.message(F.text == BTN_SPENT)
async def menu_spent(message: Message) -> None:
    await life.cmd_spent(message)


@router.message(F.text == BTN_MENU)
async def menu_weekmenu(message: Message) -> None:
    await kitchen.cmd_menu(message)


@router.message(F.text == BTN_DIGEST)
async def menu_digest(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await answer_pretty(message, await build_digest(message.chat.id), MORNING_IMG)


@router.message(F.text == BTN_EVENING)
async def menu_evening(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await answer_pretty(
        message, build_evening(message.chat.id), EVENING_IMG, life.mood_keyboard()
    )


@router.message(F.text == BTN_HELP)
async def menu_help(message: Message) -> None:
    from handlers.basic import HELP_TEXT

    await message.answer(HELP_TEXT, parse_mode="HTML")


def more_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧊 Холодильник", callback_data="more:fridge"),
                InlineKeyboardButton(text="📅 Меню недели", callback_data="more:weekmenu"),
            ],
            [
                InlineKeyboardButton(text="🔁 Привычки", callback_data="more:habits"),
                InlineKeyboardButton(text="💸 Расходы", callback_data="more:spent"),
            ],
            [
                InlineKeyboardButton(text="☀️ Брифинг", callback_data="more:digest"),
                InlineKeyboardButton(text="🌙 Итог дня", callback_data="more:evening"),
            ],
            [
                InlineKeyboardButton(text="❓ Помощь", callback_data="more:help"),
            ],
        ]
    )


@router.message(F.text == BTN_MORE)
async def menu_more(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer("Что показать?", reply_markup=more_menu())


@router.callback_query(F.data.startswith("more:"))
async def cb_more(callback: CallbackQuery) -> None:
    action = callback.data.split(":", 1)[1]
    await safe_answer(callback)
    msg = callback.message
    if action == "fridge":
        text, kb = kitchen.fridge_view(msg.chat.id)
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")
    elif action == "habits":
        await life.cmd_habits(msg)
    elif action == "spent":
        await life.cmd_spent(msg)
    elif action == "weekmenu":
        await kitchen.cmd_menu(msg)
    elif action == "digest":
        await menu_digest(msg)
    elif action == "evening":
        await menu_evening(msg)
    elif action == "help":
        await menu_help(msg)
