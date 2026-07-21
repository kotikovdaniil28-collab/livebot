"""Задачи: /tasks, /done + inline-кнопки «выполнено» и «отложить»."""

from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db as store
from handlers.cb_utils import safe_answer
from config import TZ

router = Router()


def reminder_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Кнопки под напоминанием: выполнено / отложить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task:done:{task_id}")],
            [
                InlineKeyboardButton(text="⏰ +30 мин", callback_data=f"task:snooze:{task_id}:30"),
                InlineKeyboardButton(text="⏰ +2 часа", callback_data=f"task:snooze:{task_id}:120"),
                InlineKeyboardButton(text="⏰ Завтра", callback_data=f"task:snooze:{task_id}:1440"),
            ],
        ]
    )


def tasks_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    tasks = store.open_tasks(chat_id)
    if not tasks:
        return "📋 Открытых задач нет 🎉", None
    lines = ["<b>📋 Задачи</b>"]
    for t in tasks:
        when = f"  <i>⏰ {t['remind_at'][5:16].replace('-', '.').replace('T', ' ')}</i>" if t["remind_at"] else ""
        lines.append(f"{t['id']}. {escape(t['text'])}{when}")
    # компактные кнопки-номера, до 4 в ряд
    btns = [
        InlineKeyboardButton(text=f"✅ {t['id']}", callback_data=f"task:done:{t['id']}")
        for t in tasks
    ]
    rows = [btns[i : i + 4] for i in range(0, len(btns), 4)]
    lines.append("\n<i>Нажми номер, чтобы закрыть</i>")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    text, kb = tasks_view(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("task:done:"))
async def cb_task_done(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":")[2])
    if not store.complete_task(callback.message.chat.id, task_id):
        await safe_answer(callback, "Задача уже закрыта", show_alert=True)
        return
    await safe_answer(callback, "Выполнено ✅")
    text, kb = tasks_view(callback.message.chat.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("task:snooze:"))
async def cb_task_snooze(callback: CallbackQuery) -> None:
    _, _, task_id, minutes = callback.data.split(":")
    new_time = datetime.now(TZ) + timedelta(minutes=int(minutes))
    new_str = new_time.strftime("%Y-%m-%dT%H:%M")
    with store.db() as c:
        cur = c.execute(
            "UPDATE tasks SET remind_at = ?, reminded = 0 WHERE id = ? AND chat_id = ? AND done = 0",
            (new_str, int(task_id), callback.message.chat.id),
        )
    if cur.rowcount == 0:
        await safe_answer(callback, "Задача уже закрыта", show_alert=True)
        return
    human = new_time.strftime("%d.%m %H:%M")
    await safe_answer(callback, f"Отложено до {human} ⏰")
    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n⏰ Отложено до {human}", reply_markup=None
        )
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
