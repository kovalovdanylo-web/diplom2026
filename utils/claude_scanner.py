import base64
import io
import json
import logging
import re
from urllib.parse import urlparse, parse_qs

import anthropic
from PIL import Image, ImageFilter

try:
    from pyzbar.pyzbar import decode as decode_qr
    HAS_PYZBAR = True
except ImportError:
    HAS_PYZBAR = False

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TAX_CABINET_HOST = "cabinet.tax.gov.ua"

# Категорії витрат
CATEGORIES = {
    "food":          "🛒 Продукти",
    "transport":     "⛽ Транспорт",
    "cafe":          "🍽 Кафе/Ресторан",
    "pharmacy":      "💊 Аптека",
    "entertainment": "🎭 Розваги",
    "clothing":      "👕 Одяг",
    "household":     "🏠 Побут",
    "other":         "📦 Інше",
}
CATEGORY_KEYS = "/".join(CATEGORIES.keys())  # для промпту
CATEGORY_DEFAULT = "📦 Інше"

# --- Промпт для повного розпізнавання через Claude ---
# c = категорія (одне слово з CATEGORY_KEYS)
RECEIPT_PROMPT = f"""Фото українського чеку. Витягни дані з КОЖНОГО чеку на фото.
rn=номер чеку (після ЧЕК №), fn=фіскальний номер (після ФН, 10 цифр), sn=заводський номер (після ЗН), d=дата DD.MM.YYYY, t=час HH:MM:SS, a=сума числом (підсумок), q=URL з QR або null, c=категорія ({CATEGORY_KEYS}).
Відсутнє=null. Сума завжди число: 27.50. Тільки JSON, без пояснень.
[{{"rn":"...","fn":"...","sn":"...","d":"...","t":"...","a":0.00,"q":null,"c":"food"}}]"""

# --- Мінімальний промпт тільки для визначення категорій (для QR-шляху) ---
CATEGORY_PROMPT = f"""На фото чек(и). Визнач категорію кожного чеку.
Категорії: {CATEGORY_KEYS}.
Якщо чеків кілька — перелічи через кому в порядку зліва направо/зверху вниз.
Відповідай ТІЛЬКИ ключами категорій через кому, без пояснень. Приклад: food або food,transport"""


# ============================================================
# 1. QR-код: витягування з фото
# ============================================================

def _extract_qr_links(image_bytes: bytes) -> list[str]:
    """
    Витягує URL з QR-кодів на фото за допомогою pyzbar.

    Пробує серію обробок зображення від м'яких до агресивних,
    щоб розпізнати QR навіть на поганих фото (тіні, нахил, розмиття).
    """
    if not HAS_PYZBAR:
        logger.debug("pyzbar не встановлено, QR-парсинг пропущено")
        return []
    try:
        img = Image.open(io.BytesIO(image_bytes))
        gray = img.convert("L")

        # Серія спроб: від найменш агресивної до найбільш
        attempts = [
            ("original", img),
            ("grayscale", gray),
            ("upscale2x", gray.resize(
                (gray.width * 2, gray.height * 2), Image.LANCZOS,
            )),
        ]

        # Бінаризація з різними порогами
        for t in (100, 120, 140):
            attempts.append((
                f"binary_t{t}",
                gray.point(lambda x, t=t: 255 if x > t else 0),
            ))

        # Unsharp mask + бінаризація (допомагає при розмитті та тінях)
        unsharp = gray.filter(
            ImageFilter.UnsharpMask(radius=3, percent=200, threshold=0),
        )
        for t in (120, 140):
            attempts.append((
                f"unsharp_binary_t{t}",
                unsharp.point(lambda x, t=t: 255 if x > t else 0),
            ))

        all_urls = set()
        for name, processed in attempts:
            results = decode_qr(processed)
            for r in results:
                text = r.data.decode("utf-8", errors="ignore").strip()
                if text.startswith("http"):
                    all_urls.add(text)

            if all_urls:
                logger.info(
                    "QR знайдено на етапі '%s': %d URL(s)", name, len(all_urls),
                )

        for url in all_urls:
            logger.info("QR URL: %s", url)

        return list(all_urls)

    except Exception as e:
        logger.warning("Помилка QR-парсингу: %s", e)
        return []


def _is_tax_cabinet_url(url: str) -> bool:
    """Перевіряє чи URL веде на cabinet.tax.gov.ua."""
    try:
        parsed = urlparse(url)
        return parsed.hostname == TAX_CABINET_HOST
    except Exception:
        return False


# ============================================================
# 2. Парсинг даних з URL параметрів QR-коду ДПС
# ============================================================

def _parse_tax_qr_url(url: str) -> dict | None:
    """
    Парсить дані чеку прямо з URL параметрів QR-коду ДПС.

    Формат URL:
    https://cabinet.tax.gov.ua/cashregs/check?mac=...&date=YYYYMMDD&time=HHMM&id=NUM&sm=AMOUNT&fn=FISCAL_NUM

    Повертає dict з даними чеку або None якщо URL невалідний.
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # Витягуємо параметри (parse_qs повертає списки)
        fn = params.get("fn", [None])[0]
        receipt_id = params.get("id", [None])[0]
        date_raw = params.get("date", [None])[0]
        time_raw = params.get("time", [None])[0]
        amount_raw = params.get("sm", [None])[0]

        # Нормалізація дати: YYYYMMDD -> DD.MM.YYYY
        receipt_date = None
        if date_raw and len(date_raw) == 8:
            receipt_date = f"{date_raw[6:8]}.{date_raw[4:6]}.{date_raw[0:4]}"

        # Нормалізація часу: HHMM -> HH:MM:SS
        receipt_time = None
        if time_raw and len(time_raw) >= 4:
            receipt_time = f"{time_raw[0:2]}:{time_raw[2:4]}:00"

        # Сума
        amount = None
        if amount_raw:
            try:
                amount = float(amount_raw)
            except ValueError:
                pass

        result = {
            "receipt_number": receipt_id,
            "fiscal_number": fn,
            "serial_number": None,
            "receipt_date": receipt_date,
            "receipt_time": receipt_time,
            "amount": amount,
            "qr_link": url,
        }

        logger.info(
            "QR URL розпарсено: ФН=%s, дата=%s, час=%s, сума=%s",
            fn, receipt_date, receipt_time, amount,
        )
        return result

    except Exception as e:
        logger.warning("Помилка парсингу QR URL: %s", e)
        return None


# ============================================================
# 3. Claude Vision API (fallback)
# ============================================================

def _prepare_image(image_bytes: bytes, max_side: int = 1200) -> tuple[str, str]:
    """Стискає фото та конвертує в base64."""
    img = Image.open(io.BytesIO(image_bytes))

    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.info("Зображення стиснуто: %dx%d -> %dx%d", w, h, new_w, new_h)

    buf = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=60)

    b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return b64, "image/jpeg"


def _safe_float(value) -> float | None:
    """Безпечна конвертація в float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).replace(",", ".").replace(" ", "").lower()
        cleaned = cleaned.replace("грн", "").replace("uah", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _normalize_date(value: str | None) -> str | None:
    """Нормалізує дату до DD.MM.YYYY."""
    if not value:
        return None
    normalized = value.replace("-", ".")
    parts = normalized.split(".")
    if len(parts) == 3 and len(parts[2]) == 2:
        parts[2] = "20" + parts[2]
        normalized = ".".join(parts)
    return normalized


def _normalize_time(value: str | None) -> str | None:
    """Нормалізує час до HH:MM:SS."""
    if not value:
        return None
    return value.replace("-", ":").replace(".", ":")


def _parse_claude_response(raw_text: str) -> list[dict]:
    """Парсить JSON відповідь від Claude."""
    text = raw_text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    data = json.loads(text)

    if isinstance(data, dict):
        data = [data]

    results = []
    for item in data:
        def g(short, full):
            return item.get(short) or item.get(full)

        results.append({
            "receipt_number": g("rn", "receipt_number"),
            "fiscal_number": g("fn", "fiscal_number"),
            "serial_number": g("sn", "serial_number"),
            "receipt_date": _normalize_date(g("d", "receipt_date")),
            "receipt_time": _normalize_time(g("t", "receipt_time")),
            "amount": _safe_float(g("a", "amount")),
            "qr_link": g("q", "qr_link"),
            "category": CATEGORIES.get(
                (g("c", "category") or "").strip().lower(),
                CATEGORY_DEFAULT,
            ),
        })

    return results


def _classify_categories(image_bytes: bytes, count: int) -> list[str]:
    """
    Визначає категорії для чеків на фото через мінімальний запит до Claude.
    Використовується тільки для QR-шляху (дані вже є, потрібна тільки категорія).
    Повертає список категорій довжиною count.
    """
    try:
        b64_image, media_type = _prepare_image(image_bytes)

        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=32,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_image,
                            },
                        },
                        {"type": "text", "text": CATEGORY_PROMPT},
                    ],
                }
            ],
        )

        raw = message.content[0].text.strip().lower()
        logger.info(
            "Категорії визначено: '%s' (tokens: %d in, %d out)",
            raw, message.usage.input_tokens, message.usage.output_tokens,
        )

        # Парсимо відповідь — очікуємо "food" або "food,transport"
        keys = [k.strip() for k in raw.split(",")]
        categories = [CATEGORIES.get(k, CATEGORY_DEFAULT) for k in keys]

        # Вирівнюємо до потрібної кількості
        while len(categories) < count:
            categories.append(CATEGORY_DEFAULT)

        return categories[:count], message.usage.input_tokens, message.usage.output_tokens

    except Exception as e:
        logger.warning("Помилка визначення категорії: %s", e)
        return [CATEGORY_DEFAULT] * count, 0, 0


def _scan_with_claude(image_bytes: bytes, qr_links: list[str] = None) -> dict:
    """Розпізнавання через Claude Vision API (fallback)."""
    b64_image, media_type = _prepare_image(image_bytes)

    prompt = RECEIPT_PROMPT
    if qr_links:
        prompt += f"\nQR з фото: {', '.join(qr_links)}"

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    raw_response = message.content[0].text
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens

    logger.info(
        "Claude відповів: %d input, %d output токенів",
        input_tokens, output_tokens,
    )

    receipts = _parse_claude_response(raw_response)

    # Доповнюємо qr_link з pyzbar
    if qr_links:
        for i, r in enumerate(receipts):
            if not r["qr_link"] and i < len(qr_links):
                r["qr_link"] = qr_links[i]

    return {
        "receipts": receipts,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "raw_response": raw_response,
        "source": "claude",
        "success": True,
        "error": None,
    }


# ============================================================
# 5. Головна функція — оркестрація
# ============================================================

def scan_receipt(image_bytes: bytes) -> dict:
    """
    Розпізнавання чеків на фото.

    Пріоритет:
    1. QR-код → парсинг URL параметрів ДПС (безкоштовно)
    2. Fallback → Claude Vision API (платно)
    """
    try:
        # Крок 1: Шукаємо QR-коди на фото
        qr_links = _extract_qr_links(image_bytes)
        tax_links = [url for url in qr_links if _is_tax_cabinet_url(url)]
        other_links = [url for url in qr_links if not _is_tax_cabinet_url(url)]

        # Крок 2: Якщо є QR з ДПС — парсимо дані з URL
        if tax_links:
            receipts = []
            for url in tax_links:
                parsed = _parse_tax_qr_url(url)
                if parsed:
                    receipts.append(parsed)

            if receipts:
                logger.info(
                    "Розпізнано %d чек(ів) з QR-коду (без Claude API)",
                    len(receipts),
                )

                # Визначаємо категорії окремим мінімальним запитом
                categories, in_tok, out_tok = _classify_categories(
                    image_bytes, len(receipts),
                )
                for receipt, cat in zip(receipts, categories):
                    receipt["category"] = cat

                return {
                    "receipts": receipts,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "raw_response": json.dumps(
                        [{"qr_url": r["qr_link"]} for r in receipts],
                        ensure_ascii=False,
                    ),
                    "source": "qr",
                    "success": True,
                    "error": None,
                }

        # Крок 3: Fallback — Claude Vision API
        logger.info("QR ДПС не знайдено, використовую Claude API")
        return _scan_with_claude(image_bytes, qr_links=other_links)

    except json.JSONDecodeError as e:
        logger.error("Помилка парсингу JSON від Claude: %s", e)
        return {
            "receipts": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "raw_response": "",
            "source": "error",
            "success": False,
            "error": f"Помилка розпізнавання відповіді AI: {e}",
        }
    except anthropic.APIError as e:
        logger.error("Помилка API Anthropic: %s", e)
        return {
            "receipts": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "raw_response": "",
            "source": "error",
            "success": False,
            "error": f"Помилка API: {e}",
        }
    except Exception as e:
        logger.error("Неочікувана помилка сканування: %s", e, exc_info=True)
        return {
            "receipts": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "raw_response": "",
            "source": "error",
            "success": False,
            "error": f"Помилка: {e}",
        }
