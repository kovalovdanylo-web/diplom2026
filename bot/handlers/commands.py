import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from database import (
    get_total_stats,
    auth_set_logged_in,
    auth_get_by_telegram,
)

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("logout"))
async def cmd_logout(message: Message):
    """Вихід з акаунту."""
    await auth_set_logged_in(message.from_user.id, False)
    await message.answer(
        "🔒 Ви вийшли з акаунту.\n"
        "Натисніть /start щоб увійти знову."
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    """Головне меню."""
    await message.answer(
        "📋 <b>Меню</b>\n\n"
        "/total — статистика витрат\n"
        "/help — довідка\n"
        "/logout — вийти з акаунту\n\n"
        "📸 Надішліть фото чеку щоб розпочати.",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Докладна довідка по використанню."""
    await message.answer(
        "📖 <b>Як користуватися ботом</b>\n\n"
        "<b>📸 Розпізнавання чеків:</b>\n"
        "Сфотографуйте чек та надішліть фото боту.\n"
        "На одному фото може бути кілька чеків — бот знайде всі.\n\n"
        "<b>💡 Поради для кращого розпізнавання:</b>\n"
        "• Фотографуйте при хорошому освітленні\n"
        "• Тримайте камеру рівно над чеком\n"
        "• Переконайтесь що текст чіткий і читабельний\n"
        "• Уникайте тіней та відблисків\n\n"
        "<b>📋 Команди:</b>\n"
        "/start — привітання та вхід в акаунт\n"
        "/total — загальна статистика витрат\n"
        "/menu — головне меню\n"
        "/help — ця довідка\n"
        "/logout — вийти з акаунту\n\n"
        "<b>📊 Які дані витягуються з чеку:</b>\n"
        "• Номер чеку\n"
        "• Фіскальний номер (ФН)\n"
        "• Заводський номер ПРРО (ЗН)\n"
        "• Дата та час\n"
        "• Загальна сума\n"
        "• Категорія витрат\n"
        "• QR-посилання (якщо є)",
        parse_mode="HTML",
    )


async def _get_auth_id(telegram_id: int) -> int | None:
    auth = await auth_get_by_telegram(telegram_id)
    return auth["id"] if auth else None


@router.message(Command("total"))
async def cmd_total(message: Message):
    """Загальна статистика."""
    auth_id = await _get_auth_id(message.from_user.id)
    stats = await get_total_stats(auth_id)

    if stats["count"] == 0:
        await message.answer("📊 У вас поки немає збережених чеків.\nНадішліть фото чеку, щоб почати!")
        return

    cost_input = stats["input_tokens"] * 1.0 / 1_000_000
    cost_output = stats["output_tokens"] * 5.0 / 1_000_000
    total_cost = cost_input + cost_output

    text = (
        "📊 <b>Статистика витрат</b>\n\n"
        f"🧾 Чеків збережено: {stats['count']}\n"
        f"💰 Загальна сума: {stats['total']:,.2f} грн\n"
    )

    if stats["first_date"]:
        text += f"📅 Перший чек: {stats['first_date']}\n"
    if stats["last_date"]:
        text += f"📅 Останній чек: {stats['last_date']}\n"

    text += f"\n🤖 Витрати API: ~${total_cost:.3f}"

    await message.answer(text, parse_mode="HTML")


@router.message(F.text)
async def catch_text(message: Message):
    """Catch-all для будь-якого тексту — підказка користувачу."""
    await message.answer(
        "📸 Надішліть фото чеку щоб розпочати.\n\n"
        "Доступні команди:\n"
        "/total /menu /help /logout"
    )
