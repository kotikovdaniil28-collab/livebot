"""Единый список покупок: команды, шаринг и кнопка «добавить недостающее»."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db as store

router = Router()


def missing_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Добавить недостающее в список покупок", callback_data="missing:add")]
        ]
    )


def _short(text: str, limit: int = 25) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def shopping_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    items = store.get_shopping(chat_id)
    if not items:
        return "🛒 Список покупок пуст. Просто напиши: «купить молоко и хлеб»", None
    lines = ["🛒 Список покупок (нажми, чтобы вычеркнуть):"]
    rows = []
    for i in items:
        lines.append(f"{i['id']}. {i['item']}")
        rows.append(
            [InlineKeyboardButton(text=f"✔️ {_short(i['item'])}", callback_data=f"shop:del:{i['id']}")]
        )
    rows.append([InlineKeyboardButton(text="🗑 Очистить весь список", callback_data="shop:clear")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def shopping_text(chat_id: int) -> str:
    return shopping_view(chat_id)[0]


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    store.upsert_user(message.chat.id)
    text, kb = shopping_view(message.chat.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("shop:del:"))
async def cb_shop_del(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":")[2])
    if not store.remove_shopping(callback.message.chat.id, item_id):
        await callback.answer("Позиция уже вычеркнута", show_alert=True)
        return
    await callback.answer("Вычеркнуто ✔️")
    text, kb = shopping_view(callback.message.chat.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "shop:clear")
async def cb_shop_clear(callback: CallbackQuery) -> None:
    n = store.clear_shopping(callback.message.chat.id)
    await callback.answer(f"Очищено ({n} поз.)")
    try:
        await callback.message.edit_text("🛒 Список покупок пуст. Просто напиши: «купить молоко и хлеб»")
    except Exception:
        pass


@router.message(Command("buy"))
async def cmd_buy(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    if not command.args:
        await message.answer("Что купить? Например: /buy молоко, хлеб")
        return
    items = [s.strip() for s in command.args.replace(";", ",").split(",") if s.strip()]
    added = store.add_shopping(message.chat.id, items)
    text, kb = shopping_view(message.chat.id)
    await message.answer(f"Добавил в список: {added} поз. ✅\n\n" + text, reply_markup=kb)


@router.message(Command("bought"))
async def cmd_bought(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Укажи номер позиции: /bought 2 (список — /list)")
        return
    if store.remove_shopping(message.chat.id, int(args)):
        await message.answer(f"Вычеркнул позицию {args} ✅")
    else:
        await message.answer("Не нашёл такую позицию. Список — /list")


@router.message(Command("clearlist"))
async def cmd_clearlist(message: Message) -> None:
    n = store.clear_shopping(message.chat.id)
    await message.answer(f"Список покупок очищен ({n} поз.) ✅")


# --- шаринг списка с другим пользователем -----------------------------------

@router.message(Command("share"))
async def cmd_share(message: Message) -> None:
    store.upsert_user(message.chat.id)
    owner = store.list_owner(message.chat.id)
    await message.answer(
        f"Код твоего списка покупок: `{owner}`\n"
        f"Пусть близкий человек отправит боту:\n/join {owner}\n"
        "— и вы будете вести один список на двоих.",
        parse_mode="Markdown",
    )


@router.message(Command("join"))
async def cmd_join(message: Message, command: CommandObject) -> None:
    store.upsert_user(message.chat.id)
    args = (command.args or "").strip()
    if not args.lstrip("-").isdigit():
        await message.answer("Формат: /join КОД (код даёт команда /share у владельца списка)")
        return
    owner = int(args)
    if owner == message.chat.id:
        await message.answer("Это твой собственный код 🙂 Отправь его другому человеку.")
        return
    store.set_user(message.chat.id, "list_owner", owner)
    await message.answer("Готово! Теперь у вас общий список покупок ✅\nПосмотреть: /list")


@router.message(Command("unjoin"))
async def cmd_unjoin(message: Message) -> None:
    store.upsert_user(message.chat.id)
    store.set_user(message.chat.id, "list_owner", 0)
    await message.answer("Ты снова ведёшь свой собственный список покупок ✅")


# --- кнопка «добавить недостающие ингредиенты» под рецептами ------------------

@router.callback_query(F.data == "missing:add")
async def cb_missing(callback: CallbackQuery) -> None:
    items = store.pop_pending(callback.message.chat.id)
    if not items:
        await callback.answer("Список уже добавлен или устарел", show_alert=True)
        return
    added = store.add_shopping(callback.message.chat.id, items)
    await callback.answer(f"Добавлено: {added} поз.")
    await callback.message.answer(
        f"🛒 Добавил в список покупок: {', '.join(items)}\nПосмотреть: /list"
    )
