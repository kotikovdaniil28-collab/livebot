"""Задачи: /tasks, /done."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import db as store

router = Router()


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    tasks = store.open_tasks(message.chat.id)
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
    if store.complete_task(message.chat.id, int(args)):
        await message.answer(f"Задача {args} выполнена ✅")
    else:
        await message.answer("Не нашёл такую открытую задачу. Список — /tasks")
