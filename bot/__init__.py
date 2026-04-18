from aiogram import Router

from bot.handlers import commands, photo
from bot.handlers.auth import router as auth_router
from bot.middleware import AuthMiddleware


def get_router() -> Router:
    """
    Повертає головний роутер.

    Структура:
    - auth_router: /start, реєстрація, логін — БЕЗ middleware (завжди доступні)
    - main_router: всі інші хендлери — З AuthMiddleware (тільки для залогінених)
    """
    main_router = Router()
    main_router.message.middleware(AuthMiddleware())
    main_router.include_router(commands.router)
    main_router.include_router(photo.router)

    root = Router()
    root.include_router(auth_router)   # першим — без перевірки
    root.include_router(main_router)   # другим — з перевіркою

    return root
