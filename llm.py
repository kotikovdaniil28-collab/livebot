"""LLM-клиент: Groq API (OpenAI-совместимый endpoint chat/completions).

Работает с любым OpenAI-совместимым API — управляется через LLM_BASE_URL/LLM_MODEL.
Vision (фото) передаётся стандартным image_url c data:-URI — модель Llama 4 Scout
на Groq это поддерживает (LLM_VISION_MODEL).
"""

import asyncio
import json
import logging
import re

import aiohttp

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_VISION_MODEL

log = logging.getLogger("bot.llm")

_RETRIES = 3

# Резервная модель на случай, если основная упёрлась в лимит
# (llama-3.1-8b-instant на Groq: лимиты выше, отвечает быстрее)
_FALLBACK_MODEL = "llama-3.1-8b-instant"


class LLMRateLimitError(RuntimeError):
    """Исчерпан лимит запросов к LLM (HTTP 429)."""


async def _call(session: aiohttp.ClientSession, model: str, messages: list[dict]) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    payload = {"model": model, "messages": messages}
    async with session.post(
        f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers
    ) as resp:
        return resp.status, await resp.json(content_type=None)


def _has_image(messages: list[dict]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            return True
    return False


async def llm(messages: list[dict]) -> str:
    timeout = aiohttp.ClientTimeout(total=120)
    if _has_image(messages):
        # Фото разбирает vision-модель, запасной для неё нет
        models = [LLM_VISION_MODEL]
    else:
        models = [LLM_MODEL]
        if LLM_MODEL != _FALLBACK_MODEL:
            models.append(_FALLBACK_MODEL)

    last_err: Exception | None = None
    rate_limited = False
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in models:
            for attempt in range(1, _RETRIES + 1):
                try:
                    status, data = await _call(session, model, messages)
                    # Некоторые API возвращают ошибку списком: [{"error": {...}}]
                    if isinstance(data, list):
                        data = data[0] if data and isinstance(data[0], dict) else {}
                    if not isinstance(data, dict):
                        raise RuntimeError(f"LLM вернул неожиданный ответ (HTTP {status})")
                    if status == 429:
                        rate_limited = True
                        # Лимит на минуту — ждать дольше нет смысла, пробуем запасную модель
                        raise RuntimeError(f"HTTP 429 (лимит запросов, модель {model})")
                    if status >= 500:
                        raise RuntimeError(f"LLM HTTP {status}")
                    if "error" in data:
                        err = data["error"]
                        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        raise RuntimeError(f"LLM error (HTTP {status}): {msg}")
                    content = data["choices"][0]["message"]["content"]
                    if not content or not content.strip():
                        raise RuntimeError("LLM вернул пустой ответ")
                    return content
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError, TypeError, IndexError) as e:
                    last_err = e
                    is_429 = "429" in str(e)
                    if is_429:
                        # Минутный лимит: сразу переходим к следующей модели
                        log.warning("Модель %s: лимит 429, переключаюсь на запасную", model)
                        break
                    if attempt < _RETRIES:
                        wait = 2 * attempt
                        log.warning(
                            "LLM %s: попытка %d/%d не удалась (%s), повтор через %dс",
                            model, attempt, _RETRIES, e, wait,
                        )
                        await asyncio.sleep(wait)

    if rate_limited:
        raise LLMRateLimitError(
            "лимит бесплатных запросов LLM исчерпан (минутный или дневной)"
        )
    raise RuntimeError(f"LLM недоступен: {last_err}")


WHISPER_MODEL = "whisper-large-v3"


async def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Распознаёт речь через Groq Whisper. Возвращает текст."""
    timeout = aiohttp.ClientTimeout(total=60)
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type="audio/ogg")
    form.add_field("model", WHISPER_MODEL)
    form.add_field("language", "ru")
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{LLM_BASE_URL}/audio/transcriptions", data=form, headers=headers
        ) as resp:
            data = await resp.json(content_type=None)
    if not isinstance(data, dict) or "text" not in data:
        err = data.get("error", data) if isinstance(data, dict) else data
        raise RuntimeError(f"Whisper error: {err}")
    return (data["text"] or "").strip()


def friendly_error(e: Exception, action: str = "обработать запрос") -> str:
    """Человекочитаемое сообщение об ошибке LLM (без трейсбеков)."""
    if isinstance(e, LLMRateLimitError) or "429" in str(e):
        return (
            "⏳ Слишком много запросов подряд — бесплатный лимит на минуту исчерпан.\n"
            "Подожди минутку и попробуй ещё раз 🙏"
        )
    return f"😔 Не получилось {action}. Попробуй ещё раз чуть позже."


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

Текущее состояние пользователя:
Открытые задачи (id: текст): {tasks}
Список покупок (id: товар): {shopping}

Определи намерение пользователя и верни СТРОГО один JSON без пояснений:
{{"intent": "task" | "complete_task" | "delete_task" | "recipes" | "expense" | "delete_expense" | "shopping" | "remove_shopping" | "fridge" | "chat",
  "task_text": "суть задачи без слов 'напомни' и без времени" | null,
  "remind_at": "YYYY-MM-DDTHH:MM" | null,
  "repeat": "daily" | "weekly" | "monthly" | null,
  "task_id": число | null,
  "products": ["продукт", ...] | null,
  "expense_item": "на что потратил" | null,
  "expense_amount": число | null,
  "shopping_items": ["что купить", ...] | null,
  "shopping_ids": [число, ...] | null,
  "fridge_items": [{{"product": "название", "expires_at": "YYYY-MM-DD" | ""}}, ...] | null,
  "reply": "ответ, если intent=chat" | null}}
Правила:
- "task": добавить дело или напоминание. Если указано время — заполни remind_at в будущем относительно текущего момента («завтра в 9», «через 20 минут»). Если пользователь просит напоминать регулярно («каждый день», «каждую неделю/каждый понедельник», «каждый месяц») — заполни repeat и remind_at первым срабатыванием.
- "complete_task": пользователь говорит, что задача сделана («позвонил маме», «задача про отчёт готова»). Найди её в списке открытых задач и верни task_id. Если не нашёл — intent=chat.
- "delete_task": пользователь просит удалить/отменить задачу («убери задачу про отчёт»). Верни task_id из списка.
- "recipes": пользователь перечисляет продукты и спрашивает, что приготовить. Заполни products.
- "expense": трата («кофе 250», «потратил 1200 на бензин»). Заполни expense_item и expense_amount.
- "delete_expense": пользователь просит удалить последнюю трату («удали последнюю трату», «я ошибся с тратой»).
- "shopping": добавить в список покупок («купить молоко»). Заполни shopping_items.
- "remove_shopping": убрать из списка покупок («убери молоко из списка», «молоко уже купил»). Верни shopping_ids из списка.
- "fridge": продукты дома («в холодильнике курица до 25 июля»). Заполни fridge_items, дату в YYYY-MM-DD (или "").
- "chat": всё остальное — короткий полезный ответ на русском в reply. Учитывай контекст предыдущих сообщений диалога.
Если фраза похожа и на recipes, и на fridge: «что приготовить» → recipes, «запомни/добавь в холодильник» → fridge."""

RECIPES_PROMPT = """Ты — опытный повар. Из этих продуктов предложи 2–3 рецепта: {products}.
{extra}Верни СТРОГО один JSON без пояснений:
{{"text": "рецепты одним сообще��ием: для каждого — название с эмодзи, время готовки, короткие пошаговые инструкции; по-русски, компактно",
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
