import asyncio
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, F, Router
from aiogram.types import Message

from config import CLAUDE_MODEL
from database import register_user, save_receipt, log_api_usage, auth_get_by_telegram
from utils.claude_scanner import scan_receipt

logger = logging.getLogger(__name__)
router = Router()

executor = ThreadPoolExecutor(max_workers=2)


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot):
    """Головний обробник фото чеків."""
    user = message.from_user

    # Отримуємо auth_id — чеки прив'язані до акаунту, не до TG
    auth = await auth_get_by_telegram(user.id)
    if not auth:
        await message.answer("❌ Помилка авторизації. Натисніть /start")
        return
    auth_id = auth["id"]

    # Статусне повідомлення
    status_msg = await message.answer("⏳ Обробляю фото чеку...")

    try:
        # Завантажуємо найбільше фото
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = io.BytesIO()
        await bot.download_file(file.file_path, file_bytes)
        image_bytes = file_bytes.getvalue()

        # Викликаємо scan_receipt в окремому потоці (синхронний API)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, scan_receipt, image_bytes)

        if not result["success"]:
            await status_msg.edit_text(f"❌ {result['error']}")
            return

        if not result["receipts"]:
            await status_msg.edit_text(
                "🤷 Не вдалося знайти чеки на фото.\n"
                "Спробуйте зробити чіткіше фото."
            )
            return

        # Логуємо витрати API (тільки якщо використано Claude)
        source = result.get("source", "claude")
        if result["input_tokens"] > 0:
            await log_api_usage(
                telegram_id=user.id,
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                model=CLAUDE_MODEL,
            )

        total_tokens = result["input_tokens"] + result["output_tokens"]
        receipts = result["receipts"]
        total_count = len(receipts)

        # Зберігаємо кожен чек та формуємо відповідь
        response_parts = []

        new_count = 0
        updated_count = 0

        for i, r in enumerate(receipts, 1):
            receipt_id, is_new = await save_receipt(
                auth_id=auth_id,
                receipt_number=r["receipt_number"],
                fiscal_number=r["fiscal_number"],
                serial_number=r["serial_number"],
                receipt_date=r["receipt_date"],
                receipt_time=r["receipt_time"],
                amount=r["amount"],
                qr_link=r["qr_link"],
                category=r.get("category"),
                raw_claude_json=json.dumps(r, ensure_ascii=False),
                photo_file_id=photo.file_id,
                tokens_used=total_tokens // total_count,
            )

            if is_new:
                new_count += 1
            else:
                updated_count += 1

            # Формуємо текст для одного чеку
            if total_count > 1:
                status = "🆕" if is_new else "🔄"
                part = f"📄 Чек {i} з {total_count} {status}:\n"
            else:
                part = "✅ Чек розпізнано!\n" if is_new else "🔄 Чек оновлено!\n"

            if r["receipt_number"]:
                part += f"🔢 Номер чеку: {r['receipt_number']}\n"
            if r["fiscal_number"]:
                part += f"🏛 Фіскальний №: {r['fiscal_number']}\n"
            if r["serial_number"]:
                part += f"📟 ЗН ПРРО: {r['serial_number']}\n"

            date_time = ""
            if r["receipt_date"]:
                date_time = r["receipt_date"]
            if r["receipt_time"]:
                date_time += f" {r['receipt_time']}"
            if date_time:
                part += f"📅 Дата: {date_time.strip()}\n"

            if r["amount"] is not None:
                part += f"💰 Сума: {r['amount']:.2f} грн\n"

            if r.get("category"):
                part += f"🏷 Категорія: {r['category']}\n"

            if r["qr_link"]:
                part += f"🔗 <a href=\"{r['qr_link']}\">Перевірити на сайті ДПС</a>\n"

            response_parts.append(part)

        # Збираємо повну відповідь
        response = "\n".join(response_parts)

        # Підсумок
        footer_parts = []
        if total_count > 1:
            if new_count:
                footer_parts.append(f"{new_count} нових")
            if updated_count:
                footer_parts.append(f"{updated_count} оновлено")
            response += f"\n📊 Готово! {', '.join(footer_parts)}."

        response += "\n\n/menu"

        await status_msg.edit_text(response, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error("Помилка обробки фото: %s", e, exc_info=True)
        await status_msg.edit_text(
            "❌ Сталася помилка при обробці фото.\n"
            "Спробуйте ще раз або зверніться до /help"
        )
