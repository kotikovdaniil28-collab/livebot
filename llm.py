"""LLM-клиент: Gemini API через OpenAI-совместимый endpoint (chat/completions).

Работает с любым OpenAI-совместимым API — управляется через LLM_BASE_URL/LLM_MODEL.
Vision (фото) передаётся стандартным image_url c data:-URI — Gemini это поддерживает.
"""

import asyncio
import json
import logging
import re

import aiohttp

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

log = logging.getLogger("bot.llm")

_RETRIES = 3


async def llm(messages: list[dict]) -> str:
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    payload = {"model": LLM_MODEL, "messages": messages}
    timeout = aiohttp.ClientTimeout(total=120)
    last_err: Exception | None = None
    for attempt in range(1, _RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers
                ) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
            if status == 429 or status >= 500:
                raise RuntimeError(f"LLM HTTP {status}")
            if "error" in data:
                msg = data["error"].get("message", str(data["error"]))
                raise RuntimeError(f"LLM error: {msg}")
            content = data["choices"][0]["message"]["content"]
            if not content or not content.strip():
                raise RuntimeError("LLM вернул пустой ответ")
            return content
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            last_err = e
            if attempt < _RETRIES:
                wait = 2 * attempt
                log.warning("LLM attempt %d/%d failed (%s), retry in %ds", attempt, _RETRIES, e, wait)
                await asyncio.sleep(wait)
    raise RuntimeError(f"LLM недоступен после {_RETRIES} попыток: {last_err}")


def parse_llm_json(text: str) -> dict:
    """Вытаскивает JSON из ответа модели (убирает ```-обёртки)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(match.group(0) if match else text)


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------

ROUTER_PROMPT = """Ты — модуль-маршрутизатор Telegram-бота «Личный Ассистент Дня».
Сейчас: {now} ({tz}).
Определи намерение пользователя и верни СТРОГО один JSON без пояснений:
{{"intent": "task" | "recipes" | "expense" | "shopping" | "fridge" | "chat",
  "task_text": "суть задачи без слов 'напомни' и без времени" | null,
  "remind_at": "YYYY-MM-DDTHH:MM" | null,
  "products": ["продукт", ...] | null,
  "expense_item": "на что потратил" | null,
  "expense_amount": число | null,
  "shopping_items": ["что купить", ...] | null,
  "fridge_items": [{{"product": "название", "expires_at": "YYYY-MM-DD" | ""}}, ...] | null,
  "reply": "ответ, если intent=chat" | null}}
Правила:
- "task": пользователь хочет добавить дело или напоминание. Если указано время — заполни remind_at в будущем относительно текущего момента («завтра в 9», «через 20 минут»).
- "recipes": пользователь перечисляет продукты и спрашивает, что приготовить (или явно просит рецепт). Заполни products.
- "expense": пользователь сообщает о трате («кофе 250», «потратил 1200 на бензин»). Заполни expense_item и expense_amount.
- "shopping": пользователь просит добавить в список покупок («купить молоко», «добавь хлеб в список»). Заполни shopping_items.
- "fridge": пользователь сообщает, что положил/у него есть продукты дома («в холодильнике курица до 25 июля», «купил молоко, срок до пятницы»). Заполни fridge_items, дату переведи в YYYY-MM-DD (или "" если срок не указан).
- "chat": всё остальное — дай короткий полезный ответ на русском в поле reply.
Если фраза похожа и на recipes, и на fridge: «что приготовить» → recipes, «запомни/добавь в холодильник» → fridge."""

RECIPES_PROMPT = """Ты — опытный повар. Из этих продуктов предложи 2–3 рецепта: {products}.
{extra}Верни СТРОГО один JSON без пояснений:
{{"text": "рецепты одним сообщением: для каждого — название с эмодзи, время готовки, короткие пошаговые инструкции; по-русски, компактно",
  "missing": ["ингредиент, которого не хватает", ...]}}
Используй в основном указанные продукты + базовые (соль, масло, специи).
В "missing" — только то, что реально стоит докупить (не соль и не воду). Если ничего — пустой список."""

VISION_PROMPT = """На фото — продукты (холодильник, стол или пакеты с едой).
{extra}Верни СТРОГО один JSON без пояснений:
{{"text": "ответ пользователю: 1) список распознанных продуктов (начни с '🧊 Вижу:'), 2) 2–3 рецепта из них (название, время, короткие шаги); по-русски, компактно, с эмодзи. Если на фото нет еды — так и скажи",
  "products": ["распознанный продукт", ...],
  "missing": ["ингредиент, который стоит докупить", ...]}}"""

MENU_PROMPT = """Ты — опытный повар и диетолог. Составь меню ужинов на 5 дней (пн–пт).
{fridge_note}Верни СТРОГО один JSON без пояснений:
{{"text": "меню одним сообщением: для каждого дня — эмодзи, название блюда, время готовки, 1–2 строки описания; по-русски",
  "shopping": ["продукт для покупки", ...]}}
В "shopping" — общий список продуктов на всю неделю (без базовых: соль, масло, специи).
Учитывай продукты, которые уже есть дома — их докупать не надо."""

SAVE_PRODUCT_PROMPT = (
    "Продукт «{product}» скоро испортится. Предложи 1–2 быстрых рецепта, чтобы его использовать. "
    "Название с эмодзи, время, короткие шаги. По-русски, очень компактно."
)
