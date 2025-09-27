from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
import sqlite3
from datetime import datetime
import csv
import io
import os

DB_FILE = "budget.db"

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if os.path.exists(DB_FILE):
        return
    conn = get_db()
    cur = conn.cursor()
    # Categories
    cur.execute("""
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    """)
    # Transactions
    cur.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            description TEXT,
            category_id INTEGER,
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );
    """)
    # Seed categories
    categories = ['Groceries', 'Rent', 'Utilities', 'Eating Out', 'Salary', 'Entertainment', 'Transport', 'Other']
    for c in categories:
        cur.execute("INSERT INTO categories (name) VALUES (?)", (c,))
    # Seed sample transactions (this keeps DB small)
    sample = [
        ("2025-09-01","September Salary", 5, 4000.00, "income"),
        ("2025-09-02","Market groceries", 1, 120.35, "expense"),
        ("2025-09-03","Monthly rent", 2, 1500.00, "expense"),
        ("2025-09-05","Bus pass", 7, 60.00, "expense"),
        ("2025-09-10","Dinner out", 4, 45.40, "expense"),
        ("2025-09-12","Streaming service", 6, 12.99, "expense"),
    ]
    for t in sample:
        cur.execute("INSERT INTO transactions (date, description, category_id, amount, type) VALUES (?,?,?,?,?)", t)
    conn.commit()
    conn.close()

def query(sql, args=(), one=False):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, args)
    rv = cur.fetchall()
    conn.close()
    return (rv[0] if rv else None) if one else rv

@app.route("/")
def index():
    # default month = current year-month
    month = request.args.get("month")
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # fetch transactions for month
    start = month + "-01"
    # naive end: next month: we can use LIKE on date prefix
    rows = query("""
        SELECT t.*, c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE substr(t.date, 1, 7) = ?
        ORDER BY date DESC, id DESC
    """, (month,))
    cats = query("SELECT * FROM categories ORDER BY name")
    # compute totals
    income = 0.0
    expenses = 0.0
    for r in rows:
        if r["type"] == "income":
            income += float(r["amount"])
        else:
            expenses += float(r["amount"])
    net = income - expenses
    return render_template("index.html", transactions=rows, categories=cats, month=month, income=income, expenses=expenses, net=net)

@app.route("/add_transaction", methods=["POST"])
def add_transaction():
    date = request.form.get("date")
    description = request.form.get("description","")
    category_id = request.form.get("category_id")
    amount = request.form.get("amount")
    ttype = request.form.get("type","expense")
    # basic validation
    try:
        datetime.strptime(date, "%Y-%m-%d")
        amount_val = float(amount)
    except Exception:
        return "Invalid input", 400
    if category_id == "" or category_id is None:
        category_id = None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO transactions (date, description, category_id, amount, type) VALUES (?,?,?,?,?)",
                (date, description, category_id, amount_val, ttype))
    conn.commit()
    conn.close()
    return redirect(url_for("index", month=date[:7]))

@app.route("/delete_transaction/<int:tx_id>", methods=["POST"])
def delete_transaction(tx_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))

@app.route("/add_category", methods=["POST"])
def add_category():
    name = request.form.get("name","").strip()
    if not name:
        return redirect(request.referrer or url_for("index"))
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        pass
    return redirect(request.referrer or url_for("index"))

@app.route("/delete_category/<int:cat_id>", methods=["POST"])
def delete_category(cat_id):
    conn = get_db()
    cur = conn.cursor()
    # set transactions referencing this to NULL to avoid FK issues
    cur.execute("UPDATE transactions SET category_id = NULL WHERE category_id = ?", (cat_id,))
    cur.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))

@app.route("/api/summary")
def api_summary():
    month = request.args.get("month")
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # totals and breakdown by category
    totals = query("""
        SELECT type, SUM(amount) as total
        FROM transactions
        WHERE substr(date,1,7)=?
        GROUP BY type
    """, (month,))
    breakdown = query("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE substr(t.date,1,7)=? AND t.type='expense'
        GROUP BY c.name
        ORDER BY total DESC
    """, (month,))
    res = {
        "month": month,
        "totals": {r["type"]: r["total"] for r in totals},
        "breakdown": [{"category": r["category"] or "Uncategorized", "total": r["total"]} for r in breakdown]
    }
    return jsonify(res)

@app.route("/export_csv")
def export_csv():
    month = request.args.get("month")
    if not month:
        month = datetime.now().strftime("%Y-%m")
    rows = query("""
        SELECT t.*, c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE substr(t.date,1,7)=?
        ORDER BY date
    """, (month,))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date","description","category","type","amount"])
    for r in rows:
        writer.writerow([r["date"], r["description"], r["category_name"] or "Uncategorized", r["type"], r["amount"]])
    mem = io.BytesIO()
    mem.write(out.getvalue().encode("utf-8"))
    mem.seek(0)
    filename = f"budget_{month}.csv"
    return send_file(mem, as_attachment=True, download_name=filename, mimetype="text/csv")

if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', debug=True)
