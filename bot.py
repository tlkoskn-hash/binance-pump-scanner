import asyncio
import requests
import os
from datetime import datetime, timedelta, timezone
from collections import deque, defaultdict

from telegram import (
    Update,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
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
COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"

UTC_PLUS_3 = timezone(timedelta(hours=3))

cfg = {
    "chat_id": None,

    "long_period": 2,
    "long_percent": 2.0,

    "short_period": 20,
    "short_percent": 8.0,

    "dump_period": 30,
    "dump_percent": 5.0,

    "mode": "exclude_top",  # "all" или "exclude_top"
}

scanner_running = False
price_history = defaultdict(deque)
signals_today = defaultdict(int)

SYMBOLS_CACHE = []
LAST_SYMBOL_UPDATE = None

TOP_MARKETCAP_LIMIT = 50
MARKETCAP_REFRESH_SEC = 7 * 24 * 60 * 60
top_marketcap = set()

# ================== KEYBOARDS ==================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📊 Статус"],
            ["⚙️ Настройки"],
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def settings_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🕝 ЛОНГ период", "📈 ЛОНГ %"],
            ["🕝 ШОРТ период", "📉 ШОРТ %"],
            ["🕝 DUMP период", "📉 DUMP %"],
            ["📊 Все пары", "🚫 - топ 50 по кап"],
            ["🔙 Назад"],
        ],
        resize_keyboard=True,
        is_persistent=True
    )

# ================== MARKETCAP ==================

async def load_top_marketcap():
    global top_marketcap
    try:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": TOP_MARKETCAP_LIMIT,
            "page": 1,
        }

        r = requests.get(COINGECKO, params=params, timeout=15).json()

        top_marketcap = {
            f"{coin['symbol'].upper()}USDT"
            for coin in r
            if isinstance(coin, dict) and "symbol" in coin
        }

        print(f"[MARKETCAP] Loaded top {len(top_marketcap)}")

    except Exception as e:
        print("[MARKETCAP ERROR]", e)

async def weekly_marketcap_update():
    while True:
        await asyncio.sleep(MARKETCAP_REFRESH_SEC)
        await load_top_marketcap()

# ================== BINANCE ==================

def get_symbols():
    global SYMBOLS_CACHE, LAST_SYMBOL_UPDATE

    if SYMBOLS_CACHE and LAST_SYMBOL_UPDATE:
        if datetime.now() - LAST_SYMBOL_UPDATE < timedelta(hours=1):
            return SYMBOLS_CACHE

    r = requests.get(f"{BINANCE}/fapi/v1/ticker/24hr", timeout=10).json()

    if cfg["mode"] == "exclude_top":
        symbols = [
            s for s in r
            if s["symbol"].endswith("USDT")
            and s["symbol"] not in top_marketcap
        ]
    else:
        symbols = [
            s for s in r
            if s["symbol"].endswith("USDT")
        ]

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

# ================== STATUS ==================

def status_text():
    now = datetime.now(UTC_PLUS_3).strftime("%H:%M:%S")
    mode_text = "Все пары" if cfg["mode"] == "all" else f"- топ {TOP_MARKETCAP_LIMIT} по кап"

    return (
        "🤖 <b>PUMP / DUMP Screener Binance</b>\n\n"
        "🟢 <b>ЛОНГ</b>\n"
        f"• {cfg['long_period']} мин / {cfg['long_percent']}%\n\n"
        "🔴 <b>ШОРТ</b>\n"
        f"• {cfg['short_period']} мин / {cfg['short_percent']}%\n\n"
        "⏬ <b>DUMP</b>\n"
        f"• {cfg['dump_period']} мин / {cfg['dump_percent']}%\n\n"
        f"📊 Режим: <b>{mode_text}</b>\n\n"
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
        reply_markup=main_keyboard(),
    )

# ================== TEXT HANDLER ==================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        return

    text = update.message.text

    # ===== 1. Если сейчас ввод числа =====
    key = context.user_data.get("edit")
    if key:
        try:
            value = float(text)
            cfg[key] = int(value) if "period" in key else value
            context.user_data["edit"] = None

            # Показываем обновлённый статус
            await update.message.reply_text(
                status_text(),
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )

        except:
            await update.message.reply_text("❌ Введите число")

        return

    # ===== 2. Кнопки главного меню =====

    if text == "📊 Статус":
        await update.message.reply_text(
            status_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )
        return

    if text == "⚙️ Настройки":
        await update.message.reply_text(
            "⚙️ Меню настроек",
            reply_markup=settings_keyboard()
        )
        return

    if text == "📊 Все пары":
        cfg["mode"] = "all"
        await update.message.reply_text(
            "Режим: Все пары",
            reply_markup=settings_keyboard()
        )
        return

    if text == "🚫 - топ 50 по кап":
        cfg["mode"] = "exclude_top"
        await update.message.reply_text(
            "Режим: - топ 50 по капитализации",
            reply_markup=settings_keyboard()
        )
        return

    if text == "🔙 Назад":
        await update.message.reply_text(
            status_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )
        return

    # ===== 3. Переход в режим редактирования =====

    mapping = {
        "🕝 ЛОНГ период": "long_period",
        "📈 ЛОНГ %": "long_percent",
        "🕝 ШОРТ период": "short_period",
        "📉 ШОРТ %": "short_percent",
        "🕝 DUMP период": "dump_period",
        "📉 DUMP %": "dump_percent",
    }

    if text in mapping:
        context.user_data["edit"] = mapping[text]
        await update.message.reply_text("Введите число:")
        return

# ================== ОСТАЛЬНОЙ КОД БЕЗ ИЗМЕНЕНИЙ ==================

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


async def scanner_loop():
    global scanner_running

    if scanner_running:
        return

    scanner_running = True
    print(">>> PUMP / DUMP scanner loop started <<<")

    try:
        while True:
            cycle_start = datetime.now(UTC_PLUS_3)

            if not cfg["chat_id"]:
                await asyncio.sleep(1)
                continue

            symbols = get_symbols()
            now = datetime.now(UTC_PLUS_3)

            for s in symbols:
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

async def on_startup(app):
    await load_top_marketcap()
    asyncio.create_task(weekly_marketcap_update())
    asyncio.create_task(scanner_loop())

app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print(">>> PUMP / DUMP SCREENER RUNNING <<<")
app.run_polling()

