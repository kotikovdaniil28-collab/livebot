"""
Бот «Личный Ассистент Дня» — точка входа.

Функции (полный план):
- Задачи и напоминания свободной фразой («напомни завтра в 9 позвонить маме»)
- Фото холодильника → список продуктов + 2–3 рецепта (LLM vision)
- Список продуктов текстом → рецепты + кнопка «докупить недостающее»
- Единый список покупок (/list, /buy, /bought) с шарингом (/share, /join)
- Учёт расходов одной строкой («кофе 250») + итоги (/spent)
- Трекер привычек с напоминаниями (/habit, /habits)
- Виртуальный холодильник и сроки годности (/fridge) + рецепты «спасения»
- Меню на неделю + общий список покупок (/menu)
- Утренний брифинг: погода + задачи + новости + сроки годности (/time, /digest)
- Вечерний итог: выполненное, перенос, оценка дня 1–10 (/eveningtime, /evening)

Запуск: python3 main.py (предварительно заполни .env — см. README.md)
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import BOT_TOKEN, LLM_API_KEY, WEBAPP_URL
from db import init_db
from handlers import build_router
from scheduler import background_loop
from webapp import start_webapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан BOT_TOKEN — заполни файл .env (см. README.md)")
    if not LLM_API_KEY:
        raise SystemExit("❌ Не задан GROQ_API_KEY — заполни файл .env (см. README.md)")
    init_db()
    bot = Bot(token=BOT_TOKEN)
    await bot.set_my_commands(
        [
            BotCommand(command="tasks", description="Задачи"),
            BotCommand(command="list", description="Список покупок"),
            BotCommand(command="fridge", description="Холодильник"),
            BotCommand(command="habits", description="Привычки"),
            BotCommand(command="spent", description="Расходы"),
            BotCommand(command="menu", description="Меню на неделю"),
            BotCommand(command="digest", description="Утренний брифинг"),
            BotCommand(command="evening", description="Итог дня"),
            BotCommand(command="mood", description="Дневник настроения"),
            BotCommand(command="app", description="Мини-апп"),
            BotCommand(command="help", description="Справка"),
        ]
    )
    # Кнопка мини-аппа слева от поля ввода (если задан публичный HTTPS-адрес)
    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Мини-апп", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    web_runner = await start_webapp()
    dp = Dispatcher()
    dp.include_router(build_router())
    loop_task = asyncio.create_task(background_loop(bot))
    log.info("Бот запущен (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        loop_task.cancel()
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
