import asyncio
import requests
import os
from datetime import datetime, timedelta, timezone
from collections import deque, defaultdict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

TOKEN = os.getenv("BOT_TOKEN")

ALLOWED_USERS = set(
    int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()
)

BINANCE = "https://fapi.binance.com"
UTC_PLUS_3 = timezone(timedelta(hours=3))

cfg = {
    "enabled": False,
    "chat_id": None,

    "long_period": 2,
    "long_percent": 2.0,

    "short_period": 20,
    "short_percent": 8.0,

    "dump_period": 30,
    "dump_percent": 5.0,
}

scanner_running = False

price_history = defaultdict(deque)
signals_today = defaultdict(int)

SYMBOLS_CACHE = []
LAST_SYMBOL_UPDATE = None

# ================== BINANCE ==================

def get_symbols():
    global SYMBOLS_CACHE, LAST_SYMBOL_UPDATE

    if SYMBOLS_CACHE and LAST_SYMBOL_UPDATE:
        if datetime.now() - LAST_SYMBOL_UPDATE < timedelta(hours=1):
            return SYMBOLS_CACHE

    r = requests.get(f"{BINANCE}/fapi/v1/ticker/24hr", timeout=10).json()

    symbols = [s for s in r if s["symbol"].endswith("USDT")]
    symbols.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)

    SYMBOLS_CACHE = [s["symbol"] for s in symbols[:100]]
    LAST_SYMBOL_UPDATE = datetime.now()
    return SYMBOLS_CACHE


def get_price(symbol):
    try:
        r = requests.get(
            f"{BINANCE}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        ).json()

        if "price" not in r:
            return None

        return float(r["price"])

    except Exception as e:
        print(f"[WARN] price timeout {symbol}: {e}")
        return None

# ================== UI ==================

def keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕝 ЛОНГ период", callback_data="long_period"),
            InlineKeyboardButton("📈 ЛОНГ %", callback_data="long_percent"),
        ],
        [
            InlineKeyboardButton("🕝 ШОРТ период", callback_data="short_period"),
            InlineKeyboardButton("📉 ШОРТ %", callback_data="short_percent"),
        ],
        [
            InlineKeyboardButton("🕝 DUMP период", callback_data="dump_period"),
            InlineKeyboardButton("📉 DUMP %", callback_data="dump_percent"),
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
    now = datetime.now(UTC_PLUS_3).strftime("%H:%M:%S")
    return (
        "🤖 <b>PUMP / DUMP Screener Binance</b>\n\n"
        f"▶️ Включен: <b>{cfg['enabled']}</b>\n\n"
        "🟢 <b>ЛОНГ</b>\n"
        f"• {cfg['long_period']} мин / {cfg['long_percent']}%\n\n"
        "🔴 <b>ШОРТ</b>\n"
        f"• {cfg['short_period']} мин / {cfg['short_percent']}%\n\n"
        "⏬ <b>DUMP</b>\n"
        f"• {cfg['dump_period']} мин / {cfg['dump_percent']}%\n\n"
        f"⏱ Рынок обновлён: <i>{now} (UTC+3)</i>"
    )

# ================== COMMANDS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        return

    cfg["chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=keyboard(),
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ================== BUTTON HANDLER ==================

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
            f"Введи значение для: <b>{action}</b>",
            parse_mode="HTML",
        )
        return

    await q.message.edit_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=keyboard(),
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

    await update.message.reply_text("✅ Сохранено", reply_markup=keyboard())

# ================== CHECK SIGNAL ==================

async def check_signal(side, symbol, history, period_min, percent, is_up):
    now = datetime.now(UTC_PLUS_3)
    cutoff = now - timedelta(minutes=period_min)

    prices = [p for t, p in history if t >= cutoff]
    if len(prices) < 2:
        return

    start_price = prices[0]
    last_price = prices[-1]

    change = (last_price - start_price) / start_price * 100

    if is_up and change >= percent:
        await send_signal(side, symbol, change, period_min)
        history.clear()

    if not is_up and change <= -percent:
        await send_signal(side, symbol, abs(change), period_min)
        history.clear()

# ================== SCANNER ==================

async def scanner_loop():
    global scanner_running

    if scanner_running:
        return

    scanner_running = True
    print(">>> PUMP / DUMP scanner loop started <<<")

    try:
        symbols = get_symbols()

        while True:
            cycle_start = datetime.now(UTC_PLUS_3)

            if not cfg["enabled"] or not cfg["chat_id"]:
                await asyncio.sleep(1)
                continue

            now = datetime.now(UTC_PLUS_3)

            for s in symbols:
                if not cfg["enabled"]:
                    break

                price = get_price(s)
                if price is None:
                    continue

                history = price_history[s]
                history.append((now, price))

                while history and (now - history[0][0]).total_seconds() > 3600:
                    history.popleft()

                await check_signal("🟢 ЛОНГ", s, history, cfg["long_period"], cfg["long_percent"], True)
                await check_signal("🔴 ШОРТ", s, history, cfg["short_period"], cfg["short_percent"], True)
                await check_signal("⏬ DUMP", s, history, cfg["dump_period"], cfg["dump_percent"], False)

                await asyncio.sleep(0.05)

            cycle_time = (datetime.now(UTC_PLUS_3) - cycle_start).total_seconds()
            print(f"[PUMP/DUMP] Цикл занял: {cycle_time:.2f} сек")

            await asyncio.sleep(10)

    finally:
        scanner_running = False

# ================== SIGNAL ==================

async def send_signal(side, symbol, pct, period):
    today = datetime.now(UTC_PLUS_3).date()
    signals_today[(symbol, today)] += 1
    count = signals_today[(symbol, today)]

    link = f"https://www.coinglass.com/tv/Binance_{symbol}"
    sign = "-" if "DUMP" in side else "+"

    msg = (
        f"{side} <b>СИГНАЛ</b>\n"
        f"🪙 <b><a href='{link}'>{symbol}</a></b>\n"
        f"📈 Изменение: {sign}{pct:.2f}%\n"
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

async def on_startup(app):
    asyncio.create_task(scanner_loop())

app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print(">>> PUMP / DUMP SCREENER RUNNING <<<")
app.run_polling()

