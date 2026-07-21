"""Конфигурация бота: все настройки читаются из .env."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Groq API — быстрый и бесплатный. Ключ: https://console.groq.com/keys
# Можно переопределить LLM_BASE_URL/LLM_MODEL и подключить любой другой OpenAI-совместимый API.
LLM_API_KEY = (
    os.getenv("GROQ_API_KEY", "")
    or os.getenv("LLM_API_KEY", "")
    or os.getenv("OPENAI_API_KEY", "")
)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# Основной LLM-провайдер — Unity2 (Gemini). Если UNITY2_API_KEY задан,
# бот сначала ходит в Gemini, а Groq остаётся резервом на случай ошибок/лимитов.
UNITY2_API_KEY = os.getenv("UNITY2_API_KEY", "")
UNITY2_BASE_URL = os.getenv("UNITY2_BASE_URL", "https://api.unity2.ai/v1").rstrip("/")
UNITY2_MODEL = os.getenv("UNITY2_MODEL", "gemini-3-flash-preview")
# Модель для разбора фото (vision). У Groq это Llama 4 Scout.
LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# RSS-лента для новостей в утреннем брифинге (необязательно)
NEWS_RSS_URL = os.getenv("NEWS_RSS_URL", "https://lenta.ru/rss/news")
NEWS_COUNT = int(os.getenv("NEWS_COUNT", "3"))

TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
DB_PATH = os.getenv("DB_PATH", "assistant.db")
# Постоянная база MySQL/MariaDB вместо SQLite-файла.
# Формат: mysql://user:password@host:3306/dbname
MYSQL_URL = os.getenv("MYSQL_URL", "")

# Мини-апп (Telegram Mini App).
# WEBAPP_URL — публичный HTTPS-адрес встроенного веб-сервера бота
# (например, https://mybot.example.com). Если не задан, кнопка мини-аппа
# не показывается, но сервер всё равно поднимается на WEB_PORT.
WEBAPP_URL = os.getenv("WEBAPP_URL", "").rstrip("/")
WEB_PORT = int(os.getenv("WEB_PORT", os.getenv("SERVER_PORT", os.getenv("PORT", "8080"))))
