"""Свободный текст и голосовые → LLM-роутер (задача / рецепты / трата / покупки /
холодильник / выполнение и удаление / болтовня) с памятью диалога.

Важно: этот роутер регистрируется ПОСЛЕДНИМ, чтобы не перехватывать команды.
"""

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import Message

import db as store
from config import TZ
from handlers.kitchen import send_recipes
from llm import LLMRateLimitError, ROUTER_PROMPT, llm, parse_llm_json, transcribe

log = logging.getLogger("bot.text")

router = Router()


def _fmt_rows(rows, key: str) -> str:
    if not rows:
        return "пусто"
    return "; ".join(f"{r['id']}: {r[key]}" for r in rows[:30])


async def process_text(message: Message, text: str) -> None:
    """Обрабатывает текст (набранный или распознанный из голоса)."""
    chat_id = message.chat.id
    store.upsert_user(chat_id)
    try:
        system = ROUTER_PROMPT.format(
            now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A"),
            tz=str(TZ),
            tasks=_fmt_rows(store.open_tasks(chat_id), "text"),
            shopping=_fmt_rows(store.get_shopping(chat_id), "item"),
        )
        messages = [{"role": "system", "content": system}]
        messages += store.get_history(chat_id, limit=8)
        messages.append({"role": "user", "content": text})
        raw = await llm(messages)
        data = parse_llm_json(raw)
    except LLMRateLimitError:
        await message.answer(
            "⏳ <b>Слишком много запросов подряд</b>\n\n"
            "Бесплатный лимит запросов на минуту исчерпан.\n"
            "Подожди минутку и напиши ещё раз 🙏",
            parse_mode="HTML",
        )
        return
    except Exception:
        log.exception("router failed")
        await message.answer(
            "😔 <b>Не получилось обработать сообщение</b>\n\n"
            "Попробуй ещё раз чуть позже — или воспользуйся кнопками меню внизу.",
            parse_mode="HTML",
        )
        return

    intent = data.get("intent")
    store.add_history(chat_id, "user", text)

    def remember(reply: str) -> None:
        store.add_history(chat_id, "assistant", reply)

    # --- задача / напоминание ---
    if intent == "task" and data.get("task_text"):
        remind_at = data.get("remind_at")
        repeat = data.get("repeat") or ""
        if repeat not in ("daily", "weekly", "monthly"):
            repeat = ""
        store.add_task(chat_id, data["task_text"], remind_at, repeat)
        rep_note = {"daily": " · каждый день", "weekly": " · каждую неделю", "monthly": " · каждый месяц"}.get(repeat, "")
        if remind_at:
            nice = f"{remind_at[8:10]}.{remind_at[5:7]} в {remind_at[11:16]}"
            reply = f"✅ «{data['task_text']}» — напомню {nice}{rep_note}"
        else:
            reply = f"✅ «{data['task_text']}» — записал{rep_note}"
        remember(reply)
        await message.answer(reply)
        return

    # --- задача выполнена (текстом) ---
    if intent == "complete_task" and data.get("task_id"):
        try:
            tid = int(data["task_id"])
        except (TypeError, ValueError):
            tid = 0
        row = next((t for t in store.open_tasks(chat_id) if t["id"] == tid), None)
        if row and store.complete_task(chat_id, tid):
            reply = f"🎉 «{row['text']}» — выполнено!"
            remember(reply)
            await message.answer(reply)
            return

    # --- удалить задачу (текстом) ---
    if intent == "delete_task" and data.get("task_id"):
        try:
            tid = int(data["task_id"])
        except (TypeError, ValueError):
            tid = 0
        row = next((t for t in store.open_tasks(chat_id) if t["id"] == tid), None)
        if row and store.delete_task(chat_id, tid):
            reply = f"🗑 «{row['text']}» — удалил"
            remember(reply)
            await message.answer(reply)
            return

    # --- рецепты ---
    if intent == "recipes":
        products = data.get("products") or []
        if not products:
            products = [i["product"] for i in store.get_fridge(chat_id)]
        if products:
            remember(f"[подобрал рецепты из: {', '.join(products)}]")
            await send_recipes(message, products)
            return
        reply = (
            "Напиши, какие продукты есть (например: «курица, картошка, сметана»), "
            "или пришли фото холодильника 📸"
        )
        remember(reply)
        await message.answer(reply)
        return

    # --- трата ---
    if intent == "expense" and data.get("expense_amount"):
        item = (data.get("expense_item") or "покупка").strip()
        amount = float(data["expense_amount"])
        store.add_expense(chat_id, item, amount)
        rows = store.expenses_since(chat_id, store.today())
        total = sum(r["amount"] for r in rows)
        reply = f"💸 {item} — {amount:g} · за сегодня: {total:g}"
        # проверка бюджета месяца
        user = store.get_user(chat_id)
        budget = user["budget"] if user else 0
        if budget:
            spent = store.month_spent(chat_id)
            month_key = store.today()[:7]
            warned = user["budget_warned"] or ""
            if spent >= budget and warned != month_key + ":100":
                reply += f"\n\n🚨 Бюджет месяца превышен: {spent:g} из {budget:g}"
                store.set_user(chat_id, "budget_warned", month_key + ":100")
            elif spent >= budget * 0.8 and not warned.startswith(month_key):
                reply += f"\n\n⚠️ Уже {spent:g} из {budget:g} — {round(spent / budget * 100)}% бюджета месяца"
                store.set_user(chat_id, "budget_warned", month_key + ":80")
        remember(reply)
        await message.answer(reply)
        return

    # --- удалить последнюю трату ---
    if intent == "delete_expense":
        row = store.delete_last_expense(chat_id)
        reply = (
            f"🗑 Удалил трату «{row['item']}» на {row['amount']:g}" if row else "Трат пока нет — удалять нечего"
        )
        remember(reply)
        await message.answer(reply)
        return

    # --- список покупок ---
    if intent == "shopping" and data.get("shopping_items"):
        items = [s for s in data["shopping_items"] if isinstance(s, str) and s.strip()]
        store.add_shopping(chat_id, items)
        from handlers.shopping import shopping_view

        text_out, kb = shopping_view(chat_id)
        remember(f"[добавил в покупки: {', '.join(items)}]")
        await message.answer(text_out, reply_markup=kb, parse_mode="HTML")
        return

    # --- убрать из списка покупок (текстом) ---
    if intent == "remove_shopping" and data.get("shopping_ids"):
        removed = []
        current = {r["id"]: r["item"] for r in store.get_shopping(chat_id)}
        for sid in data["shopping_ids"]:
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if sid in current and store.remove_shopping(chat_id, sid):
                removed.append(current[sid])
        if removed:
            reply = "🛒 Убрал из списка: " + ", ".join(removed)
            remember(reply)
            await message.answer(reply)
            return

    # --- виртуальный холодильник ---
    if intent == "fridge" and data.get("fridge_items"):
        saved = []
        for it in data["fridge_items"]:
            if isinstance(it, dict) and it.get("product"):
                store.add_fridge(
                    chat_id, it["product"].strip(), (it.get("expires_at") or "").strip()
                )
                saved.append(it["product"].strip())
        if saved:
            from handlers.kitchen import fridge_view

            text_out, kb = fridge_view(chat_id)
            remember(f"[добавил в холодильник: {', '.join(saved)}]")
            await message.answer(text_out, reply_markup=kb, parse_mode="HTML")
            return

    # --- обычный ответ ---
    reply = data.get("reply") or "Принял! Чем ещё помочь? 😊"
    remember(reply)
    await message.answer(reply)


@router.message(F.voice)
async def on_voice(message: Message, bot: Bot) -> None:
    """Голосовое сообщение → Whisper → обычный текстовый роутер."""
    note = await message.answer("🎙 Слушаю…")
    try:
        file = await bot.get_file(message.voice.file_id)
        buf = await bot.download_file(file.file_path)
        text = await transcribe(buf.read())
    except Exception:
        log.exception("voice transcription failed")
        await note.edit_text("😔 Не получилось распознать голосовое. Попробуй ещё раз или напиши текстом.")
        return
    if not text:
        await note.edit_text("Не расслышал 🙉 Попробуй ещё раз.")
        return
    await note.edit_text(f"🎙 <i>{text}</i>", parse_mode="HTML")
    await process_text(message, text)


@router.message(F.text)
async def on_text(message: Message) -> None:
    await process_text(message, message.text)
