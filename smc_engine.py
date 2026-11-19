# -*- coding: utf-8 -*-
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from config import (
    MARKETS, TIMEFRAMES, CANDLE_COUNT, FETCH_COUNT,
    WIB, BULAN_ID, log, api_get, format_price
)
from session_tools import get_current_wib_session, calculate_lot_for_pair, record_new_signal

_CANDLE_CACHE = {}
_CANDLE_CACHE_TTL = 15


# ============================================================
# CANDLE DATA PARSER (CLOSED CANDLES)
# ============================================================

def extract_candle_list(data):
    if not isinstance(data, dict):
        raise ValueError(f"Format respons candle bukan object: {type(data).__name__}")
    bars = data.get("bars")
    if not isinstance(bars, list):
        raise ValueError(f"Properti 'bars' tidak ditemukan. Keys: {list(data.keys())}")
    if not bars:
        raise ValueError("API mengembalikan bars kosong")
    return bars


def parse_candle_time(value):
    if value is None:
        raise ValueError("Waktu candle kosong")

    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Waktu candle kosong")
        try:
            number = float(value)
        except ValueError:
            iso = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                result = datetime.fromisoformat(iso)
            except ValueError:
                raise ValueError(f"Format waktu candle tidak dikenal: {value}")
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.timestamp()
    else:
        number = float(value)

    if number > 100000000000:
        number /= 1000
    return number


def normalize_candles(raw, min_required=40):
    candles = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            required = ["open", "high", "low", "close", "openTime"]
            if any(item.get(k) is None for k in required):
                continue

            is_open = item.get("isOpen", False)
            if is_open is True or str(is_open).lower() in ("true", "1"):
                continue

            candle = {
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "time": parse_candle_time(item["openTime"])
            }

            if candle["high"] < candle["low"]:
                continue
            if candle["high"] < max(candle["open"], candle["close"]):
                continue
            if candle["low"] > min(candle["open"], candle["close"]):
                continue

            candles.append(candle)
        except (ValueError, TypeError, KeyError):
            continue

    if not candles:
        raise ValueError("Tidak ada candle valid setelah normalisasi")

    unique = {c["time"]: c for c in candles}
    candles = sorted(unique.values(), key=lambda c: c["time"])[-CANDLE_COUNT:]

    if len(candles) < min_required:
        raise ValueError(f"Candle tertutup kurang ({len(candles)} < {min_required}).")

    return candles


def get_candles(symbol, timeframe, min_required=40):
    if symbol not in MARKETS:
        raise ValueError("Market tidak valid")
    if timeframe not in TIMEFRAMES:
        raise ValueError("Timeframe tidak valid")

    cache_key = (symbol, timeframe)
    now_ts = time.time()
    cached = _CANDLE_CACHE.get(cache_key)
    if cached and (now_ts - cached[0] < _CANDLE_CACHE_TTL) and len(cached[1]) >= min_required:
        return cached[1]

    interval = TIMEFRAMES[timeframe]
    data = api_get(f"/api/{symbol}/ohlc", params={"interval": interval, "limit": FETCH_COUNT})
    raw = extract_candle_list(data)
    candles = normalize_candles(raw, min_required=min_required)
    _CANDLE_CACHE[cache_key] = (now_ts, candles)
    return candles


def get_latest_price(symbol):
    candles = get_candles(symbol, "M15", min_required=20)
    return candles[-1]["close"]


# ============================================================
# HELPER STATISTIK & PEMBACAAN STRUKTUR SMC ASLI
# ============================================================

def _calc_avg_range(candles, period=20):
    sl = candles[-period:] if len(candles) >= period else candles
    avg = sum(c["high"] - c["low"] for c in sl) / max(1, len(sl))
    if avg <= 0:
        avg = candles[-1]["close"] * 0.001
    return avg


def _evaluate_tf_bias(candles):
    price = candles[-1]["close"]
    lookback = candles[:-1] if len(candles) > 10 else candles
    recent_20 = lookback[-20:] if len(lookback) >= 20 else lookback

    swing_high = max(c["high"] for c in recent_20)
    swing_low = min(c["low"] for c in recent_20)
    mid_range = (swing_high + swing_low) / 2.0

    ema_fast = sum(c["close"] for c in candles[-8:]) / min(8, len(candles))
    ema_slow = sum(c["close"] for c in candles[-21:]) / min(21, len(candles))

    bull_score = 1 if price > ema_fast else 0
    bear_score = 1 if price <= ema_fast else 0

    if ema_fast >= ema_slow:
        bull_score += 2
    else:
        bear_score += 2

    if price >= mid_range:
        bull_score += 1
    else:
        bear_score += 1

    prior = lookback[-30:-5] if len(lookback) >= 30 else lookback[:-5]
    if prior:
        p_high = max(c["high"] for c in prior)
        p_low = min(c["low"] for c in prior)
        swept_high = any(c["high"] > p_high and c["close"] < p_high for c in candles[-5:])
        swept_low = any(c["low"] < p_low and c["close"] > p_low for c in candles[-5:])
        if swept_low and not swept_high:
            bull_score += 2
        elif swept_high and not swept_low:
            bear_score += 2

    return "Bullish" if bull_score >= bear_score else "Bearish"


def _find_structure_zone_on_tf(candles, direction, min_dist, max_dist, max_sl_dist):
    price = candles[-1]["close"]
    lookback = candles[:-1]
    avg_rng = _calc_avg_range(candles, 20)
    wick_buffer = max(avg_rng * 0.25, price * 0.00025)

    candidates = []

    if direction == "SELL":
        for i in range(len(lookback) - 2, max(1, len(lookback) - 50), -1):
            c = lookback[i]
            c1 = lookback[i + 1]
            c1_rng = c1["high"] - c1["low"]
            if c1_rng <= 0:
                continue
            if c["close"] > c["open"] and c1["close"] < c["low"] and (c1["open"] - c1["close"]) >= c1_rng * 0.45:
                z_low, z_high = c["low"], max(c["high"], c1["high"])
                dist = z_low - price
                if min_dist <= dist <= max_dist:
                    invalidated = any(lookback[k]["close"] > z_high for k in range(i + 2, len(lookback)))
                    if not invalidated:
                        sl = z_high + wick_buffer
                        if (sl - z_low) <= max_sl_dist:
                            candidates.append((dist, "Bearish Order Block", z_low, z_high, sl))

        for i in range(len(lookback) - 1, max(2, len(lookback) - 45), -1):
            c1 = lookback[i - 2]
            c3 = lookback[i]
            if c3["high"] < c1["low"]:
                g_low, g_high = c3["high"], c1["low"]
                dist = g_low - price
                if min_dist <= dist <= max_dist:
                    filled = any(lookback[k]["close"] > g_high for k in range(i + 1, len(lookback)))
                    if not filled:
                        sl = max(g_high, c1["high"]) + wick_buffer
                        if (sl - g_low) <= max_sl_dist:
                            candidates.append((dist, "Bearish FVG", g_low, g_high, sl))

        for i in range(len(lookback) - 3, max(2, len(lookback) - 40), -1):
            h = lookback[i]["high"]
            if h >= lookback[i - 1]["high"] and h > lookback[i + 1]["high"]:
                dist = h - price
                if min_dist <= dist <= max_dist:
                    z_low = max(lookback[i]["open"], lookback[i]["close"], h - avg_rng * 0.35)
                    z_high = h
                    sl = z_high + wick_buffer
                    if (sl - z_low) <= max_sl_dist:
                        candidates.append((dist + avg_rng * 0.1, "Swing High / Supply", z_low, z_high, sl))

    else:
        for i in range(len(lookback) - 2, max(1, len(lookback) - 50), -1):
            c = lookback[i]
            c1 = lookback[i + 1]
            c1_rng = c1["high"] - c1["low"]
            if c1_rng <= 0:
                continue
            if c["close"] < c["open"] and c1["close"] > c["high"] and (c1["close"] - c1["open"]) >= c1_rng * 0.45:
                z_low, z_high = min(c["low"], c1["low"]), c["high"]
                dist = price - z_high
                if min_dist <= dist <= max_dist:
                    invalidated = any(lookback[k]["close"] < z_low for k in range(i + 2, len(lookback)))
                    if not invalidated:
                        sl = z_low - wick_buffer
                        if (z_high - sl) <= max_sl_dist:
                            candidates.append((dist, "Bullish Order Block", z_low, z_high, sl))

        for i in range(len(lookback) - 1, max(2, len(lookback) - 45), -1):
            c1 = lookback[i - 2]
            c3 = lookback[i]
            if c3["low"] > c1["high"]:
                g_low, g_high = c1["high"], c3["low"]
                dist = price - g_high
                if min_dist <= dist <= max_dist:
                    filled = any(lookback[k]["close"] < g_low for k in range(i + 1, len(lookback)))
                    if not filled:
                        sl = min(g_low, c1["low"]) - wick_buffer
                        if (g_high - sl) <= max_sl_dist:
                            candidates.append((dist, "Bullish FVG", g_low, g_high, sl))

        for i in range(len(lookback) - 3, max(2, len(lookback) - 40), -1):
            l = lookback[i]["low"]
            if l <= lookback[i - 1]["low"] and l < lookback[i + 1]["low"]:
                dist = price - l
                if min_dist <= dist <= max_dist:
                    z_low = l
                    z_high = min(lookback[i]["open"], lookback[i]["close"], l + avg_rng * 0.35)
                    sl = z_low - wick_buffer
                    if (z_high - sl) <= max_sl_dist:
                        candidates.append((dist + avg_rng * 0.1, "Swing Low / Demand", z_low, z_high, sl))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        _, label, z_low, z_high, sl = candidates[0]
        return label, z_low, z_high, sl

    return None


# ============================================================
# ENGINE UTAMA 3-TF PARALLEL (SATSET & UNIK PER METODE)
# ============================================================

def analyze_hybrid_3tf(symbol, mode):
    mode_key = mode.upper()
    if mode_key == "SCALPING":
        tf_high, tf_mid, tf_low = "H1", "M30", "M15"
        mode_title = "HYBRID SCALPING"
        min_dist = -0.8 if symbol == "XAUUSD" else -60.0
        max_dist = 3.5 if symbol == "XAUUSD" else 260.0
        max_sl_dist = 4.8 if symbol == "XAUUSD" else 360.0
    else:
        tf_high, tf_mid, tf_low = "D1", "H4", "H1"
        mode_title = "HYBRID INTRA DAY"
        min_dist = 1.8 if symbol == "XAUUSD" else 150.0
        max_dist = 8.5 if symbol == "XAUUSD" else 650.0
        max_sl_dist = 9.5 if symbol == "XAUUSD" else 750.0

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_high = executor.submit(get_candles, symbol, tf_high, 30)
        f_mid = executor.submit(get_candles, symbol, tf_mid, 40)
        f_low = executor.submit(get_candles, symbol, tf_low, 40)
        c_high = f_high.result()
        c_mid = f_mid.result()
        c_low = f_low.result()

    bias_high = _evaluate_tf_bias(c_high)
    bias_mid = _evaluate_tf_bias(c_mid)
    bias_low = _evaluate_tf_bias(c_low)

    current_price = c_low[-1]["close"]

    biases = [bias_high, bias_mid, bias_low]
    if biases.count("Bullish") >= 2 and bias_high == "Bullish":
        direction = "BUY"
    elif biases.count("Bearish") >= 2 and bias_high == "Bearish":
        direction = "SELL"
    else:
        direction = "BUY" if bias_high == "Bullish" else "SELL"

    found_zone = _find_structure_zone_on_tf(c_low, direction, min_dist, max_dist, max_sl_dist)

    if found_zone is not None:
        z_name, entry_low, entry_high, sl = found_zone
    else:
        recent_bars = c_low[-4:]
        avg_rng = _calc_avg_range(c_low, 15)
        buf = min(avg_rng * 0.35, (2.2 if symbol == "XAUUSD" else 180.0))
        offset = 0.0 if mode_key == "SCALPING" else (2.2 if symbol == "XAUUSD" else 180.0)

        if direction == "SELL":
            top_wick = max(c["high"] for c in recent_bars)
            entry_low = max(current_price + offset, top_wick - avg_rng * 0.25)
            entry_high = max(entry_low + (1.2 if symbol == "XAUUSD" else 90.0), top_wick + offset * 0.5)
            sl = entry_high + buf
            z_name = f"Zona Supply / Rejection ({tf_low})"
        else:
            bot_wick = min(c["low"] for c in recent_bars)
            entry_high = min(current_price - offset, bot_wick + avg_rng * 0.25)
            entry_low = min(entry_high - (1.2 if symbol == "XAUUSD" else 90.0), bot_wick - offset * 0.5)
            sl = entry_low - buf
            z_name = f"Zona Demand / Rejection ({tf_low})"

    if direction == "SELL":
        risk = max(sl - entry_low, (1.8 if symbol == "XAUUSD" else 140.0))
        tp1 = entry_low - risk * 1.0
        tp2 = entry_low - risk * 1.8
        tp3 = entry_low - risk * 2.5
    else:
        risk = max(entry_high - sl, (1.8 if symbol == "XAUUSD" else 140.0))
        tp1 = entry_high + risk * 1.0
        tp2 = entry_high + risk * 1.8
        tp3 = entry_high + risk * 2.5

    if (direction == "BUY" and bias_low == "Bullish") or (direction == "SELL" and bias_low == "Bearish"):
        low_label = f"{bias_low} / Searah"
        exec_note = f"Struktur {tf_high} & {tf_mid} {bias_high} searah. Titik entry presisi ditarik dari {z_name} pada {tf_low}."
    else:
        low_label = "Pullback / Koreksi"
        exec_note = f"Tren utama {tf_high} {bias_high}. Menunggu harga menyelesaikan koreksi ke area pantulan {z_name} pada {tf_low}."

    sesi_nama, _ = get_current_wib_session()

    result = {
        "symbol": symbol,
        "mode": mode_key,
        "mode_title": mode_title,
        "tf_high": tf_high,
        "tf_mid": tf_mid,
        "tf_low": tf_low,
        "bias_high": bias_high,
        "bias_mid": bias_mid,
        "bias_low_label": low_label,
        "price": current_price,
        "direction": direction,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "exec_note": exec_note,
        "session_name": sesi_nama
    }

    try:
        record_new_signal(result)
    except Exception as err:
        log.warning("Gagal mencatat sinyal ke winrate: %s", err)

    return result


def format_hybrid_signal_report(res, risk_profile=None):
    now = datetime.now(WIB)
    bulan = BULAN_ID.get(now.month, now.strftime("%b"))
    waktu_str = f"{now.day:02d} {bulan} {now.year} • {now.strftime('%H:%M')} WIB"

    is_buy = res["direction"] == "BUY"
    sig_badge = "🟢 <b>BUY</b>" if is_buy else "🔴 <b>SELL</b>"
    sym = res["symbol"]

    lines = [
        f"🔥 <b>{res['mode_title']}: {sym}</b>",
        f"🕒 Update: {waktu_str}",
        f"📊 Confluence: {res['tf_high']} ({res['bias_high']}) • {res['tf_mid']} ({res['bias_mid']}) • {res['tf_low']} ({res['bias_low_label']})",
        "",
        f"⚡ Status Sinyal: {sig_badge}",
        f"📍 Entry Zone: <code>{format_price(res['entry_low'])} - {format_price(res['entry_high'])}</code>",
        f"🛑 Stop Loss: <code>{format_price(res['sl'])}</code>",
        f"🎯 TP 1: <code>{format_price(res['tp1'])}</code>",
        f"🎯 TP 2: <code>{format_price(res['tp2'])}</code>",
        f"🎯 TP 3: <code>{format_price(res['tp3'])}</code>",
        "",
        f"💡 <b>Catatan Sinyal:</b> {res['exec_note']}"
    ]

    if risk_profile:
        worst_entry = res["entry_high"] if is_buy else res["entry_low"]
        sl_points = abs(worst_entry - res["sl"])
        if sl_points > 0:
            lot, loss_usd, _, over_risk = calculate_lot_for_pair(
                sym, risk_profile.get("balance"), risk_profile.get("risk_pct"), sl_points
            )
            warn = " ⚠️" if over_risk else ""
            lines.append(
                f"\n🧮 <b>Rekomendasi Lot</b> (Modal ${risk_profile['balance']:,.0f} | Risk {risk_profile['risk_pct']:.1f}%): "
                f"<b>{lot:.2f} Lot</b> (SL -${loss_usd:,.2f}){warn}"
            )

    return "\n".join(lines)
