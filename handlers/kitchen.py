"""Кухня: фото → продукты + рецепты, рецепты из текста, холодильник, меню на неделю."""

import base64
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import db as store
from handlers.cb_utils import safe_answer
from handlers.shopping import missing_keyboard
from llm import MENU_PROMPT, RECIPES_PROMPT, VISION_PROMPT, friendly_error, llm, parse_llm_json

log = logging.getLogger("bot.kitchen")

router = Router()

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
RECIPES_IMG = ASSETS_DIR / "recipes.png"
MENU_IMG = ASSETS_DIR / "menu_week.png"
VISION_IMG = ASSETS_DIR / "fridge_scan.png"

_CAPTION_LIMIT = 1024


async def _reply_pretty(
    message: Message,
    note: Message,
    text: str,
    img: Path,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Красивый ответ: HTML-разметка + тематическая картинка, с фолбэком на текст."""
    try:
        if img.exists():
            try:
                await note.delete()
            except Exception:
                pass
            if len(text) <= _CAPTION_LIMIT:
                await message.answer_photo(
                    FSInputFile(img), caption=text, parse_mode="HTML", reply_markup=kb
                )
            else:
                await message.answer_photo(FSInputFile(img))
                await message.answer(text, parse_mode="HTML", reply_markup=kb)
        else:
            await note.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        # LLM мог вернуть кривой HTML — отправляем без разметки
        log.warning("pretty reply failed, falling back to plain text", exc_info=True)
        await message.answer(text, reply_markup=kb)


async def send_recipes(message: Message, products: list[str], extra: str = "") -> None:
    """Общая логика: продукты → рецепты + кнопка «докупить»."""
    note = await message.answer("👨‍🍳 Подбираю рецепты...")
    try:
        extra_line = f"Пожелание пользователя: {extra}.\n" if extra else ""
        raw = await llm(
            [{"role": "user", "content": RECIPES_PROMPT.format(products=", ".join(products), extra=extra_line)}]
        )
        try:
            data = parse_llm_json(raw)
        except Exception:
            # Модель ответила не-JSON — покажем как есть, лучше текст, чем ошибка
            log.warning("recipes JSON parse failed, using raw text")
            data = {"text": raw.strip().strip("`")}
        text = data.get("text") or raw
        missing = [m for m in (data.get("missing") or []) if isinstance(m, str) and m.strip()]
        kb = None
        if missing:
            store.set_pending(message.chat.id, missing)
            text += "\n\n🛒 <b>Докупить:</b> " + ", ".join(missing)
            kb = missing_keyboard()
        await _reply_pretty(message, note, text, RECIPES_IMG, kb)
    except Exception as e:
        log.exception("recipes failed")
        await note.edit_text(friendly_error(e, "подобрать рецепты"))


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
        try:
            data = parse_llm_json(raw)
        except Exception:
            # Модель ответила не-JSON — покажем как есть, лучше текст, чем ошибка
            log.warning("vision JSON parse failed, using raw text")
            data = {"text": raw.strip().strip("`")}
        text = data.get("text") or raw
        missing = [m for m in (data.get("missing") or []) if isinstance(m, str) and m.strip()]
        kb = None
        if missing:
            store.set_pending(message.chat.id, missing)
            text += "\n\n🛒 <b>Докупить:</b> " + ", ".join(missing)
            kb = missing_keyboard()
        await _reply_pretty(message, note, text, VISION_IMG, kb)
    except Exception as e:
        log.exception("photo handler failed")
        await note.edit_text(friendly_error(e, "разобрать фото"))


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
    text, kb = fridge_view(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _short(text: str, limit: int = 14) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fridge_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    items = store.get_fridge(chat_id)
    if not items:
        return "🧊 Холодильник пуст. Напиши: «в холодильнике курица до 25.07»", None
    text = f"<b>🧊 Холодильник</b> — {len(items)} поз.\n<i>Нажми, чтобы убрать</i>"
    btns = []
    for i in items:
        exp = f" · {i['expires_at'][8:10]}.{i['expires_at'][5:7]}" if i["expires_at"] else ""
        btns.append(
            InlineKeyboardButton(text=f"{_short(i['product'])}{exp}", callback_data=f"fridge:del:{i['id']}")
        )
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton(text="👨‍🍳 Что приготовить?", callback_data="fridge:cook")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("fridge:del:"))
async def cb_fridge_del(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":")[2])
    if not store.remove_fridge(callback.message.chat.id, item_id):
        await safe_answer(callback, "Позиция уже убрана", show_alert=True)
        return
    await safe_answer(callback, "Убрано ❌")
    text, kb = fridge_view(callback.message.chat.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "fridge:cook")
async def cb_fridge_cook(callback: CallbackQuery) -> None:
    products = [i["product"] for i in store.get_fridge(callback.message.chat.id)]
    if not products:
        await safe_answer(callback, "Холодильник пуст", show_alert=True)
        return
    await safe_answer(callback)
    await send_recipes(callback.message, products)


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
        kb = None
        if shopping:
            store.set_pending(message.chat.id, shopping)
            text += "\n\n🛒 <b>Купить на неделю:</b> " + ", ".join(shopping)
            kb = missing_keyboard()
        await _reply_pretty(message, note, text, MENU_IMG, kb)
    except Exception as e:
        log.exception("menu failed")
        await note.edit_text(friendly_error(e, "составить меню"))
