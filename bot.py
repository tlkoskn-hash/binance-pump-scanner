import asyncio
import requests
import os
from datetime import date, datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
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
    "chat_id": None,
}

price_snapshots = {}   # {period: {symbol: price}}
signals_today = {}     # {(symbol, date): count}
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
        timeout=5,
    ).json()
    return float(r["price"])

# ================== UI ==================

def settings_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕝 Период ЛОНГ", callback_data="long_period"),
            InlineKeyboardButton("📈 % ЛОНГ", callback_data="long_percent"),
        ],
        [
            InlineKeyboardButton("🕝 Период ШОРТ", callback_data="short_period"),
            InlineKeyboardButton("📉 % ШОРТ", callback_data="short_percent"),
        ],
        [
            InlineKeyboardButton("📊 Статус", callback_data="status"),
        ],
        [
            InlineKeyboardButton("▶️ ВКЛ", callback_data="on"),
            InlineKeyboardButton("⛔ ВЫКЛ", callback_data="off"),
        ],
    ])

def status_text():
    now = datetime.now().strftime("%H:%M:%S")
    return (
        "🤖 <b>PUMP Screener Binance</b>\n\n"
        "Я сканирую рынок:\n"
        "📈 маленькие пампы — для <b>ЛОНГА</b>\n"
        "📉 большие пампы — для <b>ШОРТА</b>\n\n"
        "<b>Текущие настройки:</b>\n"
        f"▶️ Включен: <b>{cfg['enabled']}</b>\n\n"
        "📈 <b>ЛОНГ</b>\n"
        f"• Период: {cfg['long_period']} мин\n"
        f"• Рост: {cfg['long_percent']}%\n\n"
        "📉 <b>ШОРТ</b>\n"
        f"• Период: {cfg['short_period']} мин\n"
        f"• Рост: {cfg['short_percent']}%\n\n"
        f"⏱ <i>Обновлено: {now}</i>"
    )

# ================== COMMANDS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    cfg["chat_id"] = update.effective_chat.id
    await update.message.reply_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )

# ================== CALLBACK BUTTONS ==================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanner_running

    q = update.callback_query
    await q.answer()
    action = q.data

    if action == "on":
        cfg["enabled"] = True

    elif action == "off":
        cfg["enabled"] = False
        scanner_running = False  # ⛔ мгновенно останавливаем

    elif action == "status":
        pass

    else:
        context.user_data["edit"] = action
        await q.message.reply_text(
            f"Введи значение для: <b>{action}</b>",
            parse_mode="HTML",
        )
        return

    await q.message.edit_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )

# ================== TEXT INPUT ==================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit")
    if not key:
        return

    try:
        value = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Введи число")
        return

    cfg[key] = int(value) if "period" in key else value
    context.user_data["edit"] = None

    await update.message.reply_text(
        "✅ Сохранено",
        reply_markup=settings_keyboard(),
    )

# ================== SCANNER ==================

async def scanner():
    global scanner_running

    if scanner_running or not cfg["enabled"] or not cfg["chat_id"]:
        return

    scanner_running = True

    try:
        symbols = get_symbols()
        periods = {cfg["long_period"], cfg["short_period"]}

        for p in periods:
            price_snapshots.setdefault(p, {})

        for s in symbols:
            if not cfg["enabled"]:
                break

            price = get_price(s)

            for p in periods:
                if not cfg["enabled"]:
                    break

                prev = price_snapshots[p].get(s)
                if not prev:
                    price_snapshots[p][s] = price
                    continue

                pct = (price - prev) / prev * 100

                if p == cfg["long_period"] and pct >= cfg["long_percent"]:
                    await send_signal("🟢 ЛОНГ", s, pct, p)

                if p == cfg["short_period"] and pct >= cfg["short_percent"]:
                    await send_signal("🔴 ШОРТ", s, pct, p)

                price_snapshots[p][s] = price

            await asyncio.sleep(0.05)

    finally:
        scanner_running = False

# ================== SIGNAL ==================

async def send_signal(side, symbol, pct, period):
    if not cfg["enabled"] or not cfg["chat_id"]:
        return

    today = str(date.today())
    key = (symbol, today)
    count = signals_today.get(key, 0) + 1
    signals_today[key] = count

    coinglass_link = f"https://www.coinglass.com/tv/Binance_{symbol}"

    msg = (
        f"{side} <b>СИГНАЛ</b>\n"
        f"🪙 <b><a href='{coinglass_link}'>{symbol}</a></b>\n"
        f"📈 Рост: {pct:.2f}%\n"
        f"⏱ За {period} мин\n"
        f"🔁 <b>Сигнал 24h:</b> {count}"
    )

    await app.bot.send_message(
        chat_id=cfg["chat_id"],
        text=msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

# ================== MAIN ==================

async def loop_job(context):
    await scanner()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("status", status))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

app.job_queue.run_repeating(loop_job, interval=60, first=10)

print(">>> PUMP SCREENER RUNNING <<<")
app.run_polling()
