"""Свободный текст → LLM-роутер (задача / рецепты / трата / покупки / холодильник / болтовня).

Важно: этот роутер регистрируется ПОСЛЕДНИМ, чтобы не перехватывать команды.
"""

import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.types import Message

import db as store
from config import TZ
from handlers.kitchen import send_recipes
from llm import LLMRateLimitError, ROUTER_PROMPT, llm, parse_llm_json

log = logging.getLogger("bot.text")

router = Router()


@router.message(F.text)
async def on_text(message: Message) -> None:
    store.upsert_user(message.chat.id)
    try:
        system = ROUTER_PROMPT.format(
            now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A"), tz=str(TZ)
        )
        raw = await llm(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": message.text},
            ]
        )
        data = parse_llm_json(raw)
    except LLMRateLimitError:
        await message.answer(
            "⏳ <b>Слишком много запросов подряд</b>\n\n"
            "Бесплатный лимит Gemini на минуту исчерпан.\n"
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

    # --- задача / напоминание ---
    if intent == "task" and data.get("task_text"):
        remind_at = data.get("remind_at")
        store.add_task(message.chat.id, data["task_text"], remind_at)
        if remind_at:
            nice = f"{remind_at[8:10]}.{remind_at[5:7]} в {remind_at[11:16]}"
            await message.answer(f"✅ «{data['task_text']}» — напомню {nice}")
        else:
            await message.answer(f"✅ «{data['task_text']}» — записал")
        return

    # --- рецепты ---
    if intent == "recipes":
        products = data.get("products") or []
        if not products:
            # «что приготовить из холодильника» — берём виртуальный холодильник
            products = [i["product"] for i in store.get_fridge(message.chat.id)]
        if products:
            await send_recipes(message, products)
            return
        await message.answer(
            "Напиши, какие продукты есть (например: «курица, картошка, сметана»), "
            "или пришли фото холодильника 📸"
        )
        return

    # --- трата ---
    if intent == "expense" and data.get("expense_amount"):
        item = (data.get("expense_item") or "покупка").strip()
        amount = float(data["expense_amount"])
        store.add_expense(message.chat.id, item, amount)
        rows = store.expenses_since(message.chat.id, store.today())
        total = sum(r["amount"] for r in rows)
        await message.answer(f"💸 {item} — {amount:g} · за сегодня: {total:g}")
        return

    # --- список покупок ---
    if intent == "shopping" and data.get("shopping_items"):
        items = [s for s in data["shopping_items"] if isinstance(s, str) and s.strip()]
        store.add_shopping(message.chat.id, items)
        from handlers.shopping import shopping_view

        text, kb = shopping_view(message.chat.id)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    # --- виртуальный холодильник ---
    if intent == "fridge" and data.get("fridge_items"):
        saved = []
        for it in data["fridge_items"]:
            if isinstance(it, dict) and it.get("product"):
                store.add_fridge(
                    message.chat.id, it["product"].strip(), (it.get("expires_at") or "").strip()
                )
                saved.append(it["product"].strip())
        if saved:
            from handlers.kitchen import fridge_view

            text, kb = fridge_view(message.chat.id)
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

    # --- обычный ответ ---
    await message.answer(data.get("reply") or "Принял! Чем ещё помочь? 😊")
