import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database import auth_is_logged_in

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """
    Блокує всі повідомлення від незалогінених користувачів.
    FSM-стани реєстрації/логіну пропускаються (окремий auth_router).
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return

        # Користувач в FSM-флоу (реєстрація/логін) — пропускаємо
        state: FSMContext = data.get("state")
        if state and await state.get_state() is not None:
            return await handler(event, data)

        # Залогінений — пропускаємо
        if await auth_is_logged_in(user.id):
            return await handler(event, data)

        # Не залогінений — підказуємо натиснути /start
        logger.debug("Заблоковано незалогіненого: tg_id=%d", user.id)
        await event.answer(
            "👋 Привіт! Натисніть /start щоб розпочати."
        )
