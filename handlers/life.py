"""Привычки, расходы и дневник настроения."""

import re
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db as store
from handlers.cb_utils import safe_answer
from config import TZ

router = Router()


# --- привычки -----------------------------------------------------------------

def habits_keyboard(chat_id: int) -> InlineKeyboardMarkup | None:
    habits = store.get_habits(chat_id)
    if not habits:
        return None
    rows = []
    for h in habits:
        mark = "✅" if store.habit_done_today(h["id"]) else "⬜"
        streak = store.habit_streak(h["id"])
        fire = f" · 🔥{streak}" if streak else ""
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {h['name']}{fire}", callback_data=f"habit:{h['id']}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("habit"))
async def cmd_habit(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Добавить привычку: /habit Пить воду 10:00 (время напоминания — по желанию)\n"
            "Отметить за сегодня: /habits\nУдалить: /delhabit НОМЕР"
        )
        return
    m = re.search(r"\s([01]?\d|2[0-3]):([0-5]\d)\s*$", args)
    remind_time = ""
    name = args
    if m:
        remind_time = f"{int(m.group(1)):02d}:{m.group(2)}"
        name = args[: m.start()].strip()
    if not name:
        await message.answer("Формат: /habit Пить воду 10:00")
        return
    store.add_habit(message.chat.id, name, remind_time)
    extra = f" (напомню в {remind_time})" if remind_time else ""
    await message.answer(f"Привычка «{name}» добавлена ✅{extra}\nОтмечать: /habits")


@router.message(Command("habits"))
async def cmd_habits(message: Message) -> None:
    store.upsert_user(message.chat.id)
    kb = habits_keyboard(message.chat.id)
    if not kb:
        await message.answer("🔁 Привычек пока нет. Добавь: /habit Пить воду 10:00")
        return
    habits = store.get_habits(message.chat.id)
    done = sum(1 for h in habits if store.habit_done_today(h["id"]))
    await message.answer(
        f"<b>🔁 Привычки</b> — {done}/{len(habits)} за сегодня\n"
        "<i>Нажми, чтобы отметить · удалить: /delhabit N</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.message(Command("delhabit"))
async def cmd_delhabit(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Укажи номер привычки: /delhabit 2 (список — /habits)")
        return
    if store.delete_habit(message.chat.id, int(args)):
        await message.answer(f"Привычка {args} удалена ✅")
    else:
        await message.answer("Не нашёл такую привычку. Список — /habits")


@router.callback_query(F.data.startswith("habit:"))
async def cb_habit(callback: CallbackQuery) -> None:
    habit_id = int(callback.data.split(":")[1])
    if not store.log_habit(callback.message.chat.id, habit_id):
        await safe_answer(callback, "Привычка не найдена", show_alert=True)
        return
    await safe_answer(callback, "Отмечено ✅")
    kb = habits_keyboard(callback.message.chat.id)
    if kb:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass  # разметка не изменилась


# --- расходы -------------------------------------------------------------------

@router.message(Command("spent"))
async def cmd_spent(message: Message) -> None:
    store.upsert_user(message.chat.id)
    now = datetime.now(TZ)
    week_ago = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    rows = store.expenses_since(message.chat.id, week_ago)
    if not rows:
        await message.answer("Трат за неделю нет. Записать: просто напиши «кофе 250»")
        return
    today = store.today()
    today_rows = [r for r in rows if r["date"] == today]
    lines = ["<b>💸 Расходы</b>"]
    if today_rows:
        for r in today_rows:
            lines.append(f"• {escape(r['item'])} — <b>{r['amount']:g}</b>")
        lines.append(f"\nСегодня: <b>{sum(r['amount'] for r in today_rows):g}</b>")
    lines.append(f"За 7 дней: <b>{sum(r['amount'] for r in rows):g}</b> · {len(rows)} записей")
    await message.answer("\n".join(lines), parse_mode="HTML")


# --- дневник настроения ----------------------------------------------------------

def mood_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(i), callback_data=f"mood:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(text=str(i), callback_data=f"mood:{i}") for i in range(6, 11)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


MOOD_REPLIES = {
    range(1, 4): "Сочувствую, день был тяжёлым 😔 Завтра будет лучше!",
    range(4, 7): "Нормальный день. Отдохни как следует 🙂",
    range(7, 11): "Отличный день! Так держать 🎉",
}


@router.callback_query(F.data.startswith("mood:"))
async def cb_mood(callback: CallbackQuery) -> None:
    score = int(callback.data.split(":")[1])
    store.set_mood(callback.message.chat.id, score)
    reply = next((v for k, v in MOOD_REPLIES.items() if score in k), "Записал!")
    await safe_answer(callback, f"Оценка {score}/10 записана")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"📔 {score}/10 — {reply}")


@router.message(Command("mood"))
async def cmd_mood(message: Message) -> None:
    store.upsert_user(message.chat.id)
    moods = store.recent_moods(message.chat.id)
    if not moods:
        await message.answer(
            "Записей пока нет. Вечером я пришлю итог дня и попрошу оценку 1–10.\n"
            "Или вызови /evening прямо сейчас."
        )
        return
    lines = ["<b>📔 Дневник настроения</b>"]
    for m in moods:
        score = f"{m['score']}/10" if m["score"] else "—"
        lines.append(f"{m['date'][8:10]}.{m['date'][5:7]} — {score}")
    avg = [m["score"] for m in moods if m["score"]]
    if avg:
        lines.append(f"\nВ среднем: <b>{sum(avg) / len(avg):.1f}/10</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")
