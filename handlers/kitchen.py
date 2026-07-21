"""Кухня: фото → продукты + рецепты, рецепты из текста, холодильник, меню на неделю."""

import base64
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import db as store
from handlers.shopping import missing_keyboard
from llm import MENU_PROMPT, RECIPES_PROMPT, VISION_PROMPT, llm, parse_llm_json

log = logging.getLogger("bot.kitchen")

router = Router()


async def send_recipes(message: Message, products: list[str], extra: str = "") -> None:
    """Общая логика: продукты → рецепты + кнопка «докупить»."""
    note = await message.answer("👨‍🍳 Подбираю рецепты...")
    try:
        extra_line = f"Пожелание пользователя: {extra}.\n" if extra else ""
        raw = await llm(
            [{"role": "user", "content": RECIPES_PROMPT.format(products=", ".join(products), extra=extra_line)}]
        )
        data = parse_llm_json(raw)
        text = data.get("text") or raw
        missing = [m for m in (data.get("missing") or []) if isinstance(m, str) and m.strip()]
        if missing:
            store.set_pending(message.chat.id, missing)
            await note.edit_text(
                text + "\n\n🛒 Докупить: " + ", ".join(missing),
                reply_markup=missing_keyboard(),
            )
        else:
            await note.edit_text(text)
    except Exception as e:
        log.exception("recipes failed")
        await note.edit_text(f"Не получилось подобрать рецепты 😢 ({e})")


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    store.upsert_user(message.chat.id)
    note = await message.answer("🔍 Смотрю, что у тебя есть...")
    try:
        photo = message.photo[-1]  # самое большое разрешение
        buf = await bot.download(photo)
        image_b64 = base64.b64encode(buf.read()).decode()
        user_text = (message.caption or "").strip()
        extra_line = f"Комментарий пользователя: {user_text}.\n" if user_text else ""
        content = [
            {"type": "text", "text": VISION_PROMPT.format(extra=extra_line)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
        raw = await llm([{"role": "user", "content": content}])
        data = parse_llm_json(raw)
        text = data.get("text") or raw
        missing = [m for m in (data.get("missing") or []) if isinstance(m, str) and m.strip()]
        if missing:
            store.set_pending(message.chat.id, missing)
            await note.edit_text(
                text + "\n\n🛒 Докупить: " + ", ".join(missing),
                reply_markup=missing_keyboard(),
            )
        else:
            await note.edit_text(text)
    except Exception as e:
        log.exception("photo handler failed")
        await note.edit_text(f"Не получилось разобрать фото 😢 ({e})")


# --- виртуальный холодильник ---------------------------------------------------

@router.message(Command("fridge"))
async def cmd_fridge(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    args = (command.args or "").strip()
    if args == "clear":
        n = store.clear_fridge(message.chat.id)
        await message.answer(f"Холодильник очищен ({n} поз.) ✅")
        return
    if args.isdigit():
        if store.remove_fridge(message.chat.id, int(args)):
            await message.answer(f"Убрал позицию {args} из холодильника ✅")
        else:
            await message.answer("Не нашёл такую позицию. Список — /fridge")
        return
    items = store.get_fridge(message.chat.id)
    if not items:
        await message.answer(
            "🧊 Холодильник пуст.\n"
            "Просто напиши: «в холодильнике курица до 25.07, молоко до пятницы» — я запомню.\n"
            "Убрать позицию: /fridge НОМЕР • Очистить: /fridge clear"
        )
        return
    lines = ["🧊 Виртуальный холодильник:"]
    for i in items:
        exp = f" — до {i['expires_at'][8:10]}.{i['expires_at'][5:7]}" if i["expires_at"] else ""
        lines.append(f"{i['id']}. {i['product']}{exp}")
    lines.append("\nЧто приготовить из этого? Напиши: «что приготовить из холодильника»")
    lines.append("Убрать: /fridge НОМЕР • Очистить: /fridge clear")
    await message.answer("\n".join(lines))


# --- меню на неделю -------------------------------------------------------------

@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    store.upsert_user(message.chat.id)
    note = await message.answer("📅 Составляю меню на неделю...")
    try:
        fridge = store.get_fridge(message.chat.id)
        if fridge:
            products = ", ".join(i["product"] for i in fridge)
            fridge_note = f"Дома уже есть: {products}.\n"
        else:
            fridge_note = ""
        raw = await llm([{"role": "user", "content": MENU_PROMPT.format(fridge_note=fridge_note)}])
        data = parse_llm_json(raw)
        text = data.get("text") or raw
        shopping = [s for s in (data.get("shopping") or []) if isinstance(s, str) and s.strip()]
        if shopping:
            store.set_pending(message.chat.id, shopping)
            await note.edit_text(
                text + "\n\n🛒 Купить на неделю: " + ", ".join(shopping),
                reply_markup=missing_keyboard(),
            )
        else:
            await note.edit_text(text)
    except Exception as e:
        log.exception("menu failed")
        await note.edit_text(f"Не получилось составить меню 😢 ({e})")
