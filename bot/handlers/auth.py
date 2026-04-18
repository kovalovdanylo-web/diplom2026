import re
import logging

import bcrypt
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    register_user,
    auth_create,
    auth_get_by_telegram,
    auth_get_by_email,
    auth_set_logged_in,
    auth_switch_telegram,
)

logger = logging.getLogger(__name__)
router = Router()

EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
PASSWORD_MIN_LEN = 8


class RegisterStates(StatesGroup):
    email = State()
    password = State()
    password_confirm = State()


class LoginStates(StatesGroup):
    email = State()
    password = State()


def _auth_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📝 Зареєструватись", callback_data="auth:register"),
            InlineKeyboardButton(text="🔑 Увійти", callback_data="auth:login"),
        ]
    ])


async def _show_auth_prompt(message: Message, state: FSMContext = None) -> None:
    """Показує екран вибору реєстрації/логіну."""
    if state:
        await state.clear()
    await message.answer(
        "👋 Вітаємо у <b>Receipt Bot</b>!\n\n"
        "Для використання необхідно зареєструватись або увійти в акаунт.",
        reply_markup=_auth_keyboard(),
        parse_mode="HTML",
    )


# ── /start ──────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    await register_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    auth = await auth_get_by_telegram(user.id)
    if auth and auth["is_logged_in"]:
        await message.answer(
            f"👋 З поверненням, <b>{user.first_name}</b>!\n\n"
            "📋 Команди:\n"
            "/total — загальна статистика\n"
            "/month — статистика за місяць\n"
            "/last — останні 5 чеків\n"
            "/export — завантажити CSV\n"
            "/logout — вийти з акаунту\n\n"
            "📸 Надішліть фото чеку, щоб почати!",
            parse_mode="HTML",
        )
    else:
        await _show_auth_prompt(message, state)


# ── Вибір: реєстрація або логін ─────────────────────────────

@router.callback_query(F.data == "auth:register")
async def cb_register(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup()
    await state.set_state(RegisterStates.email)
    await callback.message.answer(
        "📝 <b>Реєстрація</b>\n\n"
        "Введіть вашу електронну пошту:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "auth:login")
async def cb_login(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup()
    await state.set_state(LoginStates.email)
    await callback.message.answer(
        "🔑 <b>Вхід</b>\n\n"
        "Введіть вашу електронну пошту:",
        parse_mode="HTML",
    )
    await callback.answer()


# ── Реєстрація: email ────────────────────────────────────────

@router.message(RegisterStates.email)
async def register_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip()

    if not EMAIL_RE.match(email):
        await message.answer("❌ Невірний формат email. Спробуйте ще раз:")
        return

    existing = await auth_get_by_email(email)
    if existing:
        await message.answer(
            "❌ Цей email вже зареєстрований.\n"
            "Спробуйте інший або натисніть /start щоб увійти."
        )
        return

    await state.update_data(email=email)
    await state.set_state(RegisterStates.password)
    await message.answer(
        f"✅ Email: <code>{email}</code>\n\n"
        f"Введіть пароль (мінімум {PASSWORD_MIN_LEN} символів):",
        parse_mode="HTML",
    )


# ── Реєстрація: пароль ───────────────────────────────────────

@router.message(RegisterStates.password)
async def register_password(message: Message, state: FSMContext) -> None:
    password = message.text.strip()
    await message.delete()  # одразу видаляємо пароль з чату

    if len(password) < PASSWORD_MIN_LEN:
        await message.answer(
            f"❌ Пароль надто короткий (мінімум {PASSWORD_MIN_LEN} символів).\n"
            "Введіть пароль ще раз:"
        )
        return

    await state.update_data(password=password)
    await state.set_state(RegisterStates.password_confirm)
    await message.answer("🔁 Підтвердіть пароль (введіть ще раз):")


# ── Реєстрація: підтвердження паролю ────────────────────────

@router.message(RegisterStates.password_confirm)
async def register_confirm(message: Message, state: FSMContext) -> None:
    confirm = message.text.strip()
    await message.delete()

    data = await state.get_data()
    if confirm != data["password"]:
        await message.answer(
            "❌ Паролі не співпадають.\n"
            "Введіть пароль ще раз:"
        )
        await state.set_state(RegisterStates.password)
        return

    # Хешуємо пароль
    password_hash = bcrypt.hashpw(
        data["password"].encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    success = await auth_create(
        telegram_id=message.from_user.id,
        email=data["email"],
        password_hash=password_hash,
    )

    await state.clear()

    if success:
        logger.info("Новий користувач зареєстрований: %s", data["email"])
        await message.answer(
            "✅ <b>Реєстрація успішна!</b>\n\n"
            f"📧 Email: <code>{data['email']}</code>\n\n"
            "Тепер ви можете користуватись ботом.\n"
            "Надішліть фото чеку або оберіть команду:\n\n"
            "/total /month /last /export",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "❌ Помилка реєстрації. Можливо цей email вже зайнятий.\n"
            "Натисніть /start щоб спробувати знову."
        )


# ── Логін: email ─────────────────────────────────────────────

@router.message(LoginStates.email)
async def login_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip()

    if not EMAIL_RE.match(email):
        await message.answer("❌ Невірний формат email. Спробуйте ще раз:")
        return

    auth = await auth_get_by_email(email)
    if not auth:
        await message.answer(
            "❌ Акаунт з таким email не знайдено.\n"
            "Натисніть /start щоб зареєструватись."
        )
        await state.clear()
        return

    await state.update_data(email=email, auth_id=auth["telegram_id"])
    await state.set_state(LoginStates.password)
    await message.answer("🔑 Введіть пароль:")


# ── Логін: пароль ────────────────────────────────────────────

@router.message(LoginStates.password)
async def login_password(message: Message, state: FSMContext) -> None:
    password = message.text.strip()
    await message.delete()

    data = await state.get_data()
    auth = await auth_get_by_email(data["email"])

    if not auth or not bcrypt.checkpw(
        password.encode("utf-8"),
        auth["password_hash"].encode("utf-8"),
    ):
        await message.answer(
            "❌ Невірний пароль.\n"
            "Спробуйте ще раз або натисніть /start."
        )
        await state.clear()
        return

    current_tg_id = message.from_user.id

    # Якщо логіниться з іншого TG-акаунту — переприв'язуємо і переносимо чеки
    await register_user(
        telegram_id=current_tg_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    await auth_switch_telegram(data["email"], current_tg_id)

    await state.clear()

    logger.info("Користувач залогінився: %s (tg_id=%d)", data["email"], current_tg_id)
    await message.answer(
        "✅ <b>Вхід успішний!</b>\n\n"
        "Надішліть фото чеку або оберіть команду:\n\n"
        "/total /month /last /export /logout",
        parse_mode="HTML",
    )
