import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    get_total_stats,
    auth_set_logged_in,
    auth_get_by_telegram,
    delete_receipt,
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
        "/add — додати чек\n"
        "/total — статистика витрат\n"
        "/help — довідка\n"
        "/logout — вийти з акаунту",
        parse_mode="HTML",
    )


@router.message(Command("add"))
async def cmd_add(message: Message):
    """Підменю додавання чеку."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📸 Сканувати фото", callback_data="add_method:scan"),
        InlineKeyboardButton(text="✏️ Вручну",         callback_data="add_method:manual"),
    ]])
    await message.answer(
        "➕ <b>Додати чек</b>\n\nОберіть спосіб введення:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("add_method:"))
async def cb_add_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split(":")[1]
    await callback.answer()
    if method == "scan":
        await callback.message.edit_text(
            "📸 Надішліть фото чеку — бот розпізнає його автоматично.\n\n"
            "Поради:\n"
            "• Фотографуйте при хорошому освітленні\n"
            "• Тримайте камеру рівно над чеком\n"
            "• На одному фото може бути кілька чеків\n\n"
            "Якщо фото нечітке — спробуйте /add → Вручну"
        )
    else:
        # Запускаємо FSM ручного вводу
        from bot.handlers.manual import _skip_keyboard, AddReceiptStates
        await state.clear()
        await state.set_state(AddReceiptStates.receipt_number)
        await callback.message.edit_text(
            "✏️ <b>Ручне введення чеку</b>\n\n"
            "🔢 Введіть номер чеку\n"
            "<i>(номер після «ЧЕК №» на чеку)</i>",
            parse_mode="HTML",
            reply_markup=_skip_keyboard("receipt_number"),
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
        "/add — додати чек (фото або вручну)\n"
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


@router.callback_query(F.data.startswith("del_ask:"))
async def cb_del_ask(callback: CallbackQuery):
    """Перший крок: запит підтвердження видалення."""
    receipt_id = int(callback.data.split(":")[1])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, видалити", callback_data=f"del_yes:{receipt_id}"),
        InlineKeyboardButton(text="❌ Скасувати",    callback_data="del_no"),
    ]])

    await callback.answer()
    await callback.message.answer(
        f"⚠️ Чек буде видалено з бази даних. Це незворотно.\n\n"
        f"Підтвердити видалення?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("del_yes:"))
async def cb_del_yes(callback: CallbackQuery):
    """Другий крок: підтверджено — видаляємо."""
    receipt_id = int(callback.data.split(":")[1])

    auth = await auth_get_by_telegram(callback.from_user.id)
    if not auth:
        await callback.answer("❌ Помилка авторизації", show_alert=True)
        return

    deleted = await delete_receipt(auth_id=auth["id"], receipt_id=receipt_id)

    if deleted:
        await callback.message.edit_text("✅ Чек успішно видалено.")
    else:
        await callback.message.edit_text("❌ Чек не знайдено або вже видалено.")

    await callback.answer()


@router.callback_query(F.data == "del_no")
async def cb_del_no(callback: CallbackQuery):
    """Скасування видалення."""
    await callback.message.edit_text("↩️ Видалення скасовано.")
    await callback.answer()


@router.message(F.text, StateFilter(None))
async def catch_text(message: Message):
    """Catch-all для тексту поза FSM-станами."""
    await message.answer(
        "📸 Надішліть фото чеку або:\n"
        "/add — обрати спосіб додавання\n"
        "/menu — всі команди"
    )
