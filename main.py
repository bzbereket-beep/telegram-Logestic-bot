import os
import sqlite3
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "logistics_bot.db")
TZ = ZoneInfo("Africa/Addis_Ababa")
FUEL_KM_PER_LITER = 8.0
FUEL_PRICE_PER_LITER = 185.0
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("logsti")

MENU = ReplyKeyboardMarkup(
    [["New trip", "Trip status"], ["Record payment", "Add expense"],
     ["Today report", "Month report"], ["Fuel cost", "Close trip"], ["Help"]],
    resize_keyboard=True
)

def now():
    return datetime.now(TZ).isoformat(timespec="seconds")

def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            trip_no INTEGER NOT NULL,
            start_km REAL NOT NULL,
            passengers INTEGER NOT NULL,
            standard_fare REAL NOT NULL,
            created_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS fares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            passenger TEXT NOT NULL,
            expected REAL NOT NULL,
            UNIQUE(trip_id, passenger),
            FOREIGN KEY(trip_id) REFERENCES trips(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            passenger TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL DEFAULT 'Cash',
            verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(trip_id) REFERENCES trips(id)
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(trip_id) REFERENCES trips(id)
        );
        CREATE TABLE IF NOT EXISTS fuel_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            log_date TEXT NOT NULL,
            km REAL NOT NULL,
            liters REAL NOT NULL,
            cost REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        db.commit()

def money(v):
    return f"{v:,.2f}".rstrip("0").rstrip(".")

def active_trip(db, chat_id):
    return db.execute(
        "SELECT * FROM trips WHERE chat_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (chat_id,)
    ).fetchone()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Logsti! I can track your trips, passenger fares, payments, fuel and other expenses, and reports. Choose an action below or type /help.",
        reply_markup=MENU
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "LOGSTI QUICK GUIDE\n\n"
        "/newtrip KM PASSENGERS STANDARD_FARE\n"
        "Example: /newtrip 12842 16 100\n\n"
        "/paid PASSENGER AMOUNT METHOD\n"
        "Example: /paid 7 600 Telebirr\n\n"
        "/fare PASSENGER NEW_FARE\n"
        "Example: /fare 7 150\n\n"
        "/expense AMOUNT DESCRIPTION\n"
        "Example: /expense 1500 Fuel\n\n"
        "/fuelkm KM - save daily distance and calculate fuel cost\n"
        "Example: /fuelkm 150\n\n"
        "/status - current trip totals and unpaid passengers\n"
        "/close - close the current trip\n"
        "/today - today's totals\n"
        "/month - this month's totals\n\n"
        "Use a passenger number or name consistently. Payments are recorded as unverified until you check the actual payment in your banking app."
    )
    await update.message.reply_text(msg, reply_markup=MENU)

async def newtrip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        await update.message.reply_text("Use: /newtrip KM PASSENGERS STANDARD_FARE\nExample: /newtrip 12842 16 100")
        return
    try:
        km, count, fare = float(context.args[0]), int(context.args[1]), float(context.args[2])
        if km < 0 or count < 1 or fare < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Please enter valid numbers. Example: /newtrip 12842 16 100")
        return
    chat_id = update.effective_chat.id
    with connect() as db:
        if active_trip(db, chat_id):
            await update.message.reply_text("You already have an open trip. Use /close before starting another.")
            return
        trip_no = db.execute("SELECT COALESCE(MAX(trip_no),0)+1 FROM trips WHERE chat_id=?", (chat_id,)).fetchone()[0]
        cur = db.execute(
            "INSERT INTO trips(chat_id,trip_no,start_km,passengers,standard_fare,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, trip_no, km, count, fare, now())
        )
        trip_id = cur.lastrowid
        for i in range(1, count + 1):
            db.execute("INSERT INTO fares(trip_id,passenger,expected) VALUES(?,?,?)", (trip_id, str(i), fare))
        db.commit()
    await update.message.reply_text(
        f"Trip #{trip_no} started.\\nStarting km: {money(km)}\\nPassengers: {count}\\nStandard fare: {money(fare)} birr\\nExpected income: {money(count*fare)} birr\\n\\nChange an individual fare with /fare passenger amount."
    )

async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Use: /paid PASSENGER AMOUNT [METHOD]\\nExample: /paid 7 600 Telebirr")
        return
    passenger = context.args[0]
    method = " ".join(context.args[2:]) if len(context.args) > 2 else "Cash"
    try:
        amount = float(context.args[1])
        if amount <= 0: raise ValueError()
    except ValueError:
        await update.message.reply_text("Payment amount must be a positive number.")
        return
    with connect() as db:
        trip = active_trip(db, update.effective_chat.id)
        if not trip:
            await update.message.reply_text("There is no open trip. Start one with /newtrip.")
            return
        db.execute(
            "INSERT INTO payments(trip_id,passenger,amount,method,verified,created_at) VALUES(?,?,?,?,0,?)",
            (trip["id"], passenger, amount, method, now())
        )
        db.commit()
        total = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE trip_id=?", (trip["id"],)).fetchone()[0]
    await update.message.reply_text(
        f"Recorded: passenger {passenger} paid {money(amount)} birr by {method}.\\nPayment status: NOT VERIFIED\\nTotal recorded payments this trip: {money(total)} birr.\\nCheck your payment app before treating it as verified."
    )

async def fare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Use: /fare PASSENGER NEW_FARE\\nExample: /fare 7 150")
        return
    passenger = context.args[0]
    try:
        amount = float(context.args[1])
        if amount < 0: raise ValueError()
    except ValueError:
        await update.message.reply_text("Fare must be zero or more.")
        return
    with connect() as db:
        trip = active_trip(db, update.effective_chat.id)
        if not trip:
            await update.message.reply_text("There is no open trip.")
            return
        cur = db.execute("UPDATE fares SET expected=? WHERE trip_id=? AND passenger=?", (amount, trip["id"], passenger))
        if cur.rowcount == 0:
            db.execute("INSERT INTO fares(trip_id,passenger,expected) VALUES(?,?,?)", (trip["id"], passenger, amount))
        db.commit()
    await update.message.reply_text(f"Passenger {passenger}'s expected fare is now {money(amount)} birr.")

async def expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Use: /expense AMOUNT DESCRIPTION\\nExample: /expense 1500 Fuel")
        return
    try:
        amount = float(context.args[0])
        if amount <= 0: raise ValueError()
    except ValueError:
        await update.message.reply_text("Expense amount must be a positive number.")
        return
    note = " ".join(context.args[1:])
    with connect() as db:
        trip = active_trip(db, update.effective_chat.id)
        if not trip:
            await update.message.reply_text("There is no open trip.")
            return
        db.execute("INSERT INTO expenses(trip_id,amount,note,created_at) VALUES(?,?,?,?)", (trip["id"], amount, note, now()))
        db.commit()
    await update.message.reply_text(f"Expense recorded: {money(amount)} birr for {note}.")

def trip_totals(db, trip_id):
    expected = db.execute("SELECT COALESCE(SUM(expected),0) FROM fares WHERE trip_id=?", (trip_id,)).fetchone()[0]
    paid_total = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE trip_id=?", (trip_id,)).fetchone()[0]
    costs = db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE trip_id=?", (trip_id,)).fetchone()[0]
    return expected, paid_total, costs

async def fuelkm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Use: /fuelkm KM\\nExample: /fuelkm 150")
        return
    try:
        km = float(context.args[0])
        if km <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Please enter a positive distance in kilometers.")
        return
    liters = km / FUEL_KM_PER_LITER
    cost = liters * FUEL_PRICE_PER_LITER
    chat_id = update.effective_chat.id
    log_date = datetime.now(TZ).date().isoformat()
    with connect() as db:
        db.execute(
            "INSERT INTO fuel_logs(chat_id,log_date,km,liters,cost,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, log_date, km, liters, cost, now())
        )
        db.commit()
        totals = db.execute(
            "SELECT COALESCE(SUM(km),0),COALESCE(SUM(liters),0),COALESCE(SUM(cost),0) FROM fuel_logs WHERE chat_id=? AND log_date=?",
            (chat_id, log_date)
        ).fetchone()
    await update.message.reply_text(
        f"FUEL ENTRY SAVED\\nDistance entered: {km:g} km\\n"
        f"Fuel used: {liters:.2f} liters\\nEstimated cost: {money(cost)} birr\\n\\n"
        f"TODAY'S TOTAL\\nDistance: {totals[0]:g} km\\n"
        f"Fuel used: {totals[1]:.2f} liters\\nFuel cost: {money(totals[2])} birr\\n\\n"
        f"Based on {FUEL_KM_PER_LITER:g} km/liter and {money(FUEL_PRICE_PER_LITER)} birr/liter."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as db:
        trip = active_trip(db, update.effective_chat.id)
        if not trip:
            await update.message.reply_text("No open trip right now.")
            return
        expected, paid_total, costs = trip_totals(db, trip["id"])
        rows = db.execute("""
            SELECT f.passenger, f.expected,
                   COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.trip_id=f.trip_id AND p.passenger=f.passenger),0) AS paid
            FROM fares f WHERE f.trip_id=? ORDER BY f.id
        """, (trip["id"],)).fetchall()
    unpaid = [f"{r['passenger']}: {money(max(0, r['expected']-r['paid']))} birr" for r in rows if r["expected"] > r["paid"]]
    msg = (
        f"TRIP #{trip['trip_no']} STATUS\\nExpected fares: {money(expected)} birr\\nPayments recorded: {money(paid_total)} birr\\nOutstanding: {money(max(0, expected-paid_total))} birr\\nExpenses: {money(costs)} birr\\nCash position after expenses: {money(paid_total-costs)} birr"
    )
    if unpaid:
        msg += "\\n\\nUNPAID / PARTLY PAID:\\n" + "\\n".join(unpaid[:30])
    await update.message.reply_text(msg)

async def close_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as db:
        trip = active_trip(db, update.effective_chat.id)
        if not trip:
            await update.message.reply_text("There is no open trip to close.")
            return
        expected, paid_total, costs = trip_totals(db, trip["id"])
        db.execute("UPDATE trips SET ended_at=? WHERE id=?", (now(), trip["id"]))
        db.commit()
    await update.message.reply_text(
        f"Trip #{trip['trip_no']} closed.\\nExpected income: {money(expected)} birr\\nPayments recorded: {money(paid_total)} birr\\nOutstanding: {money(max(0,expected-paid_total))} birr\\nExpenses: {money(costs)} birr\\nNet recorded cash: {money(paid_total-costs)} birr"
    )

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str):
    chat_id = update.effective_chat.id
    today = datetime.now(TZ).date().isoformat()
    month = today[:7]
    with connect() as db:
        if period == "today":
            clause, value = "date(created_at)=?", today
            fuel_clause, fuel_value = "log_date=?", today
            title = f"TODAY'S REPORT ({today})"
        else:
            clause, value = "substr(created_at,1,7)=?", month
            fuel_clause, fuel_value = "substr(log_date,1,7)=?", month
            title = f"MONTH REPORT ({month})"
        trips = db.execute(f"SELECT id,trip_no FROM trips WHERE chat_id=? AND {clause}", (chat_id,value)).fetchall()
        ids = [t["id"] for t in trips]
        expected = income = costs = 0
        if ids:
            marks = ",".join("?" for _ in ids)
            expected = db.execute(f"SELECT COALESCE(SUM(expected),0) FROM fares WHERE trip_id IN ({marks})", ids).fetchone()[0]
            income = db.execute(f"SELECT COALESCE(SUM(amount),0) FROM payments WHERE trip_id IN ({marks})", ids).fetchone()[0]
            costs = db.execute(f"SELECT COALESCE(SUM(amount),0) FROM expenses WHERE trip_id IN ({marks})", ids).fetchone()[0]
        fuel = db.execute(
            f"SELECT COALESCE(SUM(km),0),COALESCE(SUM(liters),0),COALESCE(SUM(cost),0) FROM fuel_logs WHERE chat_id=? AND {fuel_clause}",
            (chat_id, fuel_value)
        ).fetchone()
    await update.message.reply_text(
        f"{title}\\nTrips: {len(trips)}\\nExpected fares: {money(expected)} birr\\n"
        f"Payments recorded: {money(income)} birr\\nOutstanding: {money(max(0,expected-income))} birr\\n"
        f"Other expenses: {money(costs)} birr\\nFuel distance: {fuel[0]:g} km\\n"
        f"Estimated fuel used: {fuel[1]:.2f} liters\\nFuel cost: {money(fuel[2])} birr\\n"
        f"Net recorded cash after expenses and fuel: {money(income-costs-fuel[2])} birr"
    )


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report(update, context, "today")

async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report(update, context, "month")

async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "new trip":
        await update.message.reply_text("Start a trip like this:\\n/newtrip 12842 16 100")
    elif text == "trip status":
        await status(update, context)
    elif text == "record payment":
        await update.message.reply_text("Record a payment like this:\\n/paid 7 600 Telebirr")
    elif text == "add expense":
        await update.message.reply_text("Add an expense like this:\\n/expense 1500 Fuel")
    elif text == "today report":
        await today_cmd(update, context)
    elif text == "month report":
        await month_cmd(update, context)
    elif text == "close trip":
        await close_trip(update, context)
    elif text == "help":
        await help_cmd(update, context)
    else:
        await update.message.reply_text("I did not understand. Tap Help to see examples.", reply_markup=MENU)

def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in your hosting environment.")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newtrip", newtrip))
    app.add_handler(CommandHandler("paid", paid))
    app.add_handler(CommandHandler("fare", fare))
    app.add_handler(CommandHandler("expense", expense))
    app.add_handler(CommandHandler("fuelkm", fuelkm))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("close", close_trip))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text))
    log.info("Logsti is starting")
    app.run_polling()

if __name__ == "__main__":
    main()
