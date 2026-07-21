"""Конфигурация бота: все настройки читаются из .env."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Gemini API — OpenAI-совместимый endpoint. Ключ бесплатно: https://aistudio.google.com/apikey
# Можно переопределить LLM_BASE_URL/LLM_MODEL и подключить любой другой OpenAI-совместимый API.
LLM_API_KEY = (
    os.getenv("GEMINI_API_KEY", "")
    or os.getenv("AGENTROUTER_API_KEY", "")
    or os.getenv("OPENAI_API_KEY", "")
)
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
).rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# RSS-лента для новостей в утреннем брифинге (необязательно)
NEWS_RSS_URL = os.getenv("NEWS_RSS_URL", "https://lenta.ru/rss/news")
NEWS_COUNT = int(os.getenv("NEWS_COUNT", "3"))

TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
DB_PATH = os.getenv("DB_PATH", "assistant.db")
