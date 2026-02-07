import asyncio
import requests
import os
import time
from datetime import date, datetime
from collections import deque
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

POLL_INTERVAL = 5          # ⏱ минимальный интервал опроса (сек)
HISTORY_LIMIT_MIN = 120    # сколько минут истории хранить

cfg = {
    "long_period": 10,     # минуты
    "long_percent": 3.0,
    "short_period": 30,    # минуты
    "short_percent": 8.0,
    "enabled": False,
    "chat_id": None,
}

# ================== ХРАНИЛИЩА ==================

price_history = {}        # symbol -> deque[(ts, price)]
last_signal_time = {}     # (symbol, side) -> timestamp
signals_today = {}        # (symbol, date) -> count
SYMBOLS = []

# ================== BINANCE ==================

def load_symbols():
    r = requests.get(f"{BINANCE}/fapi/v1/exchangeInfo", timeout=10).json()
    return [
        s["symbol"]
        for s in r.get("symbols", [])
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    ]

def get_all_prices():
    r = requests.get(f"{BINANCE}/fapi/v1/ticker/price", timeout=10).json()
    return {x["symbol"]: float(x["price"]) for x in r}

# ================== UI ==================

def keyboard():
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
        f"▶️ Включен: <b>{cfg['enabled']}</b>\n\n"
        "📈 <b>ЛОНГ</b>\n"
        f"• Период: {cfg['long_period']} мин\n"
        f"• Рост: {cfg['long_percent']}%\n\n"
        "📉 <b>ШОРТ</b>\n"
        f"• Период: {cfg['short_period']} мин\n"
        f"• Рост: {cfg['short_percent']}%\n\n"
        f"⏱ <i>{now}</i>"
    )

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    cfg["chat_id"] = update.effective_chat.id
    await update.message.reply_text(
        status_text(), parse_mode="HTML", reply_markup=keyboard()
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data

    if action == "on":
        cfg["enabled"] = True
    elif action == "off":
        cfg["enabled"] = False
    elif action == "status":
        pass
    else:
        context.user_data["edit"] = action
        await q.message.reply_text(
            f"Введи значение для <b>{action}</b>",
            parse_mode="HTML"
        )
        return

    await q.message.edit_text(
        status_text(), parse_mode="HTML", reply_markup=keyboard()
    )

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

    await update.message.reply_text("✅ Сохранено", reply_markup=keyboard())

# ================== SCANNER ==================

def get_price_minutes_ago(history, minutes):
    target = time.time() - minutes * 60
    for ts, price in history:
        if ts >= target:
            return price
    return None

async def scanner_loop():
    global SYMBOLS

    SYMBOLS = load_symbols()
    print(f"[INFO] Symbols loaded: {len(SYMBOLS)}")

    while True:
        if cfg["enabled"] and cfg["chat_id"]:
            try:
                prices = get_all_prices()
                now = time.time()

                for symbol in SYMBOLS:
                    price = prices.get(symbol)
                    if not price:
                        continue

                    history = price_history.setdefault(
                        symbol,
                        deque(maxlen=int(HISTORY_LIMIT_MIN * 60 / POLL_INTERVAL))
                    )
                    history.append((now, price))

                    # ===== ЛОНГ =====
                    check_signal(symbol, history, price,
                                 cfg["long_period"], cfg["long_percent"], "LONG")

                    # ===== ШОРТ =====
                    check_signal(symbol, history, price,
                                 cfg["short_period"], cfg["short_percent"], "SHORT")

            except Exception as e:
                print("Scanner error:", e)

        await asyncio.sleep(POLL_INTERVAL)

def check_signal(symbol, history, current_price, period_min, percent, side):
    old_price = get_price_minutes_ago(history, period_min)
    if not old_price:
        return

    pct = (current_price - old_price) / old_price * 100
    if pct < percent:
        return

    key = (symbol, side)
    last_ts = last_signal_time.get(key, 0)

    if time.time() - last_ts < period_min * 60:
        return  # антиспам

    last_signal_time[key] = time.time()
    asyncio.create_task(send_signal(symbol, pct, period_min, side))

# ================== SIGNAL ==================

async def send_signal(symbol, pct, period, side):
    today = str(date.today())
    signals_today[(symbol, today)] = signals_today.get((symbol, today), 0) + 1

    link = f"https://www.coinglass.com/tv/Binance_{symbol}"
    emoji = "🟢" if side == "LONG" else "🔴"

    msg = (
        f"{emoji} <b>{side} СИГНАЛ</b>\n"
        f"🪙 <b><a href='{link}'>{symbol}</a></b>\n"
        f"📈 Рост: {pct:.2f}%\n"
        f"⏱ За {period} мин\n"
        f"🔁 Сигнал 24h: {signals_today[(symbol, today)]}"
    )

    await app.bot.send_message(
        chat_id=cfg["chat_id"],
        text=msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

# ================== START ==================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print(">>> PUMP SCREENER RUNNING <<<")
app.run_polling(close_loop=False)

asyncio.get_event_loop().create_task(scanner_loop())
