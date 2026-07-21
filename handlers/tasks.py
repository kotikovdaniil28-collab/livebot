"""Задачи: /tasks, /done + inline-кнопки «выполнено»."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db as store

router = Router()


def _short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tasks_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    tasks = store.open_tasks(chat_id)
    if not tasks:
        return "Открытых задач нет 🎉", None
    lines = ["📋 Твои задачи (нажми кнопку, чтобы закрыть):"]
    rows = []
    for t in tasks:
        when = f" ⏰ {t['remind_at'].replace('T', ' ')[:16]}" if t["remind_at"] else ""
        lines.append(f"{t['id']}. {t['text']}{when}")
        rows.append(
            [InlineKeyboardButton(text=f"✅ {t['id']}. {_short(t['text'])}", callback_data=f"task:done:{t['id']}")]
        )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    text, kb = tasks_view(message.chat.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("task:done:"))
async def cb_task_done(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":")[2])
    if not store.complete_task(callback.message.chat.id, task_id):
        await callback.answer("Задача уже закрыта", show_alert=True)
        return
    await callback.answer("Выполнено ✅")
    text, kb = tasks_view(callback.message.chat.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Укажи номер задачи: /done 3 (список — /tasks)")
        return
    if store.complete_task(message.chat.id, int(args)):
        await message.answer(f"Задача {args} выполнена ✅")
    else:
        await message.answer("Не нашёл такую открытую задачу. Список — /tasks")
