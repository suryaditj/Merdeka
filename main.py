# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT V2
# XAUUSD + BTCUSD
# TELEGRAM + BIQUOTE + GITHUB ACTIONS
#
# STRATEGY:
# FVG + SNR + SND + BOS
# MINIMUM 3/4 CONFIRMATIONS
# INDEPENDENT TIMEFRAME
#
# TIMEFRAME:
# M1 M5 M15 M30 H1 H4
#
# DATA:
# BiQuote OHLC
# BiQuote Economic Calendar
# BiQuote Market News
#
# MAXIMUM 20 CLOSED CANDLES FOR ANALYSIS
# MANUAL ANALYSIS ONLY
# NO AUTO ANALYSIS
# ============================================================

import os
import html
import logging
import asyncio
import requests

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
WIB = ZoneInfo("Asia/Jakarta")

MAX_CANDLES = 20
MIN_VOTES = 3
REQUEST_TIMEOUT = 20

TIMEFRAMES = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h"
}

MARKETS = {
    "XAUUSD": "GOLD",
    "BTCUSD": "BITCOIN"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("MASTER_SMC")

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "MasterSMCTradingBot/2.0"
})


# ============================================================
# BIQUOTE REQUEST
# ============================================================

def api_get(endpoint, params=None):

    response = SESSION.get(
        BASE_URL + endpoint,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# CANDLE DATA
# ============================================================

def get_candles(symbol, timeframe):

    interval = TIMEFRAMES.get(timeframe)

    if not interval:
        raise ValueError("Timeframe tidak valid.")

    # Ambil 21 agar tersedia 20 candle tertutup
    # setelah candle berjalan dibuang.

    data = api_get(
        f"/api/{symbol}/ohlc",
        {
            "interval": interval,
            "limit": MAX_CANDLES + 1
        }
    )

    bars = data.get("bars", [])

    if not isinstance(bars, list):
        raise ValueError("Format candle BiQuote tidak valid.")

    candles = []

    for bar in bars:

        if bar.get("isOpen") is True:
            continue

        try:

            candle = {
                "time": bar["openTime"],
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"])
            }

            if candle["high"] < candle["low"]:
                continue

            candles.append(candle)

        except (KeyError, TypeError, ValueError):
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    candles = candles[-MAX_CANDLES:]

    if len(candles) < 10:
        raise ValueError(
            f"Candle tertutup hanya {len(candles)}. "
            "Data belum mencukupi."
        )

    return candles


# ============================================================
# BASIC UTILITIES
# ============================================================

def average_range(candles):

    recent = candles[-10:]

    values = [
        c["high"] - c["low"]
        for c in recent
    ]

    return sum(values) / len(values)


def candle_body(candle):

    return abs(
        candle["close"] - candle["open"]
    )


def find_swings(candles):

    highs = []
    lows = []

    for i in range(1, len(candles) - 1):

        previous = candles[i - 1]
        current = candles[i]
        following = candles[i + 1]

        if (
            current["high"] > previous["high"]
            and current["high"] > following["high"]
        ):
            highs.append({
                "index": i,
                "price": current["high"]
            })

        if (
            current["low"] < previous["low"]
            and current["low"] < following["low"]
        ):
            lows.append({
                "index": i,
                "price": current["low"]
            })

    return highs, lows


def near_level(price, level, tolerance):

    return abs(price - level) <= tolerance


# ============================================================
# 1. FAIR VALUE GAP
# ============================================================

def detect_fvg(candles):

    price = candles[-1]["close"]
    atr = average_range(candles)

    tolerance = max(
        atr * 0.35,
        price * 0.00005
    )

    # Cari gap terbaru pada 3-candle pattern.
    # Gap harus masih relevan dengan harga sekarang.

    for i in range(len(candles) - 1, 1, -1):

        first = candles[i - 2]
        middle = candles[i - 1]
        third = candles[i]

        # Bullish FVG
        if third["low"] > first["high"]:

            zone_low = first["high"]
            zone_high = third["low"]

            if (
                zone_low - tolerance
                <= price
                <= zone_high + tolerance
            ):

                return {
                    "direction": "BUY",
                    "low": zone_low,
                    "high": zone_high,
                    "reason": "Bullish FVG"
                }

        # Bearish FVG
        if third["high"] < first["low"]:

            zone_low = third["high"]
            zone_high = first["low"]

            if (
                zone_low - tolerance
                <= price
                <= zone_high + tolerance
            ):

                return {
                    "direction": "SELL",
                    "low": zone_low,
                    "high": zone_high,
                    "reason": "Bearish FVG"
                }

    return {
        "direction": "NONE",
        "reason": "Tidak ada FVG relevan"
    }


# ============================================================
# 2. SUPPORT & RESISTANCE
# ============================================================

def detect_snr(candles):

    highs, lows = find_swings(candles)

    price = candles[-1]["close"]
    atr = average_range(candles)

    tolerance = max(
        atr * 0.30,
        price * 0.00005
    )

    supports = [
        x["price"]
        for x in lows
        if x["price"] <= price
    ]

    resistances = [
        x["price"]
        for x in highs
        if x["price"] >= price
    ]

    if supports:

        support = max(supports)

        if near_level(price, support, tolerance):

            return {
                "direction": "BUY",
                "level": support,
                "reason": "Dekat support"
            }

    if resistances:

        resistance = min(resistances)

        if near_level(price, resistance, tolerance):

            return {
                "direction": "SELL",
                "level": resistance,
                "reason": "Dekat resistance"
            }

    return {
        "direction": "NONE",
        "reason": "Belum dekat SNR"
    }


# ============================================================
# 3. SUPPLY & DEMAND
# ============================================================

def detect_snd(candles):

    price = candles[-1]["close"]
    atr = average_range(candles)

    tolerance = max(
        atr * 0.35,
        price * 0.00005
    )

    # Cari base candle sebelum impuls.
    # Base dibuat dari candle sebelumnya.

    for i in range(len(candles) - 1, 1, -1):

        impulse = candles[i]
        base = candles[i - 1]

        body = candle_body(impulse)

        if body < atr * 1.15:
            continue

        zone_low = base["low"]
        zone_high = base["high"]

        # Bullish impulse = demand

        if impulse["close"] > impulse["open"]:

            if base["close"] < base["open"]:

                if (
                    zone_low - tolerance
                    <= price
                    <= zone_high + tolerance
                ):

                    return {
                        "direction": "BUY",
                        "low": zone_low,
                        "high": zone_high,
                        "reason": "Demand zone"
                    }

        # Bearish impulse = supply

        if impulse["close"] < impulse["open"]:

            if base["close"] > base["open"]:

                if (
                    zone_low - tolerance
                    <= price
                    <= zone_high + tolerance
                ):

                    return {
                        "direction": "SELL",
                        "low": zone_low,
                        "high": zone_high,
                        "reason": "Supply zone"
                    }

    return {
        "direction": "NONE",
        "reason": "Tidak ada zona relevan"
    }


# ============================================================
# 4. BREAK OF STRUCTURE
# ============================================================

def detect_bos(candles):

    # Gunakan candle terakhir sebagai candle breakout.
    # Swing pembanding hanya berasal dari candle sebelumnya.

    previous_candles = candles[:-1]

    highs, lows = find_swings(previous_candles)

    if not highs or not lows:

        return {
            "direction": "NONE",
            "reason": "Struktur swing belum cukup"
        }

    last = candles[-1]

    previous_high = highs[-1]["price"]
    previous_low = lows[-1]["price"]

    # Break harus berdasarkan CLOSE candle.

    if last["close"] > previous_high:

        return {
            "direction": "BUY",
            "level": previous_high,
            "reason": "Bullish BOS"
        }

    if last["close"] < previous_low:

        return {
            "direction": "SELL",
            "level": previous_low,
            "reason": "Bearish BOS"
        }

    return {
        "direction": "NONE",
        "reason": "Belum terjadi BOS"
    }


# ============================================================
# TRADING ANALYSIS ENGINE
# ============================================================

def analyze_market(symbol, timeframe):

    candles = get_candles(
        symbol,
        timeframe
    )

    price = candles[-1]["close"]
    atr = average_range(candles)

    methods = {
        "FVG": detect_fvg(candles),
        "SNR": detect_snr(candles),
        "SND": detect_snd(candles),
        "BOS": detect_bos(candles)
    }

    buy_count = sum(
        1 for x in methods.values()
        if x["direction"] == "BUY"
    )

    sell_count = sum(
        1 for x in methods.values()
        if x["direction"] == "SELL"
    )

    signal = "WAIT"

    if buy_count >= MIN_VOTES and buy_count > sell_count:

        signal = "BUY"

    elif sell_count >= MIN_VOTES and sell_count > buy_count:

        signal = "SELL"

    entry = None
    sl = None
    tp = None
    risk_reward = None

    highs, lows = find_swings(candles)

    # BUY PLAN

    if signal == "BUY":

        relevant_lows = [
            x["price"]
            for x in lows
            if x["price"] < price
        ]

        if relevant_lows:

            structural_sl = min(
                relevant_lows[-3:]
            )

            sl = structural_sl - atr * 0.15

        else:

            sl = price - atr * 1.5

        entry = price

        risk = entry - sl

        if risk > 0:

            tp = entry + risk * 1.8
            risk_reward = 1.8

    # SELL PLAN

    elif signal == "SELL":

        relevant_highs = [
            x["price"]
            for x in highs
            if x["price"] > price
        ]

        if relevant_highs:

            structural_sl = max(
                relevant_highs[-3:]
            )

            sl = structural_sl + atr * 0.15

        else:

            sl = price + atr * 1.5

        entry = price

        risk = sl - entry

        if risk > 0:

            tp = entry - risk * 1.8
            risk_reward = 1.8

    # Validasi trading plan

    if signal == "BUY":

        if (
            sl is None
            or tp is None
            or sl >= entry
            or tp <= entry
        ):

            signal = "WAIT"
            entry = sl = tp = risk_reward = None

    elif signal == "SELL":

        if (
            sl is None
            or tp is None
            or sl <= entry
            or tp >= entry
        ):

            signal = "WAIT"
            entry = sl = tp = risk_reward = None

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "signal": signal,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "methods": methods,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": risk_reward,
        "candles": len(candles),
        "candle_time": candles[-1]["time"]
    }


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt(value):

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.3f}"

    return f"{value:.6f}"


# ============================================================
# ANALYSIS REPORT
# ============================================================

def create_report(result):

    signal = result["signal"]

    icons = {
        "BUY": "🟢",
        "SELL": "🔴",
        "WAIT": "🟡"
    }

    lines = [
        f"📊 <b>{result['symbol']} | {result['timeframe']}</b>",
        "",
        f"{icons[signal]} <b>{signal}</b>",
        f"💰 Price: <code>{fmt(result['price'])}</code>",
        "",
        "<b>TECHNICAL CHECK</b>"
    ]

    for name, data in result["methods"].items():

        direction = data["direction"]

        mark = {
            "BUY": "🟢",
            "SELL": "🔴",
            "NONE": "⚪"
        }.get(direction, "⚪")

        lines.append(
            f"{mark} {name}: {direction}"
        )

    lines.extend([
        "",
        f"BUY: {result['buy_count']}/4",
        f"SELL: {result['sell_count']}/4"
    ])

    if signal in ("BUY", "SELL"):

        lines.extend([
            "",
            "<b>TRADING PLAN</b>",
            f"Entry: <code>{fmt(result['entry'])}</code>",
            f"SL: <code>{fmt(result['sl'])}</code>",
            f"TP: <code>{fmt(result['tp'])}</code>",
            f"Risk/Reward: 1:{result['rr']}"
        ])

    else:

        lines.extend([
            "",
            "⏳ Belum memenuhi 3 konfirmasi searah."
        ])

    lines.extend([
        "",
        f"🕯 Candle: {result['candles']} closed",
        "📌 Analisis TF ini independen.",
        "⚠️ Bukan jaminan profit."
    ])

    return "\n".join(lines)


# ============================================================
# ECONOMIC CALENDAR - BIQUOTE
# ============================================================

def get_us_calendar():

    data = api_get(
        "/api/calendar/upcoming",
        {
            "countries": "US",
            "importance": "high",
            "limit": 15
        }
    )

    if not isinstance(data, list):
        raise ValueError("Format kalender BiQuote tidak valid.")

    return data


def event_time(value):

    try:

        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        return dt.astimezone(WIB).strftime(
            "%d/%m/%Y %H:%M WIB"
        )

    except Exception:

        return "Waktu tidak tersedia"


def event_impact(name):

    name = name.lower()

    gold_sensitive = [
        "cpi",
        "inflation",
        "pce",
        "fomc",
        "interest rate",
        "federal funds",
        "nonfarm",
        "payroll",
        "unemployment",
        "powell",
        "federal reserve",
        "gdp"
    ]

    if any(x in name for x in gold_sensitive):

        return "TINGGI"

    return "PENTING"


def create_calendar_report():

    events = get_us_calendar()

    if not events:

        return (
            "🇺🇸 <b>US ECONOMIC CALENDAR</b>\n\n"
            "Tidak ada event high impact mendatang."
        )

    now = datetime.now(timezone.utc)

    lines = [
        "🇺🇸 <b>US ECONOMIC CALENDAR</b>",
        "<i>Waktu Indonesia Barat</i>",
        ""
    ]

    count = 0

    for event in events:

        if event.get("countryCode") != "US":
            continue

        name = html.escape(
            str(event.get("name", "Unknown"))
        )

        raw_time = event.get("time", "")

        try:

            dt = datetime.fromisoformat(
                raw_time.replace("Z", "+00:00")
            )

            minutes = int(
                (dt - now).total_seconds() / 60
            )

            if minutes < 0:
                timing = "Sudah lewat"

            elif minutes <= 60:
                timing = f"⚠️ Dalam {minutes} menit"

            else:
                timing = ""

        except Exception:

            timing = ""

        importance = str(
            event.get("importance", "")
        ).upper()

        actual = event.get("actual")
        forecast = event.get("forecast")
        previous = event.get("previous")

        lines.append(f"🔴 <b>{name}</b>")
        lines.append(f"🕒 {event_time(raw_time)}")

        if timing:
            lines.append(timing)

        lines.append(
            f"Impact: {event_impact(name)}"
        )

        if actual is not None:
            lines.append(f"Actual: {actual}")

        if forecast is not None:
            lines.append(f"Forecast: {forecast}")

        if previous is not None:
            lines.append(f"Previous: {previous}")

        if actual is not None and forecast is not None:

            lines.append(
                "📊 Data aktual sudah dirilis. "
                "Bandingkan actual dengan forecast."
            )

        lines.append("")

        count += 1

        if count >= 10:
            break

    if count == 0:

        lines.append(
            "Tidak ada event US yang ditemukan."
        )

    lines.extend([
        "⚠️ Berita ekonomi dapat meningkatkan volatilitas.",
        "Dampak bullish/bearish bergantung pada hasil dan ekspektasi pasar."
    ])

    return "\n".join(lines)


# ============================================================
# MARKET NEWS - BIQUOTE
# ============================================================

def get_market_news(symbol):

    data = api_get(
        "/api/news/market",
        {
            "symbol": symbol,
            "language": "en",
            "country": "US",
            "maxResults": 8
        }
    )

    if not isinstance(data, list):
        raise ValueError("Format berita BiQuote tidak valid.")

    return data


def create_news_report(symbol):

    articles = get_market_news(symbol)

    lines = [
        f"📰 <b>MARKET NEWS | {symbol}</b>",
        ""
    ]

    if not articles:

        lines.append("Belum ada berita tersedia.")

        return "\n".join(lines)

    for article in articles[:7]:

        title = html.escape(
            str(article.get("title", "Tanpa judul"))
        )

        publisher = html.escape(
            str(article.get("publisher", "Unknown"))
        )

        published = article.get(
            "publishedDate",
            ""
        )

        url = str(
            article.get("url", "")
        )

        lines.append(f"• <b>{title}</b>")
        lines.append(f"Source: {publisher}")

        if published:
            lines.append(
                f"Date: {event_time(published)}"
            )

        if url.startswith(("https://", "http://")):

            safe_url = html.escape(
                url,
                quote=True
            )

            lines.append(
                f'<a href="{safe_url}">Baca berita</a>'
            )

        lines.append("")

    lines.append(
        "ℹ️ Berita bukan sinyal entry otomatis."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM INLINE KEYBOARD
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🥇 XAUUSD",
                callback_data="market:XAUUSD"
            ),
            InlineKeyboardButton(
                "₿ BTCUSD",
                callback_data="market:BTCUSD"
            )
        ],

        [
            InlineKeyboardButton(
                "🇺🇸 US ECONOMIC",
                callback_data="calendar"
            )
        ],

        [
            InlineKeyboardButton(
                "📰 GOLD NEWS",
                callback_data="news:XAUUSD"
            ),
            InlineKeyboardButton(
                "📰 BTC NEWS",
                callback_data="news:BTCUSD"
            )
        ]

    ])


def timeframe_keyboard(symbol):

    buttons = []

    row = []

    for tf in TIMEFRAMES:

        row.append(
            InlineKeyboardButton(
                tf,
                callback_data=f"tf:{symbol}:{tf}"
            )
        )

        if len(row) == 3:

            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            "⬅️ MENU UTAMA",
            callback_data="menu"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def analysis_keyboard(symbol, timeframe):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🔍 ANALISA SEKARANG",
                callback_data=f"analyze:{symbol}:{timeframe}"
            )
        ],

        [
            InlineKeyboardButton(
                "🔄 GANTI TF",
                callback_data=f"market:{symbol}"
            ),
            InlineKeyboardButton(
                "🏠 MENU",
                callback_data="menu"
            )
        ],

        [
            InlineKeyboardButton(
                "🇺🇸 US ECONOMIC",
                callback_data="calendar"
            )
        ]

    ])


def back_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🏠 MENU UTAMA",
                callback_data="menu"
            )
        ]

    ])


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🤖 <b>MASTER SMC SCALPING BOT V2</b>\n\n"
        "Pilih market untuk memulai.\n\n"
        "📊 FVG + SNR + SND + BOS\n"
        "🎯 Minimum 3/4 konfirmasi\n"
        "⚡ Independent timeframe\n"
        "🕯 Maksimal 20 candle tertutup\n"
        "🇺🇸 US Economic Calendar\n"
        "📰 Market News\n\n"
        "Analisis hanya berjalan saat tombol ditekan."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🤖 <b>MASTER SMC SCALPING BOT V2</b>\nPilih menu:",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data

    try:

        # MAIN MENU

        if data == "menu":

            await query.message.reply_text(
                "🤖 <b>MASTER SMC SCALPING BOT V2</b>\nPilih menu:",
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )

            return

        # MARKET

        if data.startswith("market:"):

            symbol = data.split(":")[1]

            if symbol not in MARKETS:
                return

            await query.message.reply_text(
                f"📊 <b>{symbol}</b>\n\nPilih timeframe:",
                parse_mode="HTML",
                reply_markup=timeframe_keyboard(symbol)
            )

            return

        # TIMEFRAME

        if data.startswith("tf:"):

            _, symbol, timeframe = data.split(":")

            if (
                symbol not in MARKETS
                or timeframe not in TIMEFRAMES
            ):
                return

            await query.message.reply_text(
                f"📊 <b>{symbol} | {timeframe}</b>\n\n"
                "Tekan tombol ANALISA SEKARANG.",
                parse_mode="HTML",
                reply_markup=analysis_keyboard(
                    symbol,
                    timeframe
                )
            )

            return

        # ANALYSIS

        if data.startswith("analyze:"):

            _, symbol, timeframe = data.split(":")

            if (
                symbol not in MARKETS
                or timeframe not in TIMEFRAMES
            ):
                return

            status = await query.message.reply_text(
                f"⏳ Mengambil data {symbol} {timeframe}..."
            )

            result = await asyncio.to_thread(
                analyze_market,
                symbol,
                timeframe
            )

            report = create_report(result)

            await status.edit_text(
                "✅ Analisis selesai."
            )

            await query.message.reply_text(
                report,
                parse_mode="HTML",
                reply_markup=analysis_keyboard(
                    symbol,
                    timeframe
                )
            )

            return

        # ECONOMIC CALENDAR

        if data == "calendar":

            status = await query.message.reply_text(
                "⏳ Mengambil kalender ekonomi US..."
            )

            report = await asyncio.to_thread(
                create_calendar_report
            )

            await status.edit_text(
                "✅ Kalender ekonomi diperbarui."
            )

            await query.message.reply_text(
                report,
                parse_mode="HTML",
                reply_markup=back_keyboard()
            )

            return

        # MARKET NEWS

        if data.startswith("news:"):

            symbol = data.split(":")[1]

            if symbol not in MARKETS:
                return

            status = await query.message.reply_text(
                "⏳ Mengambil berita pasar..."
            )

            report = await asyncio.to_thread(
                create_news_report,
                symbol
            )

            await status.edit_text(
                "✅ Berita berhasil diperbarui."
            )

            await query.message.reply_text(
                report,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=back_keyboard()
            )

            return

    except requests.exceptions.Timeout:

        await query.message.reply_text(
            "❌ BiQuote timeout. Coba beberapa saat lagi."
        )

    except requests.exceptions.HTTPError as error:

        log.exception("BiQuote HTTP error")

        await query.message.reply_text(
            "❌ BiQuote mengembalikan HTTP error.\n"
            f"<code>{html.escape(str(error))[:500]}</code>",
            parse_mode="HTML"
        )

    except Exception as error:

        log.exception("Callback error")

        await query.message.reply_text(
            "❌ ANALISIS GAGAL\n"
            f"<code>{html.escape(str(error))[:700]}</code>",
            parse_mode="HTML"
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):

    log.error(
        "Telegram error: %s",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN belum diatur di GitHub Secrets."
        )

    log.info("MASTER SMC SCALPING BOT V2 STARTED")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("menu", menu_command)
    )

    application.add_handler(
        CallbackQueryHandler(buttons)
    )

    application.add_error_handler(
        error_handler
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":

    main()
