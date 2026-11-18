
# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT
# XAUUSD + BTCUSD
# TELEGRAM + BIQUOTE + GITHUB ACTIONS
#
# 100 CANDLE TERAKHIR YANG SUDAH TERTUTUP
# FVG + SNR + SND + BOS
# MANUAL ANALYSIS ONLY
#
# TIMEFRAMES: M1 M5 M15 M30 H1 H4
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

MARKETS = ["XAUUSD", "BTCUSD"]

TIMEFRAMES = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h"
}

CANDLE_COUNT = 100
FETCH_COUNT = 150
REQUEST_TIMEOUT = 25

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
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 SMC-Bot/2.0",
    "Accept": "application/json"
})


def api_get(endpoint, params=None):

    url = BASE_URL + endpoint

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        raise ValueError(
            f"Respons API bukan JSON: {response.text[:500]}"
        )


# ============================================================
# CANDLE DATA PARSER - BIQUOTE
# ============================================================

def extract_candle_list(data):

    if not isinstance(data, dict):
        raise ValueError(
            f"Format respons candle bukan object: {type(data).__name__}"
        )

    bars = data.get("bars")

    if not isinstance(bars, list):
        raise ValueError(
            "Properti 'bars' tidak ditemukan. "
            f"Properti respons: {list(data.keys())}"
        )

    if not bars:
        raise ValueError("API mengembalikan bars kosong")

    return bars


def parse_candle_time(value):

    if value is None:
        raise ValueError("Waktu candle kosong")

    # Format ISO 8601, misalnya:
    # 2026-11-18T05:30:00Z
    if isinstance(value, str):

        value = value.strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        try:
            result = datetime.fromisoformat(value)

            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)

            return result.timestamp()

        except ValueError:
            pass

    # Format Unix timestamp
    number = float(value)

    # Milidetik ke detik
    if number > 100000000000:
        number /= 1000

    return number


def normalize_candles(raw):

    candles = []

    for item in raw:

        if not isinstance(item, dict):
            continue

        try:

            required = ["open", "high", "low", "close", "openTime"]

            missing = [
                key for key in required
                if item.get(key) is None
            ]

            if missing:
                continue

            candle = {
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "time": parse_candle_time(item["openTime"]),
                "is_open": item.get("isOpen", False)
            }

            # Validasi OHLC
            if candle["high"] < candle["low"]:
                continue

            if candle["high"] < max(
                candle["open"], candle["close"]
            ):
                continue

            if candle["low"] > min(
                candle["open"], candle["close"]
            ):
                continue

            # Buang candle yang masih terbuka
            is_open = candle["is_open"]

            if is_open is True:
                continue

            if str(is_open).lower() in ("true", "1"):
                continue

            candles.append(candle)

        except (ValueError, TypeError, KeyError):
            continue

    if not candles:
        raise ValueError(
            "Tidak ada candle valid setelah proses normalisasi"
        )

    # API mengirim newest-first.
    # Kita urutkan menjadi oldest-first.
    candles.sort(key=lambda candle: candle["time"])

    # Hilangkan timestamp duplikat
    unique = {}

    for candle in candles:
        unique[candle["time"]] = candle

    candles = sorted(
        unique.values(),
        key=lambda candle: candle["time"]
    )

    # Ambil tepat 100 candle tertutup paling baru
    candles = candles[-CANDLE_COUNT:]

    if len(candles) < CANDLE_COUNT:

        raise ValueError(
            f"Hanya tersedia {len(candles)} candle tertutup. "
            f"Dibutuhkan {CANDLE_COUNT} candle."
        )

    return candles


def get_candles(symbol, timeframe):

    if symbol not in MARKETS:
        raise ValueError("Market tidak valid")

    if timeframe not in TIMEFRAMES:
        raise ValueError("Timeframe tidak valid")

    interval = TIMEFRAMES[timeframe]

    endpoint = f"/api/{symbol}/ohlc"

    log.info(
        "Mengambil candle %s %s interval=%s",
        symbol, timeframe, interval
    )

    data = api_get(
        endpoint,
        params={
            "interval": interval,
            "limit": FETCH_COUNT
        }
    )

    raw = extract_candle_list(data)

    candles = normalize_candles(raw)

    log.info(
        "BERHASIL: %s %s mendapatkan %s candle tertutup",
        symbol,
        timeframe,
        len(candles)
    )

    return candles


# ============================================================
# SMC: FVG
# ============================================================

def detect_fvg(candles):

    for i in range(len(candles) - 1, 1, -1):

        first = candles[i - 2]
        third = candles[i]

        # Bullish FVG
        if third["low"] > first["high"]:

            return (
                "BUY",
                first["high"],
                third["low"]
            )

        # Bearish FVG
        if third["high"] < first["low"]:

            return (
                "SELL",
                third["high"],
                first["low"]
            )

    return "NEUTRAL", None, None


# ============================================================
# SMC: BOS
# ============================================================

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


# ============================================================
# SMC: SUPPORT & RESISTANCE
# ============================================================

def detect_snr(candles):

    recent = candles[-1]
    previous = candles[-30:-1]

    support = min(c["low"] for c in previous)
    resistance = max(c["high"] for c in previous)

    price = recent["close"]

    average_range = sum(
        c["high"] - c["low"] for c in previous
    ) / len(previous)

    threshold = average_range * 1.5

    if abs(price - support) <= threshold:
        return "BUY", support

    if abs(resistance - price) <= threshold:
        return "SELL", resistance

    return "NEUTRAL", None


# ============================================================
# SMC: SUPPLY & DEMAND
# ============================================================

def detect_snd(candles):

    recent = candles[-1]
    previous = candles[-20:-1]

    price = recent["close"]

    demand = None
    supply = None

    for i in range(1, len(previous)):

        current = previous[i]
        before = previous[i - 1]

        candle_range = current["high"] - current["low"]

        if candle_range <= 0:
            continue

        body = abs(
            current["close"] - current["open"]
        )

        # Bullish displacement
        if (
            current["close"] > current["open"]
            and body >= candle_range * 0.65
            and current["close"] > before["high"]
        ):
            demand = current["low"]

        # Bearish displacement
        if (
            current["close"] < current["open"]
            and body >= candle_range * 0.65
            and current["close"] < before["low"]
        ):
            supply = current["high"]

    current_range = max(
        recent["high"] - recent["low"],
        price * 0.0001
    )

    if demand is not None:

        if demand <= price <= demand + current_range * 2:
            return "BUY", demand

    if supply is not None:

        if supply - current_range * 2 <= price <= supply:
            return "SELL", supply

    return "NEUTRAL", None


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def analyze_market(symbol, timeframe):

    candles = get_candles(symbol, timeframe)

    last = candles[-1]
    price = last["close"]

    fvg, fvg_low, fvg_high = detect_fvg(candles)
    bos = detect_bos(candles)
    snr, snr_level = detect_snr(candles)
    snd, snd_level = detect_snd(candles)

    signals = {
        "FVG": fvg,
        "SNR": snr,
        "SND": snd,
        "BOS": bos
    }

    buy_votes = sum(
        value == "BUY" for value in signals.values()
    )

    sell_votes = sum(
        value == "SELL" for value in signals.values()
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

    if direction == "WAIT" or confirmations < 3:
        status = "BELUM ADA SETUP KUAT"

    elif confirmations == 3:
        status = "KUAT"

    else:
        status = "SANGAT KUAT"

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

        entry_low = entry_high = price
        sl = tp1 = tp2 = tp3 = None

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
# REPORT
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

    direction = result["direction"]

    emoji = {
        "BUY": "🟢",
        "SELL": "🔴",
        "WAIT": "⚪"
    }.get(direction, "⚪")

    lines = [
        "📊 ANALISA SMC SCALPING",
        "━━━━━━━━━━━━━━━━━━",
        f"Market: {result['symbol']}",
        f"Timeframe: {result['timeframe']}",
        f"Harga: {format_price(result['price'])}",
        f"Candle dianalisis: {result['candle_count']}/100",
        "",
        f"{emoji} ARAH: {direction}",
        f"Status: {result['status']}",
        f"Konfirmasi: {result['confirmations']}/4",
        "",
        "📌 KONFIRMASI SMC",
        f"FVG: {result['signals']['FVG']}",
        f"SNR: {result['signals']['SNR']}",
        f"SND: {result['signals']['SND']}",
        f"BOS: {result['signals']['BOS']}"
    ]

    if direction in ("BUY", "SELL"):

        lines.extend([
            "",
            "🎯 RENCANA LEVEL",
            f"Entry: {format_price(result['entry_low'])} - "
            f"{format_price(result['entry_high'])}",
            f"Stop Loss: {format_price(result['sl'])}",
            f"TP1: {format_price(result['tp1'])}",
            f"TP2: {format_price(result['tp2'])}",
            f"TP3: {format_price(result['tp3'])}"
        ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Skor adalah jumlah konfirmasi teknikal,",
        "bukan persentase probabilitas profit.",
        "Level merupakan estimasi, bukan jaminan."
    ])

    return "\n".join(lines)


# ============================================================
# CALENDAR & NEWS
# ============================================================

def get_calendar():

    return api_get("/api/calendar")


def get_news():

    return api_get(
        "/api/news/market",
        params={
            "language": "en",
            "country": "US",
            "maxResults": 10
        }
    )


def format_simple_data(data, title):

    if isinstance(data, dict):

        for key in ("data", "events", "news", "result", "items"):

            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        return f"{title}\n\nFormat data tidak dikenali."

    if not data:
        return f"{title}\n\nBelum ada data."

    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━"
    ]

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
                lines.append(f"Waktu: {time}")

            if impact:
                lines.append(f"Dampak: {impact}")

        else:
            lines.append(f"\n• {item}")

    return "\n".join(lines)


# ============================================================
# TELEGRAM MENUS
# ============================================================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton(
                "🥇 XAUUSD",
                callback_data="market_XAUUSD"
            ),
            InlineKeyboardButton(
                "₿ BTCUSD",
                callback_data="market_BTCUSD"
            )
        ],
        [
            InlineKeyboardButton(
                "📅 Kalender Ekonomi",
                callback_data="calendar"
            ),
            InlineKeyboardButton(
                "📰 Berita Market",
                callback_data="news"
            )
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
            "⬅️ Menu Utama",
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
# TELEGRAM COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    log.info("/start diterima")

    await update.message.reply_text(
        "🤖 MASTER SMC SCALPING BOT\n\n"
        "Pilih market untuk memulai analisis.\n"
        "Analisis memakai 100 candle tertutup terakhir.",
        reply_markup=main_menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🟢 BOT ONLINE\n"
        "Telegram polling aktif.\n"
        "Mode: Manual.\n"
        "Candle: 100 candle tertutup.\n"
        "Market: XAUUSD dan BTCUSD."
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Pilih menu:",
        reply_markup=main_menu()
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data

    log.info("Callback: %s", data)

    if data == "menu":

        await query.message.reply_text(
            "Pilih market:",
            reply_markup=main_menu()
        )

        return

    if data.startswith("market_"):

        symbol = data.replace("market_", "")

        if symbol not in MARKETS:
            await query.message.reply_text("Market tidak valid.")
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
            "Tekan tombol untuk analisis.",
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
            "Mengambil 100 candle tertutup."
        )

        try:

            result = await asyncio.to_thread(
                analyze_market,
                symbol,
                timeframe
            )

            await processing.edit_text(
                create_report(result)
            )

            log.info(
                "Analisis selesai: %s %s",
                symbol,
                timeframe
            )

        except Exception as error:

            log.exception("ANALISIS GAGAL")

            await processing.edit_text(
                "❌ ANALISIS GAGAL\n\n"
                f"Market: {symbol}\n"
                f"Timeframe: {timeframe}\n\n"
                f"Detail error:\n{str(error)[:2500]}"
            )

        return

    if data == "calendar":

        message = await query.message.reply_text(
            "⏳ Mengambil kalender ekonomi..."
        )

        try:

            result = await asyncio.to_thread(get_calendar)

            await message.edit_text(
                format_simple_data(
                    result,
                    "📅 KALENDER EKONOMI"
                )[:4000]
            )

        except Exception as error:

            log.exception("CALENDAR ERROR")

            await message.edit_text(
                f"❌ Kalender gagal:\n{str(error)[:2000]}"
            )

        return

    if data == "news":

        message = await query.message.reply_text(
            "⏳ Mengambil berita market..."
        )

        try:

            result = await asyncio.to_thread(get_news)

            await message.edit_text(
                format_simple_data(
                    result,
                    "📰 BERITA MARKET"
                )[:4000]
            )

        except Exception as error:

            log.exception("NEWS ERROR")

            await message.edit_text(
                f"❌ Berita gagal:\n{str(error)[:2000]}"
            )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):

    log.error(
        "Telegram error: %s",
        context.error,
        exc_info=context.error
    )


# ============================================================
# STARTUP
# ============================================================

async def post_init(application):

    log.info("======================================")
    log.info("MASTER SMC SCALPING BOT STARTED")
    log.info("Markets: XAUUSD, BTCUSD")
    log.info("Timeframes: M1 M5 M15 M30 H1 H4")
    log.info("Candles: 100 CLOSED CANDLES")
    log.info("Strategy: FVG + SNR + SND + BOS")
    log.info("Mode: MANUAL")
    log.info("======================================")

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

    log.info("Membangun Telegram application...")

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
        CommandHandler("menu", menu_command)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_error_handler(error_handler)

    log.info("Menjalankan Telegram polling...")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        log.info("Bot dihentikan.")

    except Exception:
        log.exception("FATAL ERROR")
        raise
