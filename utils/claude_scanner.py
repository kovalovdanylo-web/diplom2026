"""
Сканер фіскальних чеків.

ReceiptScanner — основний клас з усією логікою:
  • _extract_qr_links  — пошук QR-кодів на фото
  • _parse_tax_qr_url  — парсинг URL параметрів ДПС
  • _classify_category — визначення категорії через Claude (мінімальний запит)
  • _scan_with_claude  — повне розпізнавання через Claude Vision
  • scan               — оркестратор: QR → Claude fallback
"""
import base64
import io
import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

import anthropic
from PIL import Image, ImageFilter

try:
    from pyzbar.pyzbar import decode as decode_qr
    _HAS_PYZBAR = True
except ImportError:
    _HAS_PYZBAR = False

from config import config

logger = logging.getLogger(__name__)

_TAX_CABINET_HOST = "cabinet.tax.gov.ua"

CATEGORIES: dict[str, str] = {
    "food":          "🛒 Продукти",
    "transport":     "⛽ Транспорт",
    "cafe":          "🍽 Кафе/Ресторан",
    "pharmacy":      "💊 Аптека",
    "entertainment": "🎭 Розваги",
    "clothing":      "👕 Одяг",
    "household":     "🏠 Побут",
    "other":         "📦 Інше",
}
_CATEGORY_KEYS    = "/".join(CATEGORIES.keys())
_CATEGORY_DEFAULT = "📦 Інше"


class ReceiptScanner:
    """
    Сканер фіскальних чеків.

    Пріоритет розпізнавання:
    1. QR-код → парсинг URL параметрів ДПС (безкоштовно)
    2. Fallback → Claude Vision API (платно)
    """

    _RECEIPT_PROMPT = (
        f"Фото українського чеку. Витягни дані з КОЖНОГО чеку на фото.\n"
        f"rn=номер чеку, fn=фіскальний номер (10 цифр), sn=заводський номер, "
        f"d=дата DD.MM.YYYY, t=час HH:MM:SS, a=сума числом, q=URL з QR або null, "
        f"c=категорія ({_CATEGORY_KEYS}).\n"
        f"Відсутнє=null. Тільки JSON без пояснень.\n"
        f'[{{"rn":"...","fn":"...","sn":"...","d":"...","t":"...","a":0.00,"q":null,"c":"food"}}]'
    )

    _CATEGORY_PROMPT = (
        f"На фото чек(и). Визнач категорію кожного.\n"
        f"Категорії: {_CATEGORY_KEYS}.\n"
        f"Якщо чеків кілька — перелічи через кому зліва направо.\n"
        f"Відповідай ТІЛЬКИ ключами через кому. Приклад: food або food,transport"
    )

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model  = model

    # ── Публічний інтерфейс ──────────────────────────────────────

    def scan(self, image_bytes: bytes) -> dict:
        """
        Сканує зображення і повертає список розпізнаних чеків.

        Returns:
            dict з ключами: receipts, input_tokens, output_tokens,
                            source, success, error
        """
        try:
            qr_links  = self._extract_qr_links(image_bytes)
            tax_links = [u for u in qr_links if self._is_tax_url(u)]
            other     = [u for u in qr_links if not self._is_tax_url(u)]

            if tax_links:
                receipts = [self._parse_tax_qr_url(u) for u in tax_links]
                receipts = [r for r in receipts if r]

                if receipts:
                    cats, in_tok, out_tok = self._classify_category(
                        image_bytes, len(receipts)
                    )
                    for r, cat in zip(receipts, cats):
                        r["category"] = cat

                    logger.info("Розпізнано %d чек(ів) з QR", len(receipts))
                    return self._make_result(receipts, "qr", in_tok, out_tok)

            logger.info("QR ДПС не знайдено, використовую Claude API")
            return self._scan_with_claude(image_bytes, other)

        except json.JSONDecodeError as e:
            return self._make_error(f"Помилка парсингу відповіді AI: {e}")
        except anthropic.APIError as e:
            return self._make_error(f"Помилка API Anthropic: {e}")
        except Exception as e:
            logger.error("Неочікувана помилка сканування: %s", e, exc_info=True)
            return self._make_error(f"Помилка: {e}")

    # ── Приватні методи ──────────────────────────────────────────

    def _extract_qr_links(self, image_bytes: bytes) -> list[str]:
        """Витягує URL з QR-кодів на фото за допомогою pyzbar."""
        if not _HAS_PYZBAR:
            return []
        try:
            img  = Image.open(io.BytesIO(image_bytes))
            gray = img.convert("L")

            attempts = [
                ("original",  img),
                ("grayscale", gray),
                ("upscale2x", gray.resize(
                    (gray.width * 2, gray.height * 2), Image.LANCZOS
                )),
            ]
            for t in (100, 120, 140):
                attempts.append((f"binary_t{t}", gray.point(lambda x, t=t: 255 if x > t else 0)))

            unsharp = gray.filter(ImageFilter.UnsharpMask(radius=3, percent=200, threshold=0))
            for t in (120, 140):
                attempts.append((f"unsharp_t{t}", unsharp.point(lambda x, t=t: 255 if x > t else 0)))

            urls: set[str] = set()
            for name, processed in attempts:
                for r in decode_qr(processed):
                    text = r.data.decode("utf-8", errors="ignore").strip()
                    if text.startswith("http"):
                        urls.add(text)
                if urls:
                    logger.info("QR знайдено на етапі '%s': %d URL(s)", name, len(urls))

            return list(urls)
        except Exception as e:
            logger.warning("Помилка QR-парсингу: %s", e)
            return []

    def _is_tax_url(self, url: str) -> bool:
        """Перевіряє чи URL веде на cabinet.tax.gov.ua."""
        try:
            return urlparse(url).hostname == _TAX_CABINET_HOST
        except Exception:
            return False

    def _parse_tax_qr_url(self, url: str) -> Optional[dict]:
        """Парсить дані чеку з URL параметрів ДПС."""
        try:
            params = parse_qs(urlparse(url).query)
            fn         = params.get("fn",   [None])[0]
            receipt_id = params.get("id",   [None])[0]
            date_raw   = params.get("date", [None])[0]
            time_raw   = params.get("time", [None])[0]
            amount_raw = params.get("sm",   [None])[0]

            receipt_date = (
                f"{date_raw[6:8]}.{date_raw[4:6]}.{date_raw[0:4]}"
                if date_raw and len(date_raw) == 8 else None
            )
            receipt_time = (
                f"{time_raw[0:2]}:{time_raw[2:4]}:00"
                if time_raw and len(time_raw) >= 4 else None
            )
            amount = None
            if amount_raw:
                try:
                    amount = float(amount_raw)
                except ValueError:
                    pass

            logger.info("QR розпарсено: ФН=%s, дата=%s, сума=%s", fn, receipt_date, amount)
            return {
                "receipt_number": receipt_id,
                "fiscal_number":  fn,
                "serial_number":  None,
                "receipt_date":   receipt_date,
                "receipt_time":   receipt_time,
                "amount":         amount,
                "qr_link":        url,
            }
        except Exception as e:
            logger.warning("Помилка парсингу QR URL: %s", e)
            return None

    def _classify_category(self, image_bytes: bytes, count: int) -> tuple:
        """Визначає категорії через мінімальний запит до Claude."""
        try:
            b64, media = self._prepare_image(image_bytes)
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=32,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                    {"type": "text",  "text": self._CATEGORY_PROMPT},
                ]}],
            )
            raw  = msg.content[0].text.strip().lower()
            cats = [CATEGORIES.get(k.strip(), _CATEGORY_DEFAULT) for k in raw.split(",")]
            while len(cats) < count:
                cats.append(_CATEGORY_DEFAULT)
            return cats[:count], msg.usage.input_tokens, msg.usage.output_tokens
        except Exception as e:
            logger.warning("Помилка визначення категорії: %s", e)
            return [_CATEGORY_DEFAULT] * count, 0, 0

    def _scan_with_claude(self, image_bytes: bytes, qr_links: list[str] = None) -> dict:
        """Повне розпізнавання через Claude Vision API."""
        b64, media = self._prepare_image(image_bytes)
        prompt = self._RECEIPT_PROMPT
        if qr_links:
            prompt += f"\nQR з фото: {', '.join(qr_links)}"

        msg = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text",  "text": prompt},
            ]}],
        )
        raw_text     = msg.content[0].text
        in_tok       = msg.usage.input_tokens
        out_tok      = msg.usage.output_tokens
        logger.info("Claude: %d in / %d out токенів", in_tok, out_tok)

        receipts = self._parse_claude_response(raw_text)
        if qr_links:
            for i, r in enumerate(receipts):
                if not r["qr_link"] and i < len(qr_links):
                    r["qr_link"] = qr_links[i]

        return self._make_result(receipts, "claude", in_tok, out_tok, raw_text)

    # ── Допоміжні ────────────────────────────────────────────────

    @staticmethod
    def _prepare_image(image_bytes: bytes, max_side: int = 1200) -> tuple[str, str]:
        """Стискає фото та повертає (base64, media_type)."""
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=60)
        return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(",", ".").replace(" ", "")
                         .replace("грн", "").replace("uah", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_date(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        normalized = value.replace("-", ".")
        parts = normalized.split(".")
        if len(parts) == 3 and len(parts[2]) == 2:
            parts[2] = "20" + parts[2]
        return ".".join(parts)

    @staticmethod
    def _normalize_time(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return value.replace("-", ":").replace(".", ":")

    def _parse_claude_response(self, raw_text: str) -> list[dict]:
        """Парсить JSON-відповідь від Claude."""
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text).strip()
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]

        results = []
        for item in data:
            def g(s, f):
                return item.get(s) or item.get(f)
            results.append({
                "receipt_number": g("rn", "receipt_number"),
                "fiscal_number":  g("fn", "fiscal_number"),
                "serial_number":  g("sn", "serial_number"),
                "receipt_date":   self._normalize_date(g("d", "receipt_date")),
                "receipt_time":   self._normalize_time(g("t", "receipt_time")),
                "amount":         self._safe_float(g("a", "amount")),
                "qr_link":        g("q", "qr_link"),
                "category":       CATEGORIES.get(
                    (g("c", "category") or "").strip().lower(), _CATEGORY_DEFAULT
                ),
            })
        return results

    @staticmethod
    def _make_result(receipts, source, in_tok, out_tok, raw="") -> dict:
        return {
            "receipts":      receipts,
            "input_tokens":  in_tok,
            "output_tokens": out_tok,
            "raw_response":  raw,
            "source":        source,
            "success":       True,
            "error":         None,
        }

    @staticmethod
    def _make_error(msg: str) -> dict:
        return {
            "receipts": [], "input_tokens": 0, "output_tokens": 0,
            "raw_response": "", "source": "error", "success": False, "error": msg,
        }


# ─────────────────────────────────────────────────────────────
# Єдиний екземпляр (Singleton)
# ─────────────────────────────────────────────────────────────

scanner = ReceiptScanner(
    api_key=config.anthropic_api_key,
    model=config.claude_model,
)


def scan_receipt(image_bytes: bytes) -> dict:
    """Зворотна сумісність зі старим API."""
    return scanner.scan(image_bytes)
