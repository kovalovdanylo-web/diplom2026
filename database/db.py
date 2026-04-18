import aiosqlite
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)


async def _get_db() -> aiosqlite.Connection:
    """Отримати з'єднання з БД."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db() -> None:
    """Ініціалізація БД — створення таблиць та індексів."""
    db = await _get_db()
    try:
        await db.executescript("""
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

            CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(receipt_date);
            CREATE INDEX IF NOT EXISTS idx_auth_email ON auth(email);
        """)
        await db.commit()

        # Міграції для існуючих БД — виконуються після CREATE TABLE
        for migration in [
            "ALTER TABLE receipts ADD COLUMN category TEXT",
            "ALTER TABLE receipts ADD COLUMN auth_id INTEGER",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
                logger.info("Міграція виконана: %s", migration)
            except Exception:
                pass  # Колонка вже існує

        # Індекс на auth_id — після міграції, щоб колонка точно існувала
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_auth_id ON receipts(auth_id)"
            )
            await db.commit()
        except Exception:
            pass

        logger.info("База даних ініціалізована успішно")
    finally:
        await db.close()


async def register_user(
    telegram_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
) -> None:
    """Реєстрація або оновлення користувача."""
    db = await _get_db()
    try:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
            """,
            (telegram_id, username, first_name, last_name),
        )
        await db.commit()
    finally:
        await db.close()


async def find_existing_receipt(
    auth_id: int,
    receipt_number: str = None,
    fiscal_number: str = None,
    receipt_date: str = None,
    receipt_time: str = None,
) -> int | None:
    """
    Шукає дублікат чеку в БД за auth_id (прив'язка до акаунту, не до TG).
    Збіг за: fiscal_number+date+time АБО receipt_number+date.
    Повертає ID існуючого чеку або None.
    """
    db = await _get_db()
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


async def save_receipt(
    auth_id: int,
    receipt_number: str = None,
    fiscal_number: str = None,
    serial_number: str = None,
    receipt_date: str = None,
    receipt_time: str = None,
    amount: float = None,
    qr_link: str = None,
    category: str = None,
    raw_claude_json: str = None,
    photo_file_id: str = None,
    tokens_used: int = 0,
) -> tuple[int, bool]:
    """
    Збереження або оновлення чеку.
    Чек прив'язується до auth_id (акаунт), не до telegram_id.
    Повертає (ID запису, is_new).
    """
    existing_id = await find_existing_receipt(
        auth_id, receipt_number, fiscal_number, receipt_date, receipt_time,
    )

    db = await _get_db()
    try:
        if existing_id:
            await db.execute(
                """
                UPDATE receipts SET
                    receipt_number = COALESCE(?, receipt_number),
                    fiscal_number = COALESCE(?, fiscal_number),
                    serial_number = COALESCE(?, serial_number),
                    receipt_date = COALESCE(?, receipt_date),
                    receipt_time = COALESCE(?, receipt_time),
                    amount = COALESCE(?, amount),
                    qr_link = COALESCE(?, qr_link),
                    category = COALESCE(?, category),
                    raw_claude_json = ?,
                    photo_file_id = ?,
                    tokens_used = tokens_used + ?
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


async def log_api_usage(
    telegram_id: int,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> None:
    """Логування витрат API для статистики."""
    db = await _get_db()
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


async def get_total_stats(auth_id: int) -> dict:
    """Загальна статистика акаунту."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(amount), 0) as total,
                MIN(receipt_date) as first_date,
                MAX(receipt_date) as last_date
            FROM receipts WHERE auth_id = ?
            """,
            (auth_id,),
        )
        row = await cursor.fetchone()

        # api_usage лишається прив'язаним до telegram_id (технічна метрика)
        cursor2 = await db.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) as input_tokens,
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


async def get_month_stats(auth_id: int, year: int, month: int) -> dict:
    """Статистика за місяць для акаунту."""
    month_prefix = f".{month:02d}.{year}"
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as count,
                COALESCE(SUM(amount), 0) as total
            FROM receipts
            WHERE auth_id = ? AND receipt_date LIKE ?
            """,
            (auth_id, f"%{month_prefix}"),
        )
        row = await cursor.fetchone()
        return {
            "count": row["count"],
            "total": row["total"],
            "year": year,
            "month": month,
        }
    finally:
        await db.close()


async def get_recent_receipts(auth_id: int, limit: int = 5) -> list[dict]:
    """Останні N чеків акаунту."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT id, receipt_number, fiscal_number, serial_number,
                   receipt_date, receipt_time, amount, qr_link
            FROM receipts
            WHERE auth_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (auth_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "receipt_number": r["receipt_number"],
                "fiscal_number": r["fiscal_number"],
                "serial_number": r["serial_number"],
                "date": r["receipt_date"],
                "time": r["receipt_time"],
                "amount": r["amount"],
                "qr_link": r["qr_link"],
            }
            for r in rows
        ]
    finally:
        await db.close()


async def get_all_receipts_for_export(auth_id: int) -> list[tuple]:
    """Всі чеки акаунту для CSV експорту."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT id, receipt_number, fiscal_number, serial_number,
                   receipt_date, receipt_time, amount, qr_link, created_at
            FROM receipts
            WHERE auth_id = ?
            ORDER BY id ASC
            """,
            (auth_id,),
        )
        rows = await cursor.fetchall()
        return [tuple(r) for r in rows]
    finally:
        await db.close()


# ============================================================
# Auth функції
# ============================================================

async def auth_create(telegram_id: int, email: str, password_hash: str) -> bool:
    """
    Створює запис авторизації для користувача.
    Повертає False якщо email вже зайнятий.
    """
    db = await _get_db()
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


async def auth_get_by_telegram(telegram_id: int) -> dict | None:
    """Повертає запис auth по telegram_id або None."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM auth WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def auth_get_by_email(email: str) -> dict | None:
    """Повертає запис auth по email або None."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM auth WHERE email = ?",
            (email.lower().strip(),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def auth_set_logged_in(telegram_id: int, value: bool) -> None:
    """Встановлює статус сесії користувача."""
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE auth SET is_logged_in = ? WHERE telegram_id = ?",
            (1 if value else 0, telegram_id),
        )
        await db.commit()
    finally:
        await db.close()


async def auth_is_logged_in(telegram_id: int) -> bool:
    """Перевіряє чи користувач залогінений."""
    record = await auth_get_by_telegram(telegram_id)
    return bool(record and record["is_logged_in"])


async def auth_switch_telegram(email: str, new_telegram_id: int) -> None:
    """
    Переприв'язує акаунт до нового Telegram ID.
    Чеки НЕ переносяться — вони прив'язані до auth.id, а не до telegram_id.
    """
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE auth SET telegram_id = ?, is_logged_in = 1 WHERE email = ?",
            (new_telegram_id, email.lower().strip()),
        )
        await db.commit()
        logger.info("Акаунт %s переприв'язано до tg_id=%d", email, new_telegram_id)
    finally:
        await db.close()
