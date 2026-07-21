"""Обработка кнопок главного reply-меню. Регистрируется ДО text_router."""

from aiogram import F, Router
from aiogram.types import Message

import db as store
from handlers import kitchen, life, shopping, tasks
from handlers.keyboards import (
    BTN_DIGEST,
    BTN_EVENING,
    BTN_FRIDGE,
    BTN_HABITS,
    BTN_HELP,
    BTN_LIST,
    BTN_MENU,
    BTN_SPENT,
    BTN_TASKS,
)
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
    await message.answer(await build_digest(message.chat.id), parse_mode="HTML")


@router.message(F.text == BTN_EVENING)
async def menu_evening(message: Message) -> None:
    store.upsert_user(message.chat.id)
    await message.answer(
        build_evening(message.chat.id), reply_markup=life.mood_keyboard(), parse_mode="HTML"
    )


@router.message(F.text == BTN_HELP)
async def menu_help(message: Message) -> None:
    from handlers.basic import HELP_TEXT

    await message.answer(HELP_TEXT, parse_mode="HTML")
