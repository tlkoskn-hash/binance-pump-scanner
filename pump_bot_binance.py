import asyncio
import requests
import os
import time
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler
)

# ================== НАСТРОЙКИ ==================

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USERS = {1128293345}

BINANCE = "https://fapi.binance.com"

cfg = {
    "long_period": 1,
    "long_percent": 1.0,
    "short_period": 10,
    "short_percent": 30.0,
    "enabled": False,
    "chat_id": None
}

price_snapshots = {}   # {period: {symbol: price}}
signals_today = {}
scanner_running = False

# ================== BINANCE ==================

def get_symbols():
    r = requests.get(f"{BINANCE}/fapi/v1/exchangeInfo", timeout=10).json()
    return [
        s["symbol"]
        for s in r["symbols"]
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    ]

def get_price(symbol):
    r = requests.get(
        f"{BINANCE}/fapi/v1/ticker/price",
        params={"symbol": symbol},
        timeout=5
    ).json()
    return float(r["price"])

# ================== UI ==================

def settings_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 Период ЛОНГ", callback_data="long_period"),
            InlineKeyboardButton("📈 % ЛОНГ", callback_data="long_percent")
        ],
        [
            InlineKeyboardButton("📉 Период ШОРТ", callback_data="short_period"),
            InlineKeyboardButton("📉 % ШОРТ", callback_data="short_percent")
        ],
        [
            InlineKeyboardButton("▶️ ВКЛ", callback_data="on"),
            InlineKeyboardButton("⛔ ВЫКЛ", callback_data="off")
        ]
    ])

# ================== COMMANDS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        return

    cfg["chat_id"] = update.effective_chat.id

    text = (
        "🤖 <b>PUMP Screener Binance</b>\n\n"
        "Я сканирую рынок:\n"
        "📈 маленькие пампы — для ЛОНГА\n"
        "📉 большие пампы — для ШОРТА\n\n"
        "Настройки ниже ⬇️"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=settings_keyboard()
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    context.user_data["edit"] = q.data
    await q.message.reply_text(
        f"Введи значение для: <b>{q.data}</b>",
        parse_mode="HTML"
    )

async def text_handler(update: Update, context):
    key = context.user_data.get("edit")
    if not key:
        return

    value = float(update.message.text)
    cfg[key] = int(value) if "period" in key else value
    context.user_data["edit"] = None

    await update.message.reply_text("✅ Сохранено", reply_markup=settings_keyboard())

async def on(update: Update, context):
    cfg["enabled"] = True
    await update.message.reply_text("▶️ Сканер включен")

async def off(update: Update, context):
    cfg["enabled"] = False
    await update.message.reply_text("⛔ Сканер выключен")

# ================== SCANNER ==================

async def scanner():
    global scanner_running
    if scanner_running or not cfg["enabled"]:
        return

    scanner_running = True

    try:
        symbols = get_symbols()
        periods = {cfg["long_period"], cfg["short_period"]}

        for p in periods:
            price_snapshots.setdefault(p, {})

        for s in symbols:
            price = get_price(s)

            for p in periods:
                prev = price_snapshots[p].get(s)
                if not prev:
                    price_snapshots[p][s] = price
                    continue

                pct = (price - prev) / prev * 100
                today = str(date.today())
                key = (s, today, p)

                # ЛОНГ
                if p == cfg["long_period"] and pct >= cfg["long_percent"]:
                    await send_signal("📈 ЛОНГ", s, pct, p)

                # ШОРТ
                if p == cfg["short_period"] and pct >= cfg["short_percent"]:
                    await send_signal("📉 ШОРТ", s, pct, p)

                price_snapshots[p][s] = price

            await asyncio.sleep(0.05)

    finally:
        scanner_running = False

async def send_signal(side, symbol, pct, period):
    msg = (
        f"{side} <b>СИГНАЛ</b>\n"
        f"🪙 <b>{symbol}</b>\n"
        f"📈 Рост: {pct:.2f}%\n"
        f"⏱ За {period} мин"
    )

    await app.bot.send_message(
        chat_id=cfg["chat_id"],
        text=msg,
        parse_mode="HTML"
    )

# ================== MAIN ==================

async def loop_job(context):
    await scanner()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(CommandHandler("on", on))
app.add_handler(CommandHandler("off", off))
app.add_handler(CommandHandler("text", text_handler))

app.job_queue.run_repeating(loop_job, interval=60, first=10)

print(">>> PUMP SCREENER RUNNING <<<")
app.run_polling()
