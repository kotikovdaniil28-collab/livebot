"""Сборка всех роутеров. Порядок важен: text_router — последним."""

from aiogram import Router

from handlers import basic, kitchen, life, menu, shopping, tasks, text_router


def build_router() -> Router:
    root = Router()
    root.include_router(basic.router)
    root.include_router(menu.router)  # кнопки главного меню — до text_router
    root.include_router(tasks.router)
    root.include_router(shopping.router)
    root.include_router(life.router)
    root.include_router(kitchen.router)
    root.include_router(text_router.router)  # свободный текст — всегда последний
    return root
