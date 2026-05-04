"""
Ручне введення чеку через бота.

Використовується як запасний варіант коли фото погане і автосканування не спрацювало.
Користувач вводить дані вручну → система будує URL ДПС → зберігає чек.

FSM: receipt_number → fiscal_number → date → time → amount → category → confirm
"""
import re
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import db, auth_get_by_telegram, save_receipt
from utils.claude_scanner import CATEGORIES

logger = logging.getLogger(__name__)
router = Router()

_CATEGORY_DEFAULT = "📦 Інше"

_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


class AddReceiptStates(StatesGroup):
    receipt_number = State()
    fiscal_number  = State()
    date           = State()
    time           = State()
    amount         = State()
    category       = State()
    confirm        = State()


def _build_qr_url(data: dict) -> str:
    date = data.get("date", "")
    if len(date) < 10:
        return ""
    date_raw = date[6:10] + date[3:5] + date[0:2]
    time_raw = (data.get("time") or "000000").replace(":", "")[:6]
    sm = f"{float(data['amount']):.2f}" if data.get("amount") else "0"
    parts = []
    if date_raw:                    parts.append(f"date={date_raw}")
    if time_raw:                    parts.append(f"time={time_raw}")
    if data.get("receipt_number"):  parts.append(f"id={data['receipt_number']}")
    if sm:                          parts.append(f"sm={sm}")
    if data.get("fiscal_number"):   parts.append(f"fn={data['fiscal_number']}")
    return "https://cabinet.tax.gov.ua/cashregs/check?" + "&".join(parts)


def _skip_keyboard(step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏭ Пропустити", callback_data=f"addskip:{step}"),
        InlineKeyboardButton(text="❌ Скасувати",  callback_data="addskip:cancel"),
    ]])

def _category_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"addcat:{key}")]
        for key, label in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Зберегти",   callback_data="addconfirm:yes"),
        InlineKeyboardButton(text="❌ Скасувати",  callback_data="addconfirm:no"),
    ]])


def _receipt_preview(data: dict) -> str:
    lines = ["📋 Перевірте дані чеку:\n"]
    if data.get("receipt_number"):
        lines.append(f"🔢 Номер чеку: {data['receipt_number']}")
    if data.get("fiscal_number"):
        lines.append(f"🏛 Фіскальний №: {data['fiscal_number']}")
    if data.get("date"):
        dt = data["date"]
        if data.get("time"):
            dt += f"  {data['time']}"
        lines.append(f"📅 Дата: {dt}")
    if data.get("amount"):
        lines.append(f"💰 Сума: {data['amount']:.2f} грн")
    cat = data.get("category", _CATEGORY_DEFAULT)
    lines.append(f"🏷 Категорія: {cat}")
    qr = _build_qr_url(data)
    if qr:
        lines.append(f"🔗 {qr}")
    lines.append("\nВсе правильно?")
    return "\n".join(lines)


# ── /add — старт ────────────────────────────────────────────────

@router.message(Command("manual"))
async def cmd_add(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddReceiptStates.receipt_number)
    await message.answer(
        "✏️ <b>Ручне введення чеку</b>\n\n"
        "Використовуйте якщо фото нечітке і бот не зміг прочитати чек.\n\n"
        "🔢 Введіть номер чеку\n"
        "<i>(номер після слова «ЧЕК №» на чеку)</i>",
        parse_mode="HTML",
        reply_markup=_skip_keyboard("receipt_number"),
    )


# ── Callback: пропустити крок ────────────────────────────────────

@router.callback_query(F.data.startswith("addskip:"))
async def cb_skip(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[1]
    await callback.answer()
    if step == "cancel":
        await state.clear()
        await callback.message.edit_text("↩️ Введення скасовано.")
        return
    # Пропускаємо крок — null значення і переходимо далі
    skip_map = {
        "receipt_number": (None, "receipt_number", AddReceiptStates.fiscal_number,
                           "🏛 Введіть фіскальний номер РРО\n(10 цифр, наприклад: 3000812008)", "fiscal_number"),
        "fiscal_number":  (None, "fiscal_number",  AddReceiptStates.date,
                           "📅 Введіть дату чеку\nФормат: DD.MM.YYYY (наприклад: 27.04.2026)", None),
        "time":           (None, "time",            AddReceiptStates.amount,
                           "💰 Введіть загальну суму чеку\nНаприклад: 73.59", None),
    }
    if step not in skip_map:
        return
    null_val, field, next_state, next_text, next_skip = skip_map[step]
    await state.update_data(**{field: null_val})
    await state.set_state(next_state)
    kb = _skip_keyboard(next_skip) if next_skip else None
    await callback.message.edit_text(next_text, reply_markup=kb)


# ── Крок 1: номер чеку ──────────────────────────────────────────

@router.message(AddReceiptStates.receipt_number)
async def step_receipt_number(message: Message, state: FSMContext):
    await state.update_data(receipt_number=message.text.strip())
    await state.set_state(AddReceiptStates.fiscal_number)
    await message.answer(
        "🏛 Введіть фіскальний номер РРО\n(10 цифр, наприклад: 3000812008)",
        reply_markup=_skip_keyboard("fiscal_number"),
    )


# ── Крок 2: фіскальний номер ────────────────────────────────────

@router.message(AddReceiptStates.fiscal_number)
async def step_fiscal_number(message: Message, state: FSMContext):
    await state.update_data(fiscal_number=message.text.strip())
    await state.set_state(AddReceiptStates.date)
    await message.answer("📅 Введіть дату чеку\nФормат: DD.MM.YYYY (наприклад: 27.04.2026)")


# ── Крок 3: дата ────────────────────────────────────────────────

@router.message(AddReceiptStates.date)
async def step_date(message: Message, state: FSMContext):
    text = message.text.strip()
    if not _DATE_RE.match(text):
        await message.answer("❗ Невірний формат. Введіть дату як DD.MM.YYYY\nНаприклад: 27.04.2026")
        return
    await state.update_data(date=text)
    await state.set_state(AddReceiptStates.time)
    await message.answer(
        "⏱ Введіть час чеку\nФормат: HH:MM або HH:MM:SS (наприклад: 12:25)",
        reply_markup=_skip_keyboard("time"),
    )


# ── Крок 4: час ─────────────────────────────────────────────────

@router.message(AddReceiptStates.time)
async def step_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if _TIME_RE.match(text):
        value = text if text.count(":") == 2 else text + ":00"
    else:
        await message.answer("❗ Введіть час як HH:MM або HH:MM:SS")
        return
    await state.update_data(time=value)
    await state.set_state(AddReceiptStates.amount)
    await message.answer("💰 Введіть загальну суму чеку\nНаприклад: 73.59")


# ── Крок 5: сума ────────────────────────────────────────────────

@router.message(AddReceiptStates.amount)
async def step_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Введіть суму числом, наприклад: 73.59")
        return
    await state.update_data(amount=amount)
    await state.set_state(AddReceiptStates.category)
    await message.answer(
        "🏷 Оберіть категорію витрат:",
        reply_markup=_category_keyboard()
    )


# ── Крок 6: категорія (callback) ────────────────────────────────

@router.callback_query(F.data.startswith("addcat:"), AddReceiptStates.category)
async def step_category(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[1]
    cat = CATEGORIES.get(key, _CATEGORY_DEFAULT)
    await state.update_data(category=cat)
    await state.set_state(AddReceiptStates.confirm)
    data = await state.get_data()
    await callback.message.edit_text(
        _receipt_preview(data),
        reply_markup=_confirm_keyboard(),
    )
    await callback.answer()


# ── Крок 7: підтвердження ────────────────────────────────────────

@router.callback_query(F.data.startswith("addconfirm:"), AddReceiptStates.confirm)
async def step_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    if action == "no":
        await state.clear()
        await callback.message.edit_text("↩️ Введення скасовано.")
        await callback.answer()
        return

    data = await state.get_data()
    auth = await auth_get_by_telegram(callback.from_user.id)
    if not auth:
        await callback.message.edit_text("❌ Помилка авторизації.")
        await callback.answer()
        return

    receipt_id, _ = await save_receipt(
        auth_id=auth["id"],
        receipt_number=data.get("receipt_number"),
        fiscal_number=data.get("fiscal_number"),
        receipt_date=data.get("date"),
        receipt_time=data.get("time"),
        amount=data.get("amount"),
        category=data.get("category"),
        qr_link=_build_qr_url(data) or None,
    )

    await state.clear()
    await callback.message.edit_text(
        f"✅ Чек збережено!\n\n"
        f"Переглянути та відредагувати можна у веб-застосунку.\n\n"
        f"/menu"
    )
    await callback.answer()


# ── /cancel — скасування в будь-якому стані ─────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current and current.startswith("AddReceiptStates"):
        await state.clear()
        await message.answer("↩️ Введення скасовано.")
    else:
        await message.answer("Немає активного процесу для скасування.")
