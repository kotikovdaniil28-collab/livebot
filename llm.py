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

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_VISION_MODEL,
    UNITY2_API_KEY,
    UNITY2_BASE_URL,
    UNITY2_MODEL,
)

log = logging.getLogger("bot.llm")

_RETRIES = 3

# Резервная модель на случай, если основная упёрлась в лимит
# (llama-3.1-8b-instant на Groq: лимиты выше, отвечает быстрее)
_FALLBACK_MODEL = "llama-3.1-8b-instant"


def _text_providers() -> list[tuple[str, str, str, int, int]]:
    """Цепочка провайдеров для текста: (base_url, api_key, model, timeout_s, retries).

    Unity2 (Gemini) — основной, но шлюз бывает медленным, поэтому даём ему
    жёсткий лимит времени и одну попытку: не успел — мгновенно уходим на Groq.
    """
    providers: list[tuple[str, str, str, int, int]] = []
    if UNITY2_API_KEY:
        providers.append((UNITY2_BASE_URL, UNITY2_API_KEY, UNITY2_MODEL, 12, 1))
    if LLM_API_KEY:
        providers.append((LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, 60, 2))
        if LLM_MODEL != _FALLBACK_MODEL:
            providers.append((LLM_BASE_URL, LLM_API_KEY, _FALLBACK_MODEL, 60, 2))
    return providers


class LLMRateLimitError(RuntimeError):
    """Исчерпан лимит запросов к LLM (HTTP 429)."""


async def _call(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout_s: int,
) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "messages": messages}
    async with session.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
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
    if _has_image(messages):
        # Фото разбирает vision-модель Groq, запасной для неё нет
        providers = [(LLM_BASE_URL, LLM_API_KEY, LLM_VISION_MODEL, 120, _RETRIES)]
    else:
        providers = _text_providers()

    last_err: Exception | None = None
    rate_limited = False
    async with aiohttp.ClientSession() as session:
        for base_url, api_key, model, timeout_s, retries in providers:
            for attempt in range(1, retries + 1):
                try:
                    status, data = await _call(
                        session, base_url, api_key, model, messages, timeout_s
                    )
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
                    if isinstance(e, asyncio.TimeoutError):
                        # Провайдер тормозит — не тратим время на повторы, идём дальше
                        log.warning("Модель %s: таймаут %dс, переключаюсь на запасную", model, timeout_s)
                        break
                    if attempt < retries:
                        wait = 1
                        log.warning(
                            "LLM %s: попытка %d/%d не удалась (%s), повтор через %dс",
                            model, attempt, retries, e, wait,
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
    candidate = match.group(0) if match else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Модель часто оставляет сырые переводы строк внутри строк JSON —
        # strict=False разрешает управляющие символы в строках
        return json.loads(candidate, strict=False)


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
  "dish": "название конкретного блюда" | null,
  "expense_item": "на что потратил" | null,
  "expense_amount": число | null,
  "shopping_items": ["что купить", ...] | null,
  "shopping_ids": [число, ...] | null,
  "fridge_items": [{{"product": "название", "expires_at": "YYYY-MM-DD" | ""}}, ...] | null,
  "reply": "ответ, если intent=chat" | null,
  "mood": "laugh" | "cool" | "think" | "shock" | null}}
Правила:
- "task": добавить дело или напоминание. Если указано время — заполни remind_at в буду��ем относительно текущего момента («завтра в 9», «через 20 минут»). Если пользователь просит напоминать регулярно («каждый день», «каждую неделю/каждый понедельник», «каждый месяц») — заполни repeat и remind_at первым срабатыванием.
- "complete_task": пользователь говорит, что задача сделана («позвонил маме», «задача про отчёт готова»). Найди её в списке открытых задач и верни task_id. Если не н��шёл — intent=chat.
- "delete_task": пользователь просит удалить/отменить задачу («убери задачу про отчёт»). Верни task_id из списка.
- "recipes": пользователь перечисляет продукты и спрашивает, что приготовить (заполни products), ИЛИ просит рецепт конкретного блюда («рецепт пиццы», «как приготовить борщ») — тогда заполни dish названием блюда.
- "expense": трата («кофе 250», «потратил 1200 на бензин»). Заполни expense_item и expense_amount.
- "delete_expense": пользователь просит удалить последнюю трату («удали последнюю трату», «я ошибся с тратой»).
- "shopping": добавить в список покупок («купить молоко»). Заполни shopping_items.
- "remove_shopping": убрать из списка покупок («убери молоко из списка», «молоко уже купил»). Верни shopping_ids из списка.
- "fridge": продукты дома («в холодильнике курица до 25 июля»). Заполни fridge_items, дату в YYYY-MM-DD (или "").
- "chat": всё остальное — короткий полезный ответ на русском в reply. Учитывай контекст предыдущих сообщений диалога.
Характер для intent=chat:
- У тебя характер в стиле Grok: остроумный, дерзкий, с самоиронией. Не будь скучным корпоративным ботом.
- Если запрос абсурдный, троллинг или явно дурацкий (типа «как сварить пельмени из говна») — НЕ отвечай сухо и вежливо. Подыграй и угорай на всю катушку: съязви, пошути над самим запросом, доведи абсурд до смешного, по-доброму простебай пользователя. Пара эмодзи в тему приветствуется.
- Пример тона на такой запрос: «Мишлен уже выехал забирать звезду. Рецепт „а-ля Шарик“: ингредиент один — уважение к себе, и его, судя по запросу, не хватает 😌 Давай лучше нормальные пельмени: тесто, фарш, и собака останется твоим другом, а не су-шефом».
- Если пользователь сам общается неформально или матерится — можешь ответить в тон, лёгкий мат допустим как приправа (блин, чёрт, «какого хрена» и покрепче), но дозированно и смешно, а не грубо. Никогда не матерись В АДРЕС пользователя.
- На нормальные вопросы отвечай полезно, но живо и с лёгким юмором, без канцелярита.
- Юмор — да, оскорбления и реальный вред — нет. Опасные инструкции не давай, но отказ оборачивай в шутку, а не в занудную лекцию.
- Поле "mood" (только для intent=chat): выбери эмоцию, с которой отвечаешь — "laugh" (угораешь над абсурдом/шуткой), "cool" (дерзкий/самоуверенный ответ), "think" (философский или сложный вопрос), "shock" (пользователь написал дичь). Для обычных нейтральных ответов — null.
Если фраза похожа и на recipes, и на fridge: «что приготовить» → recipes, «запомни/добавь в холодильник» → fridge."""

RECIPES_PROMPT = """Ты — опытный повар. Продукты или блюдо: {products}.
Если это список продуктов — предложи 2–3 рецепта из них. Если это конкретное блюдо — дай его рецепт (1–2 варианта приготовления).
{extra}Верни СТРОГО один JSON без пояснений (внутри строки "text" экранируй переводы строк как \\n):
{{"text": "рецепты одним сообщением, красиво отформатированные по шаблону ниже",
  "missing": ["ингредиент, которого не хватает", ...]}}
Шаблон форматирования "text" (Telegram HTML, разрешены ТОЛЬКО теги <b> и <i>, никакого markdown):
🍲 <b>Название блюда</b>
⏱ 25 мин · 🔥 сытно
<i>Понадобится: список ингредиентов через запятую</i>
1. Пер��ый шаг — коротко и по делу
2. Второй шаг
3. Третий шаг

Между рецептами — пустая строка. Эмодзи блюда подбирай по смыслу (🍲🍝🥗🍳🥘🍕). Шаги — максимум 4–5, каждый с новой строки.
Используй в основном указанные продукты + базовые (соль, масло, специи).
В "missing" — только то, что реально стоит докупить (не соль и не воду). Если ничего — пустой список."""

VISION_PROMPT = """На фото — продукты (холодильник, стол или пакеты с едой).
{extra}Верни СТРОГО один JSON без пояснений:
{{"text": "ответ пользователю по шаблону ниже. Если на фото нет еды — так и скажи (можно с юмором)",
  "products": ["распознанный продукт", ...],
  "missing": ["ингредиент, который стоит докупить", ...]}}
Шаблон "text" (Telegram HTML, разрешены ТОЛЬКО теги <b> и <i>, никакого markdown):
🧊 <b>Вижу:</b> список продуктов через запятую

🍲 <b>Название блюда</b>
⏱ 25 мин
<i>Понадобится: ингредиенты через запятую</i>
1. Шаг
2. Шаг
3. Шаг

Дай 2–3 рецепта, между ними пустая строка. Эмодзи блюда — по смыслу (🍲🍝🥗🍳🥘). По-русски, компактно."""

MENU_PROMPT = """Ты — опытный по��ар и диетолог. Составь меню ужинов на 5 дней (пн–пт).
{fridge_note}Верни СТРОГО один JSON без пояснений:
{{"text": "меню одним сообщением по шаблону ниже",
  "shopping": ["продукт для покупки", ...]}}
Шаблон "text" (Telegram HTML, разрешены ТОЛЬКО теги <b> и <i>, никакого markdown):
📅 <b>Меню ужинов на неделю</b>

<b>Пн</b> · 🍝 Название блюда — ⏱ 30 мин
<i>1–2 строки: что это и почему вкусно</i>

<b>Вт</b> · 🥘 Название блюда — ⏱ 25 мин
<i>описание</i>

...и так для всех 5 дней, между днями пустая строка. Эмодзи блюда — по смыслу.
В "shopping" — общий список продуктов на всю неделю (без базовых: соль, масло, специи).
Учитывай продукты, которые уже есть дома — их докупать не надо."""

SAVE_PRODUCT_PROMPT = (
    "Продукт «{product}» скоро испортится. Предложи 1–2 быстрых рецепта, чтобы его использовать. "
    "Формат (Telegram HTML, только теги <b> и <i>, без markdown): эмодзи блюда + <b>название</b>, "
    "строка «⏱ время», затем нумерованные шаги, каждый с новой строки. По-русски, очень компактно."
)
