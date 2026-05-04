"""
Веб-застосунок — Flask.

WebDatabase  — синхронний доступ до SQLite для Flask (g-based connection)
create_app() — фабричний метод Flask Application Factory
"""
import re
import sqlite3
import sys
from functools import wraps
from pathlib import Path
from typing import Optional

import bcrypt
from flask import (
    Flask, render_template, request,
    redirect, url_for, session, g, jsonify,
    send_from_directory,
)

# Гарантуємо що корінь проєкту є в sys.path незалежно від робочої директорії
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import config

_DB_PATH    = str(Path(__file__).parent.parent / config.db_path)
_PHOTOS_DIR = str(Path(__file__).parent.parent / "photos")

_QUARTER_MONTHS = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
_UA_MONTHS_SHORT = ['','Січ','Лют','Бер','Кві','Тра','Чер','Лип','Сер','Вер','Жов','Лис','Гру']

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


def _period_where(period_type: str, period: str) -> tuple[str, list]:
    """Повертає (where_extra, params) для фільтрації по місяцю/кварталу/року."""
    if not period:
        return "", []
    if period_type == "month":
        parts = period.split("-")
        if len(parts) == 2:
            return "AND receipt_date LIKE ?", [f"%.{parts[1]}.{parts[0]}"]
    elif period_type == "quarter":
        parts = period.split("-Q")
        if len(parts) == 2:
            year, q = parts[0], int(parts[1])
            months = _QUARTER_MONTHS.get(q, [])
            placeholders = ",".join("?" * len(months))
            return (
                f"AND substr(receipt_date,7,4)=? "
                f"AND CAST(substr(receipt_date,4,2) AS INTEGER) IN ({placeholders})",
                [year] + months,
            )
    elif period_type == "year":
        return "AND substr(receipt_date,7,4)=?", [period]
    return "", []


def _period_label(period_type: str, period: str) -> str:
    """Людська назва для відображення в UI."""
    if not period:
        return ""
    if period_type == "month":
        parts = period.split("-")
        if len(parts) == 2:
            try:
                return f"{_UA_MONTHS_SHORT[int(parts[1])]} {parts[0]}"
            except (IndexError, ValueError):
                return period
    elif period_type == "quarter":
        return period.replace("-Q", " Q")
    return period


def _construct_qr_url(receipt_number, fiscal_number, date, time, amount) -> str:
    """Будує URL кабінету ДПС з параметрів чеку."""
    if not date or len(date) < 10:
        return ""
    date_raw = date[6:10] + date[3:5] + date[0:2]
    time_raw = (time or "000000").replace(":", "")[:6]
    sm = f"{float(amount):.2f}" if amount else "0"
    params = []
    if date_raw:       params.append(f"date={date_raw}")
    if time_raw:       params.append(f"time={time_raw}")
    if receipt_number: params.append(f"id={receipt_number}")
    if sm:             params.append(f"sm={sm}")
    if fiscal_number:  params.append(f"fn={fiscal_number}")
    return "https://cabinet.tax.gov.ua/cashregs/check?" + "&".join(params)

_UA_MONTHS_FULL = [
    '', 'Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень',
    'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень',
]


# ─────────────────────────────────────────────────────────────
# Синхронний репозиторій для Flask
# ─────────────────────────────────────────────────────────────

class WebDatabase:
    """
    Синхронний доступ до SQLite для Flask.
    Зберігає з'єднання у Flask g-об'єкті протягом одного запиту.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    def connection(self) -> sqlite3.Connection:
        """Повертає з'єднання для поточного Flask-запиту."""
        if "db" not in g:
            g.db = sqlite3.connect(self._path)
            g.db.row_factory = sqlite3.Row
        return g.db

    def close(self) -> None:
        """Закриває з'єднання після запиту."""
        conn = g.pop("db", None)
        if conn:
            conn.close()

    # ── Auth ──────────────────────────────────────────────────

    def auth_get_by_email(self, email: str) -> Optional[sqlite3.Row]:
        return self.connection().execute(
            "SELECT * FROM auth WHERE email = ?", (email,)
        ).fetchone()

    def auth_get_by_id(self, auth_id: int) -> Optional[sqlite3.Row]:
        return self.connection().execute(
            "SELECT * FROM auth WHERE id = ?", (auth_id,)
        ).fetchone()

    def auth_email_exists(self, email: str) -> bool:
        return bool(self.connection().execute(
            "SELECT id FROM auth WHERE email = ?", (email,)
        ).fetchone())

    def auth_create(self, email: str, password_hash: str) -> int:
        conn = self.connection()
        conn.execute(
            "INSERT INTO auth (email, password_hash, is_logged_in) VALUES (?, ?, 0)",
            (email, password_hash),
        )
        conn.commit()
        return conn.execute("SELECT id FROM auth WHERE email = ?", (email,)).fetchone()["id"]

    # ── Receipts ──────────────────────────────────────────────

    def receipts_query(self, auth_id: int, category: str = "", month: str = "") -> list:
        sql, params = "SELECT * FROM receipts WHERE auth_id = ?", [auth_id]
        if category:
            sql += " AND category = ?"; params.append(category)
        if month:
            parts = month.split("-")
            if len(parts) == 2:
                sql += " AND receipt_date LIKE ?"; params.append(f"%.{parts[1]}.{parts[0]}")
        # DD.MM.YYYY → YYYYMMDD для коректного хронологічного сортування
        sql += """ ORDER BY
            substr(receipt_date,7,4)||substr(receipt_date,4,2)||substr(receipt_date,1,2) DESC,
            receipt_time DESC"""
        return self.connection().execute(sql, params).fetchall()

    def receipts_categories(self, auth_id: int) -> list:
        return self.connection().execute(
            "SELECT DISTINCT category FROM receipts WHERE auth_id=? AND category IS NOT NULL ORDER BY category",
            (auth_id,),
        ).fetchall()

    def receipts_months(self, auth_id: int) -> list:
        return self.connection().execute(
            """SELECT DISTINCT substr(receipt_date,7,4)||'-'||substr(receipt_date,4,2) AS ym
               FROM receipts WHERE auth_id=? AND length(receipt_date)>=10 ORDER BY ym DESC""",
            (auth_id,),
        ).fetchall()

    def receipts_delete(self, auth_id: int, ids: list[int]) -> int:
        placeholders = ",".join("?" * len(ids))
        conn = self.connection()
        cursor = conn.execute(
            f"DELETE FROM receipts WHERE auth_id=? AND id IN ({placeholders})",
            [auth_id] + ids,
        )
        conn.commit()
        return cursor.rowcount

    def receipts_cat_stats(self, auth_id: int, where_extra: str = "", params_extra: list = None) -> list:
        params = [auth_id] + (params_extra or [])
        return self.connection().execute(
            f"""SELECT category, COUNT(*) as count, ROUND(SUM(amount),2) as total
                FROM receipts WHERE auth_id=? {where_extra} AND category IS NOT NULL AND category!=''
                GROUP BY category ORDER BY total DESC""",
            params,
        ).fetchall()

    def receipts_month_stats(self, auth_id: int) -> list:
        return self.connection().execute(
            """SELECT substr(receipt_date,7,4)||'-'||substr(receipt_date,4,2) AS month,
                      COUNT(*) AS count, ROUND(SUM(amount),2) AS total
               FROM receipts WHERE auth_id=? AND receipt_date IS NOT NULL AND length(receipt_date)>=10
               GROUP BY month ORDER BY month ASC LIMIT 18""",
            (auth_id,),
        ).fetchall()

    def receipts_summary(self, auth_id: int, where_extra: str = "", params_extra: list = None) -> sqlite3.Row:
        params = [auth_id] + (params_extra or [])
        return self.connection().execute(
            f"""SELECT COUNT(*) as count, ROUND(SUM(amount),2) as total_sum, ROUND(AVG(amount),2) as avg_amount
                FROM receipts WHERE auth_id=? {where_extra}""",
            params,
        ).fetchone()

    def receipts_recent(self, auth_id: int, limit: int = 5) -> list:
        return self.connection().execute(
            """SELECT receipt_date, receipt_time, amount, category, receipt_number
               FROM receipts WHERE auth_id=?
               ORDER BY
                 substr(receipt_date,7,4)||substr(receipt_date,4,2)||substr(receipt_date,1,2) DESC,
                 receipt_time DESC
               LIMIT ?""",
            (auth_id, limit),
        ).fetchall()

    def receipts_top_categories(self, auth_id: int, limit: int = 4) -> list:
        return self.connection().execute(
            """SELECT category, ROUND(SUM(amount),2) as total, COUNT(*) as cnt
               FROM receipts WHERE auth_id=? AND category IS NOT NULL
               GROUP BY category ORDER BY total DESC LIMIT ?""",
            (auth_id, limit),
        ).fetchall()

    def receipts_cat_by_month(self, auth_id: int, month_str: str) -> tuple:
        if not month_str:
            return {}, 0, 0
        parts = month_str.split("-")
        pat = f"%.{parts[1]}.{parts[0]}"
        rows = self.connection().execute(
            """SELECT category, ROUND(SUM(amount),2) as total, COUNT(*) as cnt
               FROM receipts WHERE auth_id=? AND receipt_date LIKE ? AND category IS NOT NULL
               GROUP BY category""",
            (auth_id, pat),
        ).fetchall()
        s = self.connection().execute(
            "SELECT COUNT(*) as cnt, ROUND(SUM(amount),2) as s FROM receipts WHERE auth_id=? AND receipt_date LIKE ?",
            (auth_id, pat),
        ).fetchone()
        cat_map = {r["category"]: {"total": r["total"] or 0, "count": r["cnt"]} for r in rows}
        return cat_map, s["cnt"] or 0, s["s"] or 0

    def receipt_by_id(self, auth_id: int, receipt_id: int):
        return self.connection().execute(
            "SELECT * FROM receipts WHERE id=? AND auth_id=?",
            (receipt_id, auth_id),
        ).fetchone()

    def receipt_update(self, auth_id: int, receipt_id: int, **fields) -> bool:
        allowed = {"receipt_number","fiscal_number","serial_number",
                   "receipt_date","receipt_time","amount","qr_link","category"}
        data = {k: v for k, v in fields.items() if k in allowed}
        if not data:
            return False
        set_clause = ", ".join(f"{k}=?" for k in data)
        conn = self.connection()
        cursor = conn.execute(
            f"UPDATE receipts SET {set_clause} WHERE id=? AND auth_id=?",
            list(data.values()) + [receipt_id, auth_id],
        )
        conn.commit()
        return cursor.rowcount > 0

    def receipt_insert(self, auth_id: int, **fields) -> int:
        allowed = {"receipt_number","fiscal_number","serial_number",
                   "receipt_date","receipt_time","amount","qr_link","category"}
        data = {k: v for k, v in fields.items() if k in allowed}
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        conn = self.connection()
        cursor = conn.execute(
            f"INSERT INTO receipts (auth_id, {cols}) VALUES (?, {placeholders})",
            [auth_id] + list(data.values()),
        )
        conn.commit()
        return cursor.lastrowid

    def receipts_available_years(self, auth_id: int) -> list:
        return self.connection().execute(
            """SELECT DISTINCT substr(receipt_date,7,4) as year
               FROM receipts WHERE auth_id=? AND length(receipt_date)>=10
               ORDER BY year DESC""",
            (auth_id,),
        ).fetchall()

    def items_by_receipt(self, receipt_id: int) -> list:
        return self.connection().execute(
            "SELECT * FROM receipt_items WHERE receipt_id=? ORDER BY id",
            (receipt_id,),
        ).fetchall()

    def photos_by_receipt(self, receipt_id: int) -> list:
        return self.connection().execute(
            "SELECT * FROM receipt_photos WHERE receipt_id=? ORDER BY id DESC",
            (receipt_id,),
        ).fetchall()

    def items_top(self, auth_id: int, limit: int = 10) -> list:
        return self.connection().execute(
            """SELECT ri.name,
                      ROUND(SUM(ri.total_price), 2) as total,
                      ROUND(SUM(ri.quantity), 2) as qty,
                      COUNT(*) as cnt
               FROM receipt_items ri
               JOIN receipts r ON r.id = ri.receipt_id
               WHERE r.auth_id = ? AND ri.name IS NOT NULL
               GROUP BY ri.name
               ORDER BY total DESC
               LIMIT ?""",
            (auth_id, limit),
        ).fetchall()

    def receipts_daily(self, auth_id: int, month_str: str) -> list:
        if not month_str:
            return []
        parts = month_str.split("-")
        pat = f"%.{parts[1]}.{parts[0]}"
        rows = self.connection().execute(
            """SELECT CAST(substr(receipt_date,1,2) AS INTEGER) as day,
                      ROUND(SUM(amount),2) as total, COUNT(*) as cnt
               FROM receipts WHERE auth_id=? AND receipt_date LIKE ?
               GROUP BY substr(receipt_date,1,2) ORDER BY day""",
            (auth_id, pat),
        ).fetchall()
        return [{"day": r["day"], "total": r["total"] or 0, "count": r["cnt"]} for r in rows]


# ─────────────────────────────────────────────────────────────
# Application Factory
# ─────────────────────────────────────────────────────────────

def create_app() -> Flask:
    """Фабричний метод — створює та конфігурує Flask-застосунок."""
    app = Flask(__name__)
    import os
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "receipts-web-secret-2025")

    web_db = WebDatabase(_DB_PATH)

    # ── Lifecycle ──────────────────────────────────────────────

    @app.teardown_appcontext
    def _close_db(e=None):
        web_db.close()

    # ── Auth decorator ─────────────────────────────────────────

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "auth_id" not in session:
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ── Auth helpers ───────────────────────────────────────────

    def _month_like(month_str: str) -> Optional[str]:
        parts = month_str.split("-")
        return f"%.{parts[1]}.{parts[0]}" if len(parts) == 2 else None

    def _month_label(m: str) -> str:
        if not m:
            return ""
        parts = m.split("-")
        return f"{_UA_MONTHS_FULL[int(parts[1])]} {parts[0]}"

    # ── Routes ─────────────────────────────────────────────────

    @app.route("/")
    @login_required
    def index():
        auth_id = session["auth_id"]
        conn = web_db.connection()
        home_summary = conn.execute(
            """SELECT COUNT(*) as count, ROUND(SUM(amount),2) as total_sum,
                      COUNT(DISTINCT substr(receipt_date,4,7)) as months_count
               FROM receipts WHERE auth_id=?""",
            (auth_id,),
        ).fetchone()
        return render_template(
            "home.html",
            summary=home_summary,
            recent=web_db.receipts_recent(auth_id),
            top_cats=web_db.receipts_top_categories(auth_id),
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if "auth_id" in session:
            return redirect(url_for("receipts"))
        error = None
        if request.method == "POST":
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = web_db.auth_get_by_email(email)
            if user and bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
                session["auth_id"] = user["id"]
                session["email"]   = user["email"]
                return redirect(url_for("receipts"))
            error = "Невірний email або пароль"
        return render_template("login.html", error=error)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if "auth_id" in session:
            return redirect(url_for("receipts"))
        error = None
        if request.method == "POST":
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm  = request.form.get("password_confirm", "")
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
                error = "Невірний формат email"
            elif len(password) < 8:
                error = "Пароль — мінімум 8 символів"
            elif password != confirm:
                error = "Паролі не співпадають"
            elif web_db.auth_email_exists(email):
                error = "Цей email вже зареєстрований"
            else:
                pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                auth_id = web_db.auth_create(email, pw_hash)
                session["auth_id"] = auth_id
                session["email"]   = email
                return redirect(url_for("receipts"))
        return render_template("register.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/receipts")
    @login_required
    def receipts():
        auth_id        = session["auth_id"]
        selected_cat   = request.args.get("category", "")
        selected_month = request.args.get("month", "")
        rows = web_db.receipts_query(auth_id, selected_cat, selected_month)
        return render_template(
            "receipts.html",
            receipts=rows,
            categories=web_db.receipts_categories(auth_id),
            months=web_db.receipts_months(auth_id),
            selected_cat=selected_cat,
            selected_month=selected_month,
            total_sum=sum(r["amount"] or 0 for r in rows),
        )

    @app.route("/receipts/delete", methods=["POST"])
    @login_required
    def receipts_delete():
        auth_id = session["auth_id"]
        data = request.get_json(silent=True) or {}
        ids  = data.get("ids", [])
        if not ids or not isinstance(ids, list):
            return jsonify({"ok": False, "error": "no ids"}), 400
        deleted = web_db.receipts_delete(auth_id, [int(i) for i in ids])
        return jsonify({"ok": True, "deleted": deleted})

    @app.route("/stats")
    @login_required
    def stats():
        auth_id     = session["auth_id"]
        period_type = request.args.get("period_type", "month")
        period      = request.args.get("period", "")
        extra, params = _period_where(period_type, period)
        return render_template(
            "stats.html",
            cat_stats=web_db.receipts_cat_stats(auth_id, extra, params),
            month_stats=web_db.receipts_month_stats(auth_id),
            summary=web_db.receipts_summary(auth_id, extra, params),
            months=web_db.receipts_months(auth_id),
            period_type=period_type,
            period=period,
            period_label=_period_label(period_type, period),
            top_products=web_db.items_top(auth_id),
        )

    @app.route("/photos/<path:filename>")
    def serve_photo(filename):
        return send_from_directory(_PHOTOS_DIR, filename)

    @app.route("/api/receipt/<int:receipt_id>")
    @login_required
    def receipt_detail(receipt_id):
        """JSON: поля чеку + товари + фото."""
        auth_id = session["auth_id"]
        row = web_db.receipt_by_id(auth_id, receipt_id)
        if not row:
            return jsonify({"error": "not found"}), 404
        items  = [dict(r) for r in web_db.items_by_receipt(receipt_id)]
        photos = [dict(r) for r in web_db.photos_by_receipt(receipt_id)]
        return jsonify({"receipt": dict(row), "items": items, "photos": photos})

    @app.route("/api/receipt/<int:receipt_id>/update", methods=["POST"])
    @login_required
    def receipt_update(receipt_id):
        """Оновлення полів чеку."""
        auth_id = session["auth_id"]
        data = request.get_json(silent=True) or {}
        ok = web_db.receipt_update(auth_id, receipt_id, **data)
        return jsonify({"ok": ok})

    @app.route("/receipts/add", methods=["GET", "POST"])
    @login_required
    def receipts_add():
        """Ручне введення чеку."""
        auth_id = session["auth_id"]
        error = None
        if request.method == "POST":
            receipt_number = request.form.get("receipt_number", "").strip() or None
            fiscal_number  = request.form.get("fiscal_number",  "").strip() or None
            receipt_date   = request.form.get("receipt_date",   "").strip() or None
            receipt_time   = request.form.get("receipt_time",   "").strip() or None
            amount_str     = request.form.get("amount",         "").strip()
            category       = request.form.get("category",       "other")

            if not receipt_date:
                error = "Дата обов'язкова"
            elif not amount_str:
                error = "Сума обов'язкова"
            else:
                try:
                    amount = float(amount_str.replace(",", "."))
                except ValueError:
                    error = "Невірний формат суми"

            if not error:
                qr_link = _construct_qr_url(
                    receipt_number, fiscal_number,
                    receipt_date, receipt_time, amount
                )
                web_db.receipt_insert(
                    auth_id,
                    receipt_number=receipt_number,
                    fiscal_number=fiscal_number,
                    receipt_date=receipt_date,
                    receipt_time=receipt_time or "00:00:00",
                    amount=amount,
                    category=category,
                    qr_link=qr_link or None,
                )
                return redirect(url_for("receipts"))

        return render_template("receipt_form.html",
                               title="Додати чек",
                               receipt=None,
                               categories=CATEGORIES,
                               error=error)

    @app.route("/compare")
    @login_required
    def compare():
        auth_id = session["auth_id"]
        type_a  = request.args.get("type_a", "month")
        period_a = request.args.get("period_a", "")
        type_b  = request.args.get("type_b", "month")
        period_b = request.args.get("period_b", "")

        def _cat_stats_for_period(period_type, period):
            extra, params = _period_where(period_type, period)
            if not extra:
                return {}, 0, 0
            rows = web_db.receipts_cat_stats(auth_id, extra, params)
            sm = web_db.receipts_summary(auth_id, extra, params)
            cat_map = {r["category"]: {"total": r["total"] or 0, "count": r["count"]} for r in rows}
            return cat_map, sm["count"] or 0, sm["total_sum"] or 0

        def _daily_for_period(period_type, period):
            if period_type == "month":
                return web_db.receipts_daily(auth_id, period)
            extra, params = _period_where(period_type, period)
            if not extra:
                return []
            rows = web_db.connection().execute(
                f"""SELECT CAST(substr(receipt_date,1,2) AS INTEGER) as day,
                           ROUND(SUM(amount),2) as total, COUNT(*) as cnt
                    FROM receipts WHERE auth_id=? {extra}
                    GROUP BY substr(receipt_date,1,2) ORDER BY day""",
                [auth_id] + params,
            ).fetchall()
            return [{"day": r["day"], "total": r["total"] or 0, "count": r["cnt"]} for r in rows]

        map_a, count_a, sum_a = _cat_stats_for_period(type_a, period_a)
        map_b, count_b, sum_b = _cat_stats_for_period(type_b, period_b)
        all_cats = sorted(set(list(map_a) + list(map_b)))
        comparison = [
            {
                "category": cat,
                "total_a": map_a.get(cat, {}).get("total"),
                "count_a": map_a.get(cat, {}).get("count"),
                "total_b": map_b.get(cat, {}).get("total"),
                "count_b": map_b.get(cat, {}).get("count"),
            }
            for cat in all_cats
        ]
        return render_template(
            "compare.html",
            months=web_db.receipts_months(auth_id),
            type_a=type_a, period_a=period_a,
            type_b=type_b, period_b=period_b,
            label_a=_period_label(type_a, period_a),
            label_b=_period_label(type_b, period_b),
            comparison=comparison,
            sum_a=sum_a, sum_b=sum_b,
            count_a=count_a, count_b=count_b,
            daily_a=_daily_for_period(type_a, period_a),
            daily_b=_daily_for_period(type_b, period_b),
        )

    return app


# ─────────────────────────────────────────────────────────────
# Точка запуску
# ─────────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    app.run(host=config.web_host, port=config.web_port, debug=config.debug)
