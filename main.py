
# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT
# XAUUSD + BTCUSD
# TELEGRAM + BIQUOTE + GITHUB ACTIONS
#
# ANALYSIS:
# LAST 100 CLOSED CANDLES
# FVG + SNR + SND + BOS
# 4 CONFIRMATIONS
#
# TIMEFRAMES:
# M1 M5 M15 M30 H1 H4
#
# MANUAL ANALYSIS ONLY
# ============================================================

import os
import sys
import logging
import asyncio
import requests

from datetime import datetime, timezone
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

BASE_URL = "https://biquote.io"

MARKETS = {
    "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD"
}

TIMEFRAMES = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h"
}

CANDLE_COUNT = 100
FETCH_COUNT = 120

MIN_CONFIRMATIONS = 3

REQUEST_TIMEOUT = 20

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

log = logging.getLogger("SMC_BOT")

# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 SMC-Trading-Bot/1.0",
    "Accept": "application/json"
})

# ============================================================
# TELEGRAM MENU
# ============================================================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton("🥇 XAUUSD", callback_data="market_XAUUSD"),
            InlineKeyboardButton("₿ BTCUSD", callback_data="market_BTCUSD")
        ],
        [
            InlineKeyboardButton("📅 Kalender Ekonomi", callback_data="calendar"),
            InlineKeyboardButton("📰 Berita Market", callback_data="news")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Menu", callback_data="menu")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def timeframe_menu(symbol):

    keyboard = []

    row = []

    for tf in TIMEFRAMES:

        row.append(
            InlineKeyboardButton(
                tf,
                callback_data=f"tf_{symbol}_{tf}"
            )
        )

        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Kembali",
            callback_data="menu"
        )
    ])

    return InlineKeyboardMarkup(keyboard)


def analysis_menu(symbol, timeframe):

    keyboard = [
        [
            InlineKeyboardButton(
                "🔍 ANALISA SEKARANG",
                callback_data=f"analyze_{symbol}_{timeframe}"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Ganti Timeframe",
                callback_data=f"market_{symbol}"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu Utama",
                callback_data="menu"
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# API REQUEST
# ============================================================

def api_get(endpoint, params=None):

    url = BASE_URL + endpoint

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):

        if data.get("success") is False:
            raise ValueError(
                str(data.get("message", "API returned success=false"))
            )

        if data.get("error"):
            raise ValueError(str(data["error"]))

    return data


# ============================================================
# CANDLE PARSER
# ============================================================

def extract_candle_list(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        raise ValueError("Format data candle tidak dikenal")

    for key in ("data", "candles", "ohlc", "result", "items"):

        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):

            for nested in ("candles", "ohlc", "data", "items"):
                if isinstance(value.get(nested), list):
                    return value[nested]

    raise ValueError(
        "Data candle tidak ditemukan dalam respons API"
    )


def number_value(candle, keys):

    for key in keys:

        value = candle.get(key)

        if value is not None:
            return float(value)

    raise ValueError(
        "Kolom candle tidak ditemukan: " + str(keys)
    )


def normalize_candles(raw):

    candles = []

    for item in raw:

        if not isinstance(item, dict):
            continue

        try:

            candle = {
                "open": number_value(item, ["open", "o"]),
                "high": number_value(item, ["high", "h"]),
                "low": number_value(item, ["low", "l"]),
                "close": number_value(item, ["close", "c"]),
                "time": (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("date")
                    or item.get("t")
                    or 0
                )
            }

            is_open = item.get("isOpen")

            if is_open is None:
                is_open = item.get("is_open")

            candle["is_open"] = is_open

            candles.append(candle)

        except (ValueError, TypeError):
            continue

    # Jika API menyediakan status candle terbuka,
    # buang candle yang masih berjalan.
    if any(c["is_open"] is not None for c in candles):

        candles = [
            c for c in candles
            if c["is_open"] is not True
            and str(c["is_open"]).lower() != "true"
            and str(c["is_open"]).lower() != "1"
        ]

    # Urutkan dari candle lama ke candle terbaru.
    try:
        candles.sort(key=lambda x: float(x["time"]))
    except (ValueError, TypeError):
        pass

    # Ambil tepat maksimal 100 candle terakhir.
    candles = candles[-CANDLE_COUNT:]

    if len(candles) < 30:
        raise ValueError(
            f"Candle tidak cukup: hanya {len(candles)} candle tersedia"
        )

    return candles


def get_candles(symbol, timeframe):

    interval = TIMEFRAMES[timeframe]

    endpoint = f"/api/{symbol}/ohlc"

    data = api_get(
        endpoint,
        params={
            "interval": interval,
            "limit": FETCH_COUNT
        }
    )

    raw = extract_candle_list(data)

    candles = normalize_candles(raw)

    if len(candles) < CANDLE_COUNT:
        log.warning(
            "%s %s hanya memperoleh %s candle",
            symbol,
            timeframe,
            len(candles)
        )

    log.info(
        "Candle berhasil: %s %s | %s candle",
        symbol,
        timeframe,
        len(candles)
    )

    return candles


# ============================================================
# SMC ANALYSIS
# ============================================================

def detect_fvg(candles):

    bullish = False
    bearish = False

    gap_low = None
    gap_high = None

    # Periksa FVG terbaru dari 100 candle.
    for i in range(2, len(candles)):

        first = candles[i - 2]
        third = candles[i]

        # Bullish FVG
        if third["low"] > first["high"]:

            bullish = True
            bearish = False

            gap_low = first["high"]
            gap_high = third["low"]

        # Bearish FVG
        elif third["high"] < first["low"]:

            bearish = True
            bullish = False

            gap_low = third["high"]
            gap_high = first["low"]

    if bullish:
        return "BUY", gap_low, gap_high

    if bearish:
        return "SELL", gap_low, gap_high

    return "NEUTRAL", None, None


def detect_bos(candles):

    recent = candles[-1]

    previous = candles[-11:-1]

    highest = max(c["high"] for c in previous)
    lowest = min(c["low"] for c in previous)

    if recent["close"] > highest:
        return "BUY"

    if recent["close"] < lowest:
        return "SELL"

    return "NEUTRAL"


def detect_snr(candles):

    recent = candles[-1]

    previous = candles[-30:-1]

    support = min(c["low"] for c in previous)
    resistance = max(c["high"] for c in previous)

    price = recent["close"]

    distance_support = abs(price - support)
    distance_resistance = abs(resistance - price)

    average_range = sum(
        c["high"] - c["low"] for c in previous
    ) / len(previous)

    threshold = average_range * 1.5

    if distance_support <= threshold:
        return "BUY", support

    if distance_resistance <= threshold:
        return "SELL", resistance

    return "NEUTRAL", None


def detect_snd(candles):

    recent = candles[-1]

    previous = candles[-20:-1]

    price = recent["close"]

    demand = None
    supply = None

    # Cari candle impulsif dari rangkaian 100 candle terakhir.
    for i in range(1, len(previous)):

        current = previous[i]
        before = previous[i - 1]

        current_range = current["high"] - current["low"]

        if current_range <= 0:
            continue

        body = abs(current["close"] - current["open"])

        # Bullish displacement
        if (
            current["close"] > current["open"]
            and body >= current_range * 0.65
            and current["close"] > before["high"]
        ):

            demand = current["low"]

        # Bearish displacement
        if (
            current["close"] < current["open"]
            and body >= current_range * 0.65
            and current["close"] < before["low"]
        ):

            supply = current["high"]

    if demand is not None:

        if price >= demand and price <= demand + (
            recent["high"] - recent["low"]
        ) * 2:

            return "BUY", demand

    if supply is not None:

        if price <= supply and price >= supply - (
            recent["high"] - recent["low"]
        ) * 2:

            return "SELL", supply

    return "NEUTRAL", None


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def analyze_market(symbol, timeframe):

    candles = get_candles(symbol, timeframe)

    last = candles[-1]

    price = last["close"]

    fvg_signal, fvg_low, fvg_high = detect_fvg(candles)

    bos_signal = detect_bos(candles)

    snr_signal, snr_level = detect_snr(candles)

    snd_signal, snd_level = detect_snd(candles)

    signals = {
        "FVG": fvg_signal,
        "SNR": snr_signal,
        "SND": snd_signal,
        "BOS": bos_signal
    }

    buy_votes = sum(
        1 for value in signals.values()
        if value == "BUY"
    )

    sell_votes = sum(
        1 for value in signals.values()
        if value == "SELL"
    )

    if buy_votes > sell_votes:
        direction = "BUY"
        confirmations = buy_votes

    elif sell_votes > buy_votes:
        direction = "SELL"
        confirmations = sell_votes

    else:
        direction = "WAIT"
        confirmations = max(buy_votes, sell_votes)

    if direction == "WAIT" or confirmations < 2:

        status = "BELUM ADA SETUP VALID"

    elif confirmations >= 4:

        status = "SANGAT KUAT"

    elif confirmations >= 3:

        status = "KUAT"

    else:

        status = "POTENSIAL"

    # Hitung jarak SL berdasarkan rata-rata range candle.
    recent = candles[-14:]

    average_range = sum(
        c["high"] - c["low"] for c in recent
    ) / len(recent)

    if average_range <= 0:
        average_range = price * 0.001

    if direction == "BUY":

        entry_low = price
        entry_high = price + average_range * 0.20

        sl = price - average_range * 1.5

        risk = entry_high - sl

        tp1 = entry_high + risk
        tp2 = entry_high + risk * 2
        tp3 = entry_high + risk * 3

    elif direction == "SELL":

        entry_low = price - average_range * 0.20
        entry_high = price

        sl = price + average_range * 1.5

        risk = sl - entry_low

        tp1 = entry_low - risk
        tp2 = entry_low - risk * 2
        tp3 = entry_low - risk * 3

    else:

        entry_low = price
        entry_high = price
        sl = None
        tp1 = None
        tp2 = None
        tp3 = None

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "direction": direction,
        "confirmations": confirmations,
        "status": status,
        "signals": signals,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "candle_count": len(candles),
        "fvg_low": fvg_low,
        "fvg_high": fvg_high,
        "snr_level": snr_level,
        "snd_level": snd_level
    }


# ============================================================
# FORMAT REPORT
# ============================================================

def format_price(value):

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    return f"{value:.6f}"


def create_report(result):

    symbol = result["symbol"]
    tf = result["timeframe"]

    direction = result["direction"]

    if direction == "BUY":
        emoji = "🟢"
    elif direction == "SELL":
        emoji = "🔴"
    else:
        emoji = "⚪"

    lines = [
        "📊 ANALISA SMC SCALPING",
        "━━━━━━━━━━━━━━━━━━",
        f"Market: {symbol}",
        f"Timeframe: {tf}",
        f"Harga: {format_price(result['price'])}",
        f"Candle dianalisis: {result['candle_count']}",
        "",
        f"{emoji} ARAH: {direction}",
        f"Status: {result['status']}",
        f"Konfirmasi: {result['confirmations']}/4",
        "",
        "📌 KONFIRMASI SMC",
        f"FVG : {result['signals']['FVG']}",
        f"SNR : {result['signals']['SNR']}",
        f"SND : {result['signals']['SND']}",
        f"BOS : {result['signals']['BOS']}",
    ]

    if direction in ("BUY", "SELL"):

        lines.extend([
            "",
            "🎯 RENCANA LEVEL",
            f"Entry: {format_price(result['entry_low'])} - "
            f"{format_price(result['entry_high'])}",
            f"Stop Loss: {format_price(result['sl'])}",
            f"TP 1: {format_price(result['tp1'])}",
            f"TP 2: {format_price(result['tp2'])}",
            f"TP 3: {format_price(result['tp3'])}"
        ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Catatan:",
        "Tunggu konfirmasi harga sebelum entry.",
        "Skor adalah jumlah konfirmasi teknikal,",
        "bukan persentase probabilitas profit.",
        "Level merupakan estimasi teknikal,",
        "bukan jaminan harga akan mencapainya."
    ])

    return "\n".join(lines)


# ============================================================
# CALENDAR & NEWS
# ============================================================

def get_calendar():

    data = api_get("/api/calendar/upcoming")

    return data


def get_news():

    data = api_get("/api/news/market")

    return data


def format_simple_data(data, title):

    if isinstance(data, dict):

        for key in ("data", "events", "news", "result", "items"):

            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        return f"{title}\n\nFormat data dari server tidak dikenali."

    if not data:
        return f"{title}\n\nBelum ada data tersedia."

    lines = [title, "━━━━━━━━━━━━━━━━━━"]

    for item in data[:10]:

        if isinstance(item, dict):

            name = (
                item.get("title")
                or item.get("event")
                or item.get("name")
                or "Informasi"
            )

            time = (
                item.get("time")
                or item.get("date")
                or item.get("datetime")
                or ""
            )

            impact = (
                item.get("impact")
                or item.get("importance")
                or ""
            )

            lines.append(f"\n• {name}")

            if time:
                lines.append(f"  Waktu: {time}")

            if impact:
                lines.append(f"  Dampak: {impact}")

        else:
            lines.append(f"\n• {str(item)}")

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    log.info("/start diterima dari user %s", update.effective_user.id)

    await update.message.reply_text(
        "🤖 MASTER SMC SCALPING BOT\n\n"
        "Pilih market untuk memulai analisis.\n\n"
        "Analisis menggunakan maksimal 100 candle terakhir.",
        reply_markup=main_menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🟢 BOT ONLINE\n"
        "Telegram polling aktif.\n"
        "Mode: Manual analysis.\n"
        "Candle: 100.\n"
        "Market: XAUUSD dan BTCUSD."
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Pilih menu:",
        reply_markup=main_menu()
    )


# ============================================================
# TELEGRAM CALLBACK
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    log.info("Callback diterima: %s", data)

    try:

        if data == "menu":

            await query.message.reply_text(
                "Pilih market:",
                reply_markup=main_menu()
            )

            return

        if data.startswith("market_"):

            symbol = data.replace("market_", "")

            if symbol not in MARKETS:
                await query.message.reply_text("Market tidak dikenal.")
                return

            await query.message.reply_text(
                f"Market: {symbol}\nPilih timeframe:",
                reply_markup=timeframe_menu(symbol)
            )

            return

        if data.startswith("tf_"):

            _, symbol, timeframe = data.split("_")

            if symbol not in MARKETS or timeframe not in TIMEFRAMES:
                await query.message.reply_text("Pilihan tidak valid.")
                return

            await query.message.reply_text(
                f"Market: {symbol}\n"
                f"Timeframe: {timeframe}\n\n"
                "Tekan tombol untuk menjalankan analisis.",
                reply_markup=analysis_menu(symbol, timeframe)
            )

            return

        if data.startswith("analyze_"):

            _, symbol, timeframe = data.split("_")

            if symbol not in MARKETS or timeframe not in TIMEFRAMES:
                await query.message.reply_text("Pilihan tidak valid.")
                return

            processing = await query.message.reply_text(
                f"⏳ Menganalisis {symbol} {timeframe}...\n"
                "Mengambil 100 candle terakhir."
            )

            try:

                result = await asyncio.to_thread(
                    analyze_market,
                    symbol,
                    timeframe
                )

                report = create_report(result)

                await processing.edit_text(report)

                log.info(
                    "Analisis selesai: %s %s",
                    symbol,
                    timeframe
                )

            except Exception as error:

                log.exception("Analisis gagal")

                await processing.edit_text(
                    "❌ ANALISIS GAGAL\n\n"
                    f"Market: {symbol}\n"
                    f"Timeframe: {timeframe}\n\n"
                    f"Detail error:\n{str(error)[:2500]}\n\n"
                    "Silakan coba lagi."
                )

            return

        if data == "calendar":

            message = await query.message.reply_text(
                "⏳ Mengambil kalender ekonomi..."
            )

            try:

                result = await asyncio.to_thread(get_calendar)

                text = format_simple_data(
                    result,
                    "📅 KALENDER EKONOMI"
                )

                await message.edit_text(text[:4000])

            except Exception as error:

                log.exception("Calendar error")

                await message.edit_text(
                    f"❌ Kalender gagal diambil:\n{str(error)[:2000]}"
                )

            return

        if data == "news":

            message = await query.message.reply_text(
                "⏳ Mengambil berita market..."
            )

            try:

                result = await asyncio.to_thread(get_news)

                text = format_simple_data(
                    result,
                    "📰 BERITA MARKET"
                )

                await message.edit_text(text[:4000])

            except Exception as error:

                log.exception("News error")

                await message.edit_text(
                    f"❌ Berita gagal diambil:\n{str(error)[:2000]}"
                )

            return

    except Exception:

        log.exception("Callback handler error")

        try:
            await query.message.reply_text(
                "❌ Terjadi kesalahan pada tombol. "
                "Silakan coba lagi."
            )
        except Exception:
            pass


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    log.error(
        "Telegram error: %s",
        context.error,
        exc_info=context.error
    )


# ============================================================
# STARTUP
# ============================================================

async def post_init(application: Application):

    log.info("========================================")
    log.info("MASTER SMC SCALPING BOT STARTED")
    log.info("Telegram initialization successful")
    log.info("Markets: XAUUSD, BTCUSD")
    log.info("Timeframes: M1 M5 M15 M30 H1 H4")
    log.info("Candles: LAST 100 CLOSED CANDLES")
    log.info("Strategy: FVG + SNR + SND + BOS")
    log.info("Mode: MANUAL")
    log.info("========================================")

    bot_info = await application.bot.get_me()

    log.info(
        "Telegram connected: @%s | ID: %s",
        bot_info.username,
        bot_info.id
    )


def main():

    if not BOT_TOKEN:

        log.error("BOT_TOKEN tidak ditemukan!")

        sys.exit(1)

    log.info("Memulai Telegram bot...")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    application.add_handler(
        CommandHandler("menu", menu)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_error_handler(error_handler)

    log.info("Menjalankan polling Telegram...")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        log.info("Bot dihentikan.")

    except Exception:

        log.exception("FATAL ERROR")
        raise
