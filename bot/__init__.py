"""
Telegram-бот — точка збирання роутерів.

BotApp — клас застосунку бота:
  • конфігурує Dispatcher
  • підключає роутери (auth без middleware, main з AuthMiddleware)
  • запускає polling
"""
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import commands, photo
from bot.handlers.auth import router as auth_router
from bot.middleware import AuthMiddleware
from config import AppConfig


class BotApp:
    """Клас Telegram-бота."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._bot = Bot(token=cfg.bot_token)
        self._dp  = Dispatcher(storage=MemoryStorage())
        self._dp.include_router(self._build_router())

    # ── Публічний інтерфейс ──────────────────────────────────────

    async def run(self) -> None:
        """Запускає бота в режимі polling."""
        logger = logging.getLogger(__name__)
        logger.info("Бот запущено. Очікування повідомлень...")
        try:
            await self._dp.start_polling(
                self._bot,
                allowed_updates=["message", "callback_query"],
            )
        finally:
            await self._bot.session.close()

    # ── Приватні методи ──────────────────────────────────────────

    def _build_router(self) -> Router:
        """
        Будує дерево роутерів.

        auth_router — /start, реєстрація, логін (без перевірки авторизації)
        main_router — всі інші хендлери (з AuthMiddleware)
        """
        main_router = Router()
        main_router.message.middleware(AuthMiddleware())
        main_router.include_router(commands.router)
        main_router.include_router(photo.router)

        root = Router()
        root.include_router(auth_router)
        root.include_router(main_router)
        return root


# Зворотна сумісність
def get_router() -> Router:
    """Повертає роутер без створення BotApp."""
    main_router = Router()
    main_router.message.middleware(AuthMiddleware())
    main_router.include_router(commands.router)
    main_router.include_router(photo.router)

    root = Router()
    root.include_router(auth_router)
    root.include_router(main_router)
    return root
