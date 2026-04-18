import os
import re
import sqlite3
from functools import wraps

import bcrypt
from flask import (
    Flask, render_template, request,
    redirect, url_for, session, g,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "receipts-web-secret-2025")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "receipts.db")


# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "auth_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return redirect(url_for("receipts"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "auth_id" in session:
        return redirect(url_for("receipts"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM auth WHERE email = ?", (email,)).fetchone()
        if user and bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            session["auth_id"] = user["id"]
            session["email"] = user["email"]
            return redirect(url_for("receipts"))
        error = "Невірний email або пароль"

    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "auth_id" in session:
        return redirect(url_for("receipts"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            error = "Невірний формат email"
        elif len(password) < 8:
            error = "Пароль — мінімум 8 символів"
        elif password != confirm:
            error = "Паролі не співпадають"
        else:
            db = get_db()
            if db.execute("SELECT id FROM auth WHERE email = ?", (email,)).fetchone():
                error = "Цей email вже зареєстрований"
            else:
                pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                db.execute(
                    "INSERT INTO auth (email, password_hash, is_logged_in) VALUES (?, ?, 0)",
                    (email, pw_hash),
                )
                db.commit()
                user = db.execute("SELECT id FROM auth WHERE email = ?", (email,)).fetchone()
                session["auth_id"] = user["id"]
                session["email"] = email
                return redirect(url_for("receipts"))

    return render_template("register.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/receipts")
@login_required
def receipts():
    auth_id = session["auth_id"]
    db = get_db()

    selected_cat   = request.args.get("category", "")
    selected_month = request.args.get("month", "")   # формат YYYY-MM

    query = "SELECT * FROM receipts WHERE auth_id = ?"
    params = [auth_id]

    if selected_cat:
        query += " AND category = ?"
        params.append(selected_cat)

    # receipt_date зберігається як DD.MM.YYYY — шукаємо по MM.YYYY суфіксу
    if selected_month:
        # "2025-03" → like "%.03.2025"
        parts = selected_month.split("-")
        if len(parts) == 2:
            like_pat = f"%.{parts[1]}.{parts[0]}"
            query += " AND receipt_date LIKE ?"
            params.append(like_pat)

    query += " ORDER BY receipt_date DESC, receipt_time DESC"
    rows = db.execute(query, params).fetchall()

    categories = db.execute(
        "SELECT DISTINCT category FROM receipts WHERE auth_id = ? AND category IS NOT NULL ORDER BY category",
        (auth_id,),
    ).fetchall()

    # Список доступних місяців для дропдауну
    months_raw = db.execute(
        """
        SELECT DISTINCT
            substr(receipt_date,7,4) || '-' || substr(receipt_date,4,2) AS ym
        FROM receipts
        WHERE auth_id = ? AND length(receipt_date) >= 10
        ORDER BY ym DESC
        """,
        (auth_id,),
    ).fetchall()

    total_sum = sum(r["amount"] or 0 for r in rows)

    return render_template(
        "receipts.html",
        receipts=rows,
        categories=categories,
        months=months_raw,
        selected_cat=selected_cat,
        selected_month=selected_month,
        total_sum=total_sum,
    )


@app.route("/stats")
@login_required
def stats():
    auth_id = session["auth_id"]
    db = get_db()

    selected_month = request.args.get("month", "")

    # Базова умова — з фільтром або без
    where_base = "auth_id = ?"
    base_params = [auth_id]
    if selected_month:
        parts = selected_month.split("-")
        if len(parts) == 2:
            like_pat = f"%.{parts[1]}.{parts[0]}"
            where_base += " AND receipt_date LIKE ?"
            base_params.append(like_pat)

    # Витрати по категоріях
    cat_stats = db.execute(
        f"""
        SELECT category, COUNT(*) as count, ROUND(SUM(amount), 2) as total
        FROM receipts
        WHERE {where_base} AND category IS NOT NULL AND category != ''
        GROUP BY category
        ORDER BY total DESC
        """,
        base_params,
    ).fetchall()

    # Витрати по місяцях (тільки для загального вигляду, без фільтру місяця)
    month_stats = db.execute(
        """
        SELECT
            substr(receipt_date, 7, 4) || '-' || substr(receipt_date, 4, 2) AS month,
            COUNT(*) AS count,
            ROUND(SUM(amount), 2) AS total
        FROM receipts
        WHERE auth_id = ? AND receipt_date IS NOT NULL AND length(receipt_date) >= 10
        GROUP BY month
        ORDER BY month ASC
        LIMIT 18
        """,
        (auth_id,),
    ).fetchall()

    # Загальна статистика (з фільтром)
    summary = db.execute(
        f"""
        SELECT
            COUNT(*) as count,
            ROUND(SUM(amount), 2) as total_sum,
            ROUND(AVG(amount), 2) as avg_amount
        FROM receipts WHERE {where_base}
        """,
        base_params,
    ).fetchone()

    # Список місяців для дропдауну
    months_raw = db.execute(
        """
        SELECT DISTINCT
            substr(receipt_date,7,4) || '-' || substr(receipt_date,4,2) AS ym
        FROM receipts
        WHERE auth_id = ? AND length(receipt_date) >= 10
        ORDER BY ym DESC
        """,
        (auth_id,),
    ).fetchall()

    return render_template(
        "stats.html",
        cat_stats=cat_stats,
        month_stats=month_stats,
        summary=summary,
        months=months_raw,
        selected_month=selected_month,
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
