# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT
# XAUUSD + BTCUSD
# TELEGRAM + BIQUOTE + GITHUB ACTIONS
#
# 100 CANDLE TERAKHIR YANG SUDAH TERTUTUP
# FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD
# MANUAL ANALYSIS ONLY
#
# TIMEFRAMES: M1 M5 M15 M30 H1 H4
# ============================================================

import os
import re
import sys
import html
import logging
import asyncio
import requests

from datetime import datetime, timezone, timedelta

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

WIB = timezone(timedelta(hours=7))

BULAN_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "Mei", 6: "Jun", 7: "Jul", 8: "Agu",
    9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"
}

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
    "User-Agent": "Mozilla/5.0 SMC-Bot/2.2",
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
# TRANSLATION & TIME HELPERS
# ============================================================

def translate_texts_to_id(texts):
    """
    Menerjemahkan list teks Inggris ke Bahasa Indonesia sekaligus
    menggunakan endpoint publik tanpa perlu library tambahan.
    """
    if not texts:
        return []

    clean_inputs = [
        re.sub(r"\s+", " ", str(t or "")).strip()
        for t in texts
    ]

    if not any(clean_inputs):
        return clean_inputs

    separator = "\n|||\n"
    joined_text = separator.join(clean_inputs)

    try:
        resp = SESSION.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "id",
                "dt": "t",
                "q": joined_text
            },
            timeout=8
        )

        if resp.status_code == 200:
            data = resp.json()
            translated_full = "".join(
                part[0] for part in data[0] if part and part[0]
            )
            parts = [p.strip() for p in translated_full.split("|||")]

            if len(parts) == len(clean_inputs):
                return [
                    parts[i] if parts[i] else clean_inputs[i]
                    for i in range(len(clean_inputs))
                ]

    except Exception as err:
        log.warning("Translasi dilewati (fallback ke teks asli): %s", err)

    return clean_inputs


def parse_event_dt_wib(value):
    """
    Mengubah string/timestamp waktu API menjadi objek datetime WIB.
    """
    if value is None or str(value).strip() == "":
        return None

    raw_str = str(value).strip()

    try:
        if isinstance(value, (int, float)) or raw_str.replace(".", "", 1).isdigit():
            num = float(raw_str)
            if num > 100000000000:
                num /= 1000.0
            return datetime.fromtimestamp(num, tz=timezone.utc).astimezone(WIB)

        iso_str = raw_str[:-1] + "+00:00" if raw_str.endswith("Z") else raw_str
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WIB)
    except Exception:
        return None


def format_wib_time(value):
    """
    Mengonversi timestamp/ISO string ke format waktu WIB yang rapi.
    """
    dt_wib = parse_event_dt_wib(value)
    if dt_wib is not None:
        bulan = BULAN_ID.get(dt_wib.month, dt_wib.strftime("%b"))
        return f"{dt_wib.day:02d} {bulan} {dt_wib.year} | {dt_wib.strftime('%H:%M')} WIB"

    if value is None or str(value).strip() == "":
        return "Waktu belum tersedia"

    return str(value).strip()


def parse_numeric_metric(val):
    """
    Ekstrak nilai angka dari string seperti '3.4%', '250K', '-0.2%', '1.5M'.
    """
    if val is None:
        return None

    text = str(val).strip().upper().replace(",", "")
    if not text or text in ("-", "N/A", "NONE", "NULL", "TBD"):
        return None

    multiplier = 1.0
    if text.endswith("K"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith("B"):
        multiplier = 1_000_000_000.0
        text = text[:-1]

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None

    try:
        return float(match.group(0)) * multiplier
    except ValueError:
        return None


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

    number = float(value)

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

    candles.sort(key=lambda candle: candle["time"])

    unique = {}

    for candle in candles:
        unique[candle["time"]] = candle

    candles = sorted(
        unique.values(),
        key=lambda candle: candle["time"]
    )

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
# SMC: FVG (FAIR VALUE GAP)
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
# SMC: BOS (BREAK OF STRUCTURE)
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
# SMC: SUPPORT & RESISTANCE (SNR)
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
# SMC: SBR & RBS (SUPPORT BECOME RESISTANCE / RESISTANCE BECOME SUPPORT)
# ============================================================

def detect_sbr_rbs(candles):
    """
    Mendeteksi area Role Reversal (Flip Zone):
    - RBS (BUY): Swing Resistance ditembus ke atas, lalu harga retest di area tersebut.
    - SBR (SELL): Swing Support ditembus ke bawah, lalu harga retest di area tersebut.
    """
    recent = candles[-1]
    price = recent["close"]

    lookback = candles[-60:-1]
    if len(lookback) < 10:
        return "NEUTRAL", "-", None

    avg_range = sum(c["high"] - c["low"] for c in lookback) / len(lookback)
    if avg_range <= 0:
        avg_range = price * 0.001

    threshold = avg_range * 1.2

    swing_highs = []
    swing_lows = []

    for i in range(2, len(lookback) - 2):
        h = lookback[i]["high"]
        l = lookback[i]["low"]

        if (
            h >= lookback[i - 1]["high"]
            and h >= lookback[i - 2]["high"]
            and h > lookback[i + 1]["high"]
            and h > lookback[i + 2]["high"]
        ):
            swing_highs.append((i, h))

        if (
            l <= lookback[i - 1]["low"]
            and l <= lookback[i - 2]["low"]
            and l < lookback[i + 1]["low"]
            and l < lookback[i + 2]["low"]
        ):
            swing_lows.append((i, l))

    # Cek RBS (Resistance ditembus ke atas -> jadi Support)
    for idx, res_level in reversed(swing_highs):
        broken_up = any(
            lookback[k]["close"] > res_level
            for k in range(idx + 2, len(lookback))
        )
        if broken_up:
            if (
                abs(price - res_level) <= threshold
                and price >= res_level - (threshold * 0.5)
            ):
                return "BUY", "RBS", res_level

    # Cek SBR (Support ditembus ke bawah -> jadi Resistance)
    for idx, sup_level in reversed(swing_lows):
        broken_down = any(
            lookback[k]["close"] < sup_level
            for k in range(idx + 2, len(lookback))
        )
        if broken_down:
            if (
                abs(price - sup_level) <= threshold
                and price <= sup_level + (threshold * 0.5)
            ):
                return "SELL", "SBR", sup_level

    return "NEUTRAL", "-", None


# ============================================================
# SMC: SUPPLY & DEMAND (SND)
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
# SMC: RBR & DBD (RALLY-BASE-RALLY / DROP-BASE-DROP)
# ============================================================

def detect_rbr_dbd(candles):
    """
    Mendeteksi zona Supply & Demand Continuation:
    - RBR (BUY): Rally (Impulsif Naik) -> Base (Konsolidasi) -> Rally (Breakout Naik)
    - DBD (SELL): Drop (Impulsif Turun) -> Base (Konsolidasi) -> Drop (Breakdown Turun)
    """
    recent = candles[-1]
    price = recent["close"]

    lookback = candles[-50:-1]
    if len(lookback) < 8:
        return "NEUTRAL", "-", None, None

    avg_range = sum(c["high"] - c["low"] for c in lookback) / len(lookback)
    if avg_range <= 0:
        avg_range = price * 0.001

    buffer = avg_range * 0.65

    def is_strong_bull(c):
        rng = c["high"] - c["low"]
        body = c["close"] - c["open"]
        return rng > 0 and body > 0 and (body / rng) >= 0.55

    def is_strong_bear(c):
        rng = c["high"] - c["low"]
        body = c["open"] - c["close"]
        return rng > 0 and body > 0 and (body / rng) >= 0.55

    def is_base_candle(c):
        rng = c["high"] - c["low"]
        if rng <= 0:
            return True
        body = abs(c["close"] - c["open"])
        return (body / rng) <= 0.50

    for i in range(len(lookback) - 4, -1, -1):
        leg1 = lookback[i]

        for base_len in (1, 2):
            leg2_idx = i + base_len + 1
            if leg2_idx >= len(lookback):
                continue

            base_candles = lookback[i + 1: leg2_idx]
            leg2 = lookback[leg2_idx]

            if not all(is_base_candle(bc) for bc in base_candles):
                continue

            zone_low = min(bc["low"] for bc in base_candles)
            zone_high = max(bc["high"] for bc in base_candles)

            # Cek pola RBR (Rally - Base - Rally) -> BUY
            if (
                is_strong_bull(leg1)
                and is_strong_bull(leg2)
                and leg2["close"] > zone_high
            ):
                invalidated = any(
                    lookback[k]["close"] < zone_low - buffer
                    for k in range(leg2_idx + 1, len(lookback))
                )
                if not invalidated and (zone_low - buffer <= price <= zone_high + buffer):
                    return "BUY", "RBR", zone_low, zone_high

            # Cek pola DBD (Drop - Base - Drop) -> SELL
            if (
                is_strong_bear(leg1)
                and is_strong_bear(leg2)
                and leg2["close"] < zone_low
            ):
                invalidated = any(
                    lookback[k]["close"] > zone_high + buffer
                    for k in range(leg2_idx + 1, len(lookback))
                )
                if not invalidated and (zone_low - buffer <= price <= zone_high + buffer):
                    return "SELL", "DBD", zone_low, zone_high

    return "NEUTRAL", "-", None, None


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
    sbr_rbs, sbr_rbs_type, sbr_rbs_level = detect_sbr_rbs(candles)
    snd, snd_level = detect_snd(candles)
    rbr_dbd, rbr_dbd_type, rbr_dbd_low, rbr_dbd_high = detect_rbr_dbd(candles)

    signals = {
        "FVG": fvg,
        "BOS": bos,
        "SNR": snr,
        "SBR_RBS": sbr_rbs,
        "SND": snd,
        "RBR_DBD": rbr_dbd
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

    elif confirmations in (3, 4):
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
        "total_indicators": len(signals),
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
        "sbr_rbs_type": sbr_rbs_type,
        "sbr_rbs_level": sbr_rbs_level,
        "snd_level": snd_level,
        "rbr_dbd_type": rbr_dbd_type,
        "rbr_dbd_low": rbr_dbd_low,
        "rbr_dbd_high": rbr_dbd_high
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

    sbr_rbs_sig = result["signals"]["SBR_RBS"]
    if sbr_rbs_sig != "NEUTRAL" and result["sbr_rbs_level"] is not None:
        sbr_rbs_text = (
            f"{sbr_rbs_sig} ({result['sbr_rbs_type']} @ "
            f"{format_price(result['sbr_rbs_level'])})"
        )
    else:
        sbr_rbs_text = sbr_rbs_sig

    rbr_dbd_sig = result["signals"]["RBR_DBD"]
    if (
        rbr_dbd_sig != "NEUTRAL"
        and result["rbr_dbd_low"] is not None
        and result["rbr_dbd_high"] is not None
    ):
        rbr_dbd_text = (
            f"{rbr_dbd_sig} ({result['rbr_dbd_type']} @ "
            f"{format_price(result['rbr_dbd_low'])} - "
            f"{format_price(result['rbr_dbd_high'])})"
        )
    else:
        rbr_dbd_text = rbr_dbd_sig

    lines = [
        "📊 ANALISA SMC SCALPING PRO",
        "━━━━━━━━━━━━━━━━━━",
        f"Market: {result['symbol']}",
        f"Timeframe: {result['timeframe']}",
        f"Harga: {format_price(result['price'])}",
        f"Candle dianalisis: {result['candle_count']}/100",
        "",
        f"{emoji} ARAH: {direction}",
        f"Status: {result['status']}",
        f"Konfirmasi: {result['confirmations']}/{result['total_indicators']}",
        "",
        "📌 KONFIRMASI SMC & PRICE ACTION",
        f"FVG: {result['signals']['FVG']}",
        f"BOS: {result['signals']['BOS']}",
        f"SNR: {result['signals']['SNR']}",
        f"SBR/RBS: {sbr_rbs_text}",
        f"SND: {result['signals']['SND']}",
        f"RBR/DBD: {rbr_dbd_text}"
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
# CALENDAR & NEWS (US FOCUS + XAUUSD IMPACT + BAHASA AWAM + %)
# ============================================================

def get_calendar():

    return api_get("/api/calendar")


def get_news():

    return api_get(
        "/api/news/market",
        params={
            "language": "en",
            "country": "US",
            "maxResults": 12
        }
    )


def _extract_list_payload(data):

    if isinstance(data, dict):
        for key in ("data", "events", "news", "result", "items", "articles"):
            if isinstance(data.get(key), list):
                return data[key]

    if isinstance(data, list):
        return data

    return None


def _impact_rank_and_badge(raw_impact, title_en):

    imp = str(raw_impact or "").strip().lower()
    t = title_en.lower()

    high_keywords = (
        "cpi", "non-farm", "nonfarm", "nfp", "fomc", "interest rate",
        "fed ", "powell", "ppi", "pce", "gdp", "retail sales",
        "unemployment", "jobless claims", "ism manufacturing", "ism services"
    )

    if imp in ("high", "3", "red", "tinggi") or any(k in t for k in high_keywords):
        return 3, "🔴 DAMPAK BESAR (HIGH)"

    if imp in ("medium", "moderate", "2", "orange", "yellow", "sedang"):
        return 2, "🟡 DAMPAK SEDANG (MEDIUM)"

    return 1, "🟢 DAMPAK KECIL (LOW)"


def analyze_xau_event_impact(title_en, actual_raw, forecast_raw, impact_rank=2):
    """
    Menjelaskan dampak data ekonomi US terhadap harga Emas (XAUUSD)
    dengan bahasa awam yang mudah dipahami beserta angka persentase peluangnya.
    """
    t = title_en.lower()

    # Persentase kekuatan pengaruh berita terhadap arah Emas
    pct_by_rank = {
        3: "80%",
        2: "65%",
        1: "55%"
    }
    peluang_pct = pct_by_rank.get(impact_rank, "65%")

    # Indikator yang sifatnya terbalik (jika angka naik = Dolar melemah = Emas naik)
    inverse_usd = any(
        k in t for k in ("unemployment", "jobless claims", "layoff")
    )

    # Penjelasan sederhana tentang apa data ekonomi ini
    if any(k in t for k in ("cpi", "ppi", "pce", "inflation")):
        driver = "Mengukur kenaikan harga barang di AS (sangat memengaruhi suku bunga)."
    elif any(k in t for k in ("nfp", "non-farm", "nonfarm", "payroll", "employment", "jobless", "adp")):
        driver = "Mengukur kondisi lapangan kerja di AS (penggerak cepat harga emas)."
    elif any(k in t for k in ("fomc", "interest rate", "powell", "fed")):
        driver = "Keputusan suku bunga bank sentral AS (kunci utama naik-turunnya emas)."
    elif any(k in t for k in ("gdp", "retail sales", "pmi")):
        driver = "Mengukur kekuatan ekonomi dan daya belanja masyarakat AS."
    else:
        driver = "Laporan ekonomi Dolar AS (biasanya bergerak berlawanan dengan Emas)."

    actual_num = parse_numeric_metric(actual_raw)
    forecast_num = parse_numeric_metric(forecast_raw)

    # 1. Jika hasil asli sudah keluar
    if actual_num is not None and forecast_num is not None:
        if actual_num == forecast_num:
            return (
                f"{driver}\n"
                "⚖️ <b>Efek ke Emas:</b> Hasil sama persis dengan perkiraan "
                "(Harga cenderung datar / peluang gerak <b>50%</b>)."
            )

        is_higher = actual_num > forecast_num

        if inverse_usd:
            if is_higher:
                return (
                    f"{driver}\n"
                    f"🟢 <b>Efek ke Emas:</b> Pengangguran lebih tinggi dari perkiraan (Dolar melemah) "
                    f"➔ Harga Emas berpotensi <b>NAIK 📈 (Peluang {peluang_pct})</b>."
                )
            return (
                f"{driver}\n"
                f"🔴 <b>Efek ke Emas:</b> Pengangguran lebih rendah dari perkiraan (Dolar menguat) "
                f"➔ Harga Emas berpotensi <b>TURUN 📉 (Peluang {peluang_pct})</b>."
            )
        else:
            if is_higher:
                return (
                    f"{driver}\n"
                    f"🔴 <b>Efek ke Emas:</b> Hasil lebih tinggi dari perkiraan (Dolar menguat) "
                    f"➔ Harga Emas berpotensi <b>TURUN 📉 (Peluang {peluang_pct})</b>."
                )
            return (
                f"{driver}\n"
                f"🟢 <b>Efek ke Emas:</b> Hasil lebih rendah dari perkiraan (Dolar melemah) "
                f"➔ Harga Emas berpotensi <b>NAIK 📈 (Peluang {peluang_pct})</b>."
            )

    # 2. Jika hasil belum keluar (Panduan sebelum berita rilis)
    if inverse_usd:
        return (
            f"{driver}\n"
            f"💡 <b>Panduan Emas (XAUUSD):</b>\n"
            f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas berpotensi <b>NAIK 📈 (Peluang {peluang_pct})</b>\n"
            f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas berpotensi <b>TURUN 📉 (Peluang {peluang_pct})</b>"
        )

    return (
        f"{driver}\n"
        f"💡 <b>Panduan Emas (XAUUSD):</b>\n"
        f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas berpotensi <b>TURUN 📉 (Peluang {peluang_pct})</b>\n"
        f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas berpotensi <b>NAIK 📈 (Peluang {peluang_pct})</b>"
    )


def format_calendar_data(data):

    items = _extract_list_payload(data)

    if items is None:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nFormat data tidak dikenali."

    if not items:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nBelum ada jadwal rilis data."

    # 1. Filter khusus US / USD
    us_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        country = str(
            item.get("country")
            or item.get("currency")
            or item.get("ccy")
            or item.get("code")
            or ""
        ).strip().upper()

        if country in ("US", "USA", "USD", "UNITED STATES", "UNITED STATES OF AMERICA"):
            us_items.append(item)

    filtered = us_items if us_items else [i for i in items if isinstance(i, dict)]

    if not filtered:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nTidak ada jadwal ekonomi US ditemukan."

    now_wib = datetime.now(WIB)
    today_start_wib = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)

    upcoming_or_today = []
    all_parsed = []

    for item in filtered:
        title_en = str(
            item.get("title")
            or item.get("event")
            or item.get("name")
            or item.get("indicator")
            or "Rilis Ekonomi US"
        ).strip()

        raw_impact = (
            item.get("impact")
            or item.get("importance")
            or item.get("volatility")
            or ""
        )

        raw_time = (
            item.get("time")
            or item.get("date")
            or item.get("datetime")
            or item.get("releaseTime")
        )

        dt_wib = parse_event_dt_wib(raw_time)
        rank, badge = _impact_rank_and_badge(raw_impact, title_en)
        entry = (dt_wib, rank, badge, title_en, item)

        all_parsed.append(entry)

        # Hanya ambil jadwal mulai hari ini (00:00 WIB) ke depan (buang tanggal kemarin)
        if dt_wib is None or dt_wib >= today_start_wib:
            upcoming_or_today.append(entry)

    active_list = upcoming_or_today if upcoming_or_today else all_parsed

    # Urutkan: Tanggal terdekat dulu -> Dampak Besar di atas -> Jam rilis terawal
    active_list.sort(
        key=lambda x: (
            x[0].date() if x[0] else datetime.max.date(),
            -x[1],
            x[0] if x[0] else datetime.max.replace(tzinfo=WIB)
        )
    )

    top_events = active_list[:7]

    titles_en = [entry[3] for entry in top_events]
    titles_id = translate_texts_to_id(titles_en)

    bulan_sekarang = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    tgl_hari_ini = f"{now_wib.day:02d} {bulan_sekarang} {now_wib.year}"

    header = (
        f"<b>📅 JADWAL EKONOMI US (PANDUAN EMAS)</b>\n"
        f"<i>Mulai {tgl_hari_ini} (Jam WIB) • Bahasa Sederhana</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>⚠️ Hindari masuk posisi 15 menit sebelum &amp; sesudah berita Dampak Besar karena harga emas bergejolak cepat.</i>"
    )

    body_blocks = []
    current_len = len(header) + len(footer)

    for idx, (dt_wib, rank, badge, title_en, item) in enumerate(top_events):
        title_id = titles_id[idx]

        if title_id.lower() != title_en.lower():
            display_title = f"{html.escape(title_id)} <i>({html.escape(title_en)})</i>"
        else:
            display_title = html.escape(title_en)

        raw_time = (
            item.get("time")
            or item.get("date")
            or item.get("datetime")
            or item.get("releaseTime")
        )
        time_wib = html.escape(format_wib_time(raw_time))

        is_today_tag = " <b>[HARI INI]</b>" if (dt_wib and dt_wib.date() == now_wib.date()) else ""

        actual = item.get("actual")
        forecast = (
            item.get("forecast")
            or item.get("consensus")
            or item.get("estimate")
        )
        previous = (
            item.get("previous")
            or item.get("prior")
        )

        act_str = html.escape(str(actual)) if actual not in (None, "") else "⏳ Belum keluar"
        fc_str = html.escape(str(forecast)) if forecast not in (None, "") else "-"
        prev_str = html.escape(str(previous)) if previous not in (None, "") else "-"

        xau_note = analyze_xau_event_impact(title_en, actual, forecast, rank)

        block = (
            f"\n<b>{idx + 1}. {display_title}</b>\n"
            f"🇺🇸 USD | {badge}{is_today_tag}\n"
            f"🕒 <code>{time_wib}</code>\n"
            f"📊 Hasil Asli: <b>{act_str}</b> | Perkiraan: <b>{fc_str}</b> | Lalu: <b>{prev_str}</b>\n"
            f"<blockquote>{xau_note}</blockquote>\n"
        )

        if current_len + len(block) > 3850:
            break

        body_blocks.append(block)
        current_len += len(block)

    return header + "".join(body_blocks) + footer


def analyze_xau_news_sentiment(text_en):
    """
    Mendeteksi pengaruh berita terhadap Emas (XAUUSD) dengan bahasa awam + angka %.
    """
    t = text_en.lower()

    xau_keywords = (
        "gold", "xau", "bullion", "precious metal", "fed", "powell",
        "fomc", "inflation", "cpi", "ppi", "pce", "treasury", "yield",
        "dollar", "usd", "payroll", "nfp", "jobs", "unemployment",
        "rate cut", "rate hike", "interest rate", "safe haven",
        "geopolit", "war", "conflict", "tariff", "recession"
    )

    is_xau_related = any(k in t for k in xau_keywords)

    bullish_xau_signals = (
        "gold rises", "gold surges", "gold climbs", "gold gains", "gold hits",
        "rate cut", "dovish", "inflation cools", "inflation slows",
        "dollar falls", "dollar weakens", "dollar slips", "yields fall",
        "yields drop", "safe haven", "geopolitical", "tension", "war",
        "recession fears", "weak jobs", "unemployment rises"
    )

    bearish_xau_signals = (
        "gold falls", "gold drops", "gold slips", "gold retreats", "gold declines",
        "rate hike", "hawkish", "hot inflation", "inflation rises",
        "dollar surges", "dollar rallies", "dollar strengthens", "yields rise",
        "yields surge", "strong jobs", "payrolls beat", "higher for longer"
    )

    if any(k in t for k in bullish_xau_signals):
        sentiment = (
            "🟢 <b>Pengaruh ke Emas:</b> Harga Emas berpotensi <b>NAIK 📈 (Peluang 75%)</b> "
            "— Dolar AS melemah / investor memburu aset aman."
        )
    elif any(k in t for k in bearish_xau_signals):
        sentiment = (
            "🔴 <b>Pengaruh ke Emas:</b> Harga Emas berpotensi <b>TURUN 📉 (Peluang 75%)</b> "
            "— Dolar AS menguat sehingga menekan harga emas."
        )
    elif is_xau_related:
        sentiment = (
            "🟡 <b>Pengaruh ke Emas:</b> Berita penting penggerak Emas "
            "<b>(Peluang gerak 60%)</b> — Pantau pergerakan Dolar AS."
        )
    else:
        sentiment = (
            "⚪ <b>Pengaruh ke Emas:</b> Berita umum ekonomi AS "
            "<b>(Dampak kecil ~50%)</b> — Tidak langsung ke emas."
        )

    return is_xau_related, sentiment


def format_news_data(data):

    items = _extract_list_payload(data)

    if items is None:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nFormat data tidak dikenali."

    if not items:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nBelum ada berita terbaru."

    scored_news = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title_en = str(
            item.get("title")
            or item.get("headline")
            or item.get("name")
            or ""
        ).strip()

        if not title_en:
            continue

        summary_en = str(
            item.get("summary")
            or item.get("description")
            or item.get("snippet")
            or item.get("text")
            or ""
        ).strip()

        summary_en = re.sub(r"<[^>]+>", "", summary_en)
        summary_en = re.sub(r"\s+", " ", summary_en).strip()

        if len(summary_en) > 170:
            summary_en = summary_en[:167].rsplit(" ", 1)[0] + "..."

        combined_en = f"{title_en} {summary_en}"
        is_xau, sentiment = analyze_xau_news_sentiment(combined_en)

        scored_news.append((1 if is_xau else 0, is_xau, sentiment, title_en, summary_en, item))

    scored_news.sort(key=lambda x: x[0], reverse=True)
    top_news = scored_news[:6]

    if not top_news:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nBelum ada berita valid."

    texts_to_translate = []
    for _, _, _, title_en, summary_en, _ in top_news:
        texts_to_translate.append(title_en)
        texts_to_translate.append(summary_en if summary_en else "Tidak ada ringkasan tambahan.")

    translated_all = translate_texts_to_id(texts_to_translate)

    header = (
        "<b>📰 BERITA TERKINI US &amp; EFEK KE EMAS (XAUUSD)</b>\n"
        "<i>Terjemahan Bahasa Indonesia • Dilengkapi Estimasi %</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )

    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>💡 Berita bertanda <b>FOKUS EMAS</b> paling berpengaruh pada naik-turunnya harga XAUUSD.</i>"
    )

    body_blocks = []
    current_len = len(header) + len(footer)

    for idx, (_, is_xau, sentiment, title_en, summary_en, item) in enumerate(top_news):
        title_id = html.escape(translated_all[idx * 2] or title_en)
        summary_id = html.escape(translated_all[idx * 2 + 1]) if summary_en else ""

        source = html.escape(
            str(
                item.get("source")
                or item.get("publisher")
                or item.get("site")
                or "Market Wire"
            ).strip()
        )

        raw_time = (
            item.get("publishedAt")
            or item.get("published_at")
            or item.get("time")
            or item.get("date")
            or item.get("datetime")
        )
        time_wib = html.escape(format_wib_time(raw_time))

        tag = "🥇 <b>FOKUS EMAS</b>" if is_xau else "🇺🇸 <b>BERITA US</b>"
        summary_line = f"{summary_id}\n" if summary_id else ""

        block = (
            f"\n{tag} • <i>{source}</i>\n"
            f"<b>{idx + 1}. {title_id}</b>\n"
            f"🕒 <code>{time_wib}</code>\n"
            f"<blockquote>{summary_line}{sentiment}</blockquote>\n"
        )

        if current_len + len(block) > 3850:
            break

        body_blocks.append(block)
        current_len += len(block)

    return header + "".join(body_blocks) + footer


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
                "📅 Kalender Ekonomi US",
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


def info_menu(refresh_action):

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 Refresh Data",
                callback_data=refresh_action
            ),
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
        "Analisis memakai 100 candle tertutup terakhir\n"
        "(FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD).",
        reply_markup=main_menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🟢 BOT ONLINE\n"
        "Telegram polling aktif.\n"
        "Mode: Manual.\n"
        "Candle: 100 candle tertutup.\n"
        "Metode: FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD.\n"
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
                create_report(result),
                reply_markup=analysis_menu(symbol, timeframe)
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
            "⏳ Mengambil & menerjemahkan kalender ekonomi US..."
        )

        try:

            raw_result = await asyncio.to_thread(get_calendar)
            formatted_text = await asyncio.to_thread(
                format_calendar_data,
                raw_result
            )

            await message.edit_text(
                formatted_text,
                parse_mode="HTML",
                reply_markup=info_menu("calendar")
            )

        except Exception as error:

            log.exception("CALENDAR ERROR")

            await message.edit_text(
                f"❌ Kalender gagal:\n{str(error)[:2000]}",
                reply_markup=info_menu("calendar")
            )

        return

    if data == "news":

        message = await query.message.reply_text(
            "⏳ Mengambil & menerjemahkan berita market US..."
        )

        try:

            raw_result = await asyncio.to_thread(get_news)
            formatted_text = await asyncio.to_thread(
                format_news_data,
                raw_result
            )

            await message.edit_text(
                formatted_text,
                parse_mode="HTML",
                reply_markup=info_menu("news")
            )

        except Exception as error:

            log.exception("NEWS ERROR")

            await message.edit_text(
                f"❌ Berita gagal:\n{str(error)[:2000]}",
                reply_markup=info_menu("news")
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
    log.info("Strategy: FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD")
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
