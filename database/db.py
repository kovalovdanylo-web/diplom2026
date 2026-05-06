"""
База даних — Repository Pattern.

Database — фасад з шістьма репозиторіями:
  • UserRepository         — таблиця users
  • AuthRepository         — таблиця auth
  • ReceiptRepository      — таблиця receipts
  • ApiUsageRepository     — таблиця api_usage
  • ReceiptItemsRepository — таблиця receipt_items (товари з чеків)
  • ReceiptPhotosRepository — таблиця receipt_photos (фото чеків)
"""
import logging
import re
from typing import Optional

import aiosqlite

# ── Маски / валідатори ────────────────────────────────────────────────
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _mask_date(value: Optional[str]) -> Optional[str]:
    """Нормалізує і валідує дату. Відхиляє невалідний формат."""
    if not value:
        return None
    s = value.strip().replace("/", ".")
    # ISO: YYYY-MM-DD або YYYY.MM.DD
    m = re.match(r"^(\d{4})[-.](\d{2})[-.](\d{2})$", s)
    if m:
        result = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
        return result if _DATE_RE.match(result) else None
    # DD-MM-YYYY або DD.MM.YY → DD.MM.YYYY
    m = re.match(r"^(\d{1,2})[-.](\d{1,2})[-.](\d{2,4})$", s)
    if m:
        d, mo, y = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        result = f"{d}.{mo}.{y}"
        return result if _DATE_RE.match(result) else None
    return None


def _mask_time(value: Optional[str]) -> Optional[str]:
    """Нормалізує і валідує час до HH:MM:SS."""
    if not value:
        return None
    s = value.strip().replace(".", ":").replace("-", ":")
    parts = s.split(":")
    if len(parts) == 2:
        result = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
    elif len(parts) == 3:
        result = ":".join(p.zfill(2) for p in parts)
    else:
        return None
    return result if _TIME_RE.match(result) else None

from config import DB_PATH

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Базовий репозиторій
# ─────────────────────────────────────────────────────────────

class _BaseRepository:
    """Базовий клас репозиторію — спільне підключення до БД."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        return conn


# ─────────────────────────────────────────────────────────────
# Репозиторій користувачів
# ─────────────────────────────────────────────────────────────

class UserRepository(_BaseRepository):
    """Операції з таблицею users."""

    async def register(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        """Реєструє або оновлює запис користувача."""
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    last_name  = excluded.last_name
                """,
                (telegram_id, username, first_name, last_name),
            )
            await db.commit()
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Репозиторій авторизації
# ─────────────────────────────────────────────────────────────

class AuthRepository(_BaseRepository):
    """Операції з таблицею auth."""

    async def create(self, telegram_id: int, email: str, password_hash: str) -> bool:
        """Створює запис авторизації. Повертає False якщо email вже зайнятий."""
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO auth (telegram_id, email, password_hash, is_logged_in)
                VALUES (?, ?, ?, 1)
                """,
                (telegram_id, email.lower().strip(), password_hash),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False
        finally:
            await db.close()

    async def get_by_telegram(self, telegram_id: int) -> Optional[dict]:
        """Повертає запис auth по telegram_id або None."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM auth WHERE telegram_id = ?", (telegram_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def get_by_email(self, email: str) -> Optional[dict]:
        """Повертає запис auth по email або None."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM auth WHERE email = ?", (email.lower().strip(),)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def set_logged_in(self, telegram_id: int, value: bool) -> None:
        """Встановлює статус сесії."""
        db = await self._connect()
        try:
            await db.execute(
                "UPDATE auth SET is_logged_in = ? WHERE telegram_id = ?",
                (1 if value else 0, telegram_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def is_logged_in(self, telegram_id: int) -> bool:
        """Перевіряє чи користувач залогінений."""
        record = await self.get_by_telegram(telegram_id)
        return bool(record and record["is_logged_in"])

    async def switch_telegram(self, email: str, new_telegram_id: int) -> None:
        """Переприв'язує акаунт до нового Telegram ID."""
        db = await self._connect()
        try:
            await db.execute(
                "UPDATE auth SET telegram_id = ?, is_logged_in = 1 WHERE email = ?",
                (new_telegram_id, email.lower().strip()),
            )
            await db.commit()
            logger.info("Акаунт %s переприв'язано до tg_id=%d", email, new_telegram_id)
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Репозиторій чеків
# ─────────────────────────────────────────────────────────────

class ReceiptRepository(_BaseRepository):
    """Операції з таблицею receipts."""

    async def find_existing(
        self,
        auth_id: int,
        receipt_number: Optional[str] = None,
        fiscal_number: Optional[str] = None,
        receipt_date: Optional[str] = None,
        receipt_time: Optional[str] = None,
    ) -> Optional[int]:
        """Шукає дублікат. Повертає ID або None."""
        db = await self._connect()
        try:
            if fiscal_number and receipt_date and receipt_time:
                cursor = await db.execute(
                    """
                    SELECT id FROM receipts
                    WHERE auth_id = ? AND fiscal_number = ?
                      AND receipt_date = ? AND receipt_time = ?
                    LIMIT 1
                    """,
                    (auth_id, fiscal_number, receipt_date, receipt_time),
                )
                row = await cursor.fetchone()
                if row:
                    return row["id"]

            if receipt_number and receipt_date:
                cursor = await db.execute(
                    """
                    SELECT id FROM receipts
                    WHERE auth_id = ? AND receipt_number = ? AND receipt_date = ?
                    LIMIT 1
                    """,
                    (auth_id, receipt_number, receipt_date),
                )
                row = await cursor.fetchone()
                if row:
                    return row["id"]

            return None
        finally:
            await db.close()

    async def save(
        self,
        auth_id: int,
        receipt_number: Optional[str] = None,
        fiscal_number: Optional[str] = None,
        serial_number: Optional[str] = None,
        receipt_date: Optional[str] = None,
        receipt_time: Optional[str] = None,
        amount: Optional[float] = None,
        qr_link: Optional[str] = None,
        category: Optional[str] = None,
        raw_claude_json: Optional[str] = None,
        photo_file_id: Optional[str] = None,
        tokens_used: int = 0,
    ) -> tuple[int, bool]:
        """Зберігає або оновлює чек. Повертає (id, is_new)."""
        # Маски: нормалізація і валідація перед збереженням
        receipt_date = _mask_date(receipt_date)
        receipt_time = _mask_time(receipt_time)
        if amount is not None:
            try:
                amount = round(float(amount), 2)
                if amount < 0:
                    amount = None
            except (TypeError, ValueError):
                amount = None

        existing_id = await self.find_existing(
            auth_id, receipt_number, fiscal_number, receipt_date, receipt_time
        )
        db = await self._connect()
        try:
            if existing_id:
                await db.execute(
                    """
                    UPDATE receipts SET
                        receipt_number  = COALESCE(?, receipt_number),
                        fiscal_number   = COALESCE(?, fiscal_number),
                        serial_number   = COALESCE(?, serial_number),
                        receipt_date    = COALESCE(?, receipt_date),
                        receipt_time    = COALESCE(?, receipt_time),
                        amount          = COALESCE(?, amount),
                        qr_link         = COALESCE(?, qr_link),
                        category        = COALESCE(?, category),
                        raw_claude_json = ?,
                        photo_file_id   = ?,
                        tokens_used     = tokens_used + ?
                    WHERE id = ?
                    """,
                    (
                        receipt_number, fiscal_number, serial_number,
                        receipt_date, receipt_time, amount, qr_link, category,
                        raw_claude_json, photo_file_id, tokens_used,
                        existing_id,
                    ),
                )
                await db.commit()
                logger.info("Чек #%d оновлено (дублікат)", existing_id)
                return existing_id, False
            else:
                cursor = await db.execute(
                    """
                    INSERT INTO receipts (
                        auth_id, receipt_number, fiscal_number, serial_number,
                        receipt_date, receipt_time, amount, qr_link, category,
                        raw_claude_json, photo_file_id, tokens_used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        auth_id, receipt_number, fiscal_number, serial_number,
                        receipt_date, receipt_time, amount, qr_link, category,
                        raw_claude_json, photo_file_id, tokens_used,
                    ),
                )
                await db.commit()
                return cursor.lastrowid, True
        finally:
            await db.close()

    async def set_verified(self, auth_id: int, receipt_id: int, value: bool) -> bool:
        """Встановлює або знімає позначку перевірки чеку."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE receipts SET is_verified=? WHERE id=? AND auth_id=?",
                (1 if value else 0, receipt_id, auth_id),
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def count_unverified(self, auth_id: int) -> int:
        """Кількість неперевірених чеків."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM receipts WHERE auth_id=? AND is_verified=0",
                (auth_id,),
            )
            row = await cursor.fetchone()
            return row[0]
        finally:
            await db.close()

    async def update(self, auth_id: int, receipt_id: int, **fields) -> bool:
        """Оновлює поля чеку. Повертає True якщо запис знайдено."""
        allowed = {
            "receipt_number", "fiscal_number", "serial_number",
            "receipt_date", "receipt_time", "amount",
            "qr_link", "category",
        }
        data = {k: v for k, v in fields.items() if k in allowed}
        if not data:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in data)
        db = await self._connect()
        try:
            cursor = await db.execute(
                f"UPDATE receipts SET {set_clause} WHERE id = ? AND auth_id = ?",
                list(data.values()) + [receipt_id, auth_id],
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def delete(self, auth_id: int, receipt_id: int) -> bool:
        """Видаляє чек. Повертає True якщо видалено."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "DELETE FROM receipts WHERE id = ? AND auth_id = ?",
                (receipt_id, auth_id),
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def get_stats(self, auth_id: int) -> dict:
        """Загальна статистика акаунту."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                """
                SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total,
                       MIN(receipt_date) as first_date, MAX(receipt_date) as last_date
                FROM receipts WHERE auth_id = ?
                """,
                (auth_id,),
            )
            row = await cursor.fetchone()
            cursor2 = await db.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens
                FROM api_usage
                WHERE telegram_id = (SELECT telegram_id FROM auth WHERE id = ?)
                """,
                (auth_id,),
            )
            api_row = await cursor2.fetchone()
            return {
                "count": row["count"],
                "total": row["total"],
                "first_date": row["first_date"],
                "last_date": row["last_date"],
                "input_tokens": api_row["input_tokens"],
                "output_tokens": api_row["output_tokens"],
            }
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Репозиторій товарів у чеку
# ─────────────────────────────────────────────────────────────

class ReceiptItemsRepository(_BaseRepository):
    """Операції з таблицею receipt_items."""

    async def save(self, receipt_id: int, items: list[dict]) -> None:
        """Зберігає список товарів. Перезаписує існуючі для цього чеку."""
        if not items:
            return
        db = await self._connect()
        try:
            await db.execute(
                "DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,)
            )
            await db.executemany(
                """INSERT INTO receipt_items
                       (receipt_id, name, code, quantity, unit_price, total_price)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        receipt_id,
                        i.get("name"),
                        i.get("code"),
                        i.get("quantity"),
                        i.get("unit_price"),
                        i.get("total_price"),
                    )
                    for i in items
                    if i.get("name")
                ],
            )
            await db.commit()
        finally:
            await db.close()

    async def get_by_receipt(self, receipt_id: int) -> list[dict]:
        """Повертає всі товари для чеку."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY id",
                (receipt_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Репозиторій фото чеків
# ─────────────────────────────────────────────────────────────

class ReceiptPhotosRepository(_BaseRepository):
    """Операції з таблицею receipt_photos."""

    async def save(self, receipt_id: int, file_path: str, photo_file_id: str = None) -> None:
        """Додає запис про фото чеку."""
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO receipt_photos (receipt_id, file_path, photo_file_id)
                   VALUES (?, ?, ?)""",
                (receipt_id, file_path, photo_file_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_by_receipt(self, receipt_id: int) -> list[dict]:
        """Повертає всі фото для чеку (від нового до старого)."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM receipt_photos WHERE receipt_id = ? ORDER BY id DESC",
                (receipt_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Репозиторій використання API
# ─────────────────────────────────────────────────────────────

class ApiUsageRepository(_BaseRepository):
    """Операції з таблицею api_usage."""

    async def log(
        self,
        telegram_id: int,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None:
        """Записує витрати API."""
        db = await self._connect()
        try:
            await db.execute(
                """
                INSERT INTO api_usage (telegram_id, input_tokens, output_tokens, model)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, input_tokens, output_tokens, model),
            )
            await db.commit()
        finally:
            await db.close()


# ─────────────────────────────────────────────────────────────
# Фасад Database
# ─────────────────────────────────────────────────────────────

class Database:
    """
    Фасад для роботи з базою даних.
    Надає доступ до всіх репозиторіїв через єдину точку входу.
    """

    def __init__(self, db_path: str) -> None:
        self._path   = db_path
        self.users    = UserRepository(db_path)
        self.auth     = AuthRepository(db_path)
        self.receipts = ReceiptRepository(db_path)
        self.api      = ApiUsageRepository(db_path)
        self.items    = ReceiptItemsRepository(db_path)
        self.photos   = ReceiptPhotosRepository(db_path)

    async def init(self) -> None:
        """Ініціалізація схеми БД — створення таблиць та міграції."""
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    model TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS auth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_logged_in INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth_id INTEGER,
                    receipt_number TEXT,
                    fiscal_number TEXT,
                    serial_number TEXT,
                    receipt_date TEXT,
                    receipt_time TEXT,
                    amount REAL,
                    qr_link TEXT,
                    category TEXT,
                    raw_claude_json TEXT,
                    photo_file_id TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (auth_id) REFERENCES auth(id)
                );
                CREATE TABLE IF NOT EXISTS receipt_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    code TEXT,
                    quantity REAL,
                    unit_price REAL,
                    total_price REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS receipt_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    photo_file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_receipts_date     ON receipts(receipt_date);
                CREATE INDEX IF NOT EXISTS idx_auth_email        ON auth(email);
                CREATE INDEX IF NOT EXISTS idx_items_receipt_id  ON receipt_items(receipt_id);
                CREATE INDEX IF NOT EXISTS idx_photos_receipt_id ON receipt_photos(receipt_id);
            """)
            await conn.commit()

            # Міграції
            for migration in [
                "ALTER TABLE receipts ADD COLUMN category TEXT",
                "ALTER TABLE receipts ADD COLUMN auth_id INTEGER",
                "ALTER TABLE receipts ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0",
            ]:
                try:
                    await conn.execute(migration)
                    await conn.commit()
                    logger.info("Міграція: %s", migration)
                except Exception:
                    pass

            try:
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_receipts_auth_id ON receipts(auth_id)"
                )
                await conn.commit()
            except Exception:
                pass

            logger.info("База даних ініціалізована")
        finally:
            await conn.close()


# ─────────────────────────────────────────────────────────────
# Єдиний екземпляр (Singleton)
# ─────────────────────────────────────────────────────────────

db = Database(DB_PATH)


# ─────────────────────────────────────────────────────────────
# Зворотна сумісність зі старим API
# ─────────────────────────────────────────────────────────────

async def init_db() -> None:
    await db.init()

async def register_user(telegram_id, username=None, first_name=None, last_name=None):
    await db.users.register(telegram_id, username, first_name, last_name)

async def find_existing_receipt(auth_id, receipt_number=None, fiscal_number=None,
                                receipt_date=None, receipt_time=None):
    return await db.receipts.find_existing(auth_id, receipt_number, fiscal_number,
                                           receipt_date, receipt_time)

async def save_receipt(auth_id, **kwargs):
    return await db.receipts.save(auth_id, **kwargs)

async def delete_receipt(auth_id, receipt_id):
    return await db.receipts.delete(auth_id, receipt_id)

async def get_total_stats(auth_id):
    return await db.receipts.get_stats(auth_id)

async def log_api_usage(telegram_id, input_tokens, output_tokens, model):
    await db.api.log(telegram_id, input_tokens, output_tokens, model)

async def auth_create(telegram_id, email, password_hash):
    return await db.auth.create(telegram_id, email, password_hash)

async def auth_get_by_telegram(telegram_id):
    return await db.auth.get_by_telegram(telegram_id)

async def auth_get_by_email(email):
    return await db.auth.get_by_email(email)

async def auth_set_logged_in(telegram_id, value):
    await db.auth.set_logged_in(telegram_id, value)

async def auth_is_logged_in(telegram_id):
    return await db.auth.is_logged_in(telegram_id)

async def auth_switch_telegram(email, new_telegram_id):
    await db.auth.switch_telegram(email, new_telegram_id)
