# -*- coding: utf-8 -*-
import math
from datetime import datetime, timezone
from config import (
    MARKETS, TIMEFRAMES, CANDLE_COUNT, FETCH_COUNT,
    WIB, BULAN_ID, log, api_get, format_price
)
from session_tools import get_current_wib_session, calculate_lot_for_pair, record_new_signal


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

    interval = TIMEFRAMES[timeframe]
    data = api_get(f"/api/{symbol}/ohlc", params={"interval": interval, "limit": FETCH_COUNT})
    raw = extract_candle_list(data)
    return normalize_candles(raw, min_required=min_required)


def get_latest_price(symbol):
    candles = get_candles(symbol, "M15", min_required=20)
    return candles[-1]["close"]


# ============================================================
# HELPER STATISTIK & STRUKTUR SMC PER TIMEFRAME
# ============================================================

def _calc_avg_range(candles, period=20):
    sl = candles[-period:] if len(candles) >= period else candles
    avg = sum(c["high"] - c["low"] for c in sl) / max(1, len(sl))
    if avg <= 0:
        avg = candles[-1]["close"] * 0.001
    return avg


def _evaluate_tf_structure(candles):
    """
    Menganalisa arah tren (Bullish/Bearish) dan mencari zona OB/FVG/Supply/Demand terdekat
    pada satu timeframe tanpa menghasilkan nilai netral/kosong.
    """
    price = candles[-1]["close"]
    avg_range = _calc_avg_range(candles, 20)
    lookback = candles[:-1] if len(candles) > 10 else candles

    # 1. Cek BOS (Break of Structure) & EMA-like Midpoint Trend
    recent_20 = lookback[-20:] if len(lookback) >= 20 else lookback
    swing_high = max(c["high"] for c in recent_20)
    swing_low = min(c["low"] for c in recent_20)
    mid_range = (swing_high + swing_low) / 2.0

    ema_fast = sum(c["close"] for c in candles[-8:]) / min(8, len(candles))
    ema_slow = sum(c["close"] for c in candles[-21:]) / min(21, len(candles))

    bull_score = 0
    bear_score = 0

    if price > ema_fast:
        bull_score += 1
    else:
        bear_score += 1

    if ema_fast >= ema_slow:
        bull_score += 2
    else:
        bear_score += 2

    if price >= mid_range:
        bull_score += 1
    else:
        bear_score += 1

    # Cek Sweep Likuiditas 5 candle terakhir
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

    bias = "Bullish" if bull_score >= bear_score else "Bearish"

    # 2. Cari Zona Supply/OB Bearish (untuk SELL) & Demand/OB Bullish (untuk BUY)
    sell_zone = None
    buy_zone = None

    for i in range(len(lookback) - 2, max(1, len(lookback) - 55), -1):
        c = lookback[i]
        c1 = lookback[i + 1]
        c1_rng = c1["high"] - c1["low"]
        if c1_rng <= 0:
            continue

        # Bearish OB / Supply
        if sell_zone is None and c["close"] > c["open"] and c1["close"] < c["low"] and (c1["open"] - c1["close"]) >= c1_rng * 0.45:
            z_low, z_high = c["low"], c["high"]
            invalidated = any(lookback[k]["close"] > z_high + avg_range * 0.3 for k in range(i + 2, len(lookback)))
            if not invalidated and z_high >= price - avg_range * 0.4:
                sell_zone = ("Order Block / Supply", z_low, z_high)

        # Bullish OB / Demand
        if buy_zone is None and c["close"] < c["open"] and c1["close"] > c["high"] and (c1["close"] - c1["open"]) >= c1_rng * 0.45:
            z_low, z_high = c["low"], c["high"]
            invalidated = any(lookback[k]["close"] < z_low - avg_range * 0.3 for k in range(i + 2, len(lookback)))
            if not invalidated and z_low <= price + avg_range * 0.4:
                buy_zone = ("Order Block / Demand", z_low, z_high)

        if sell_zone and buy_zone:
            break

    # Cek FVG segar jika OB belum ditemukan
    if sell_zone is None or buy_zone is None:
        for i in range(len(lookback) - 1, max(2, len(lookback) - 45), -1):
            c1 = lookback[i - 2]
            c3 = lookback[i]
            if sell_zone is None and c3["high"] < c1["low"]:
                g_low, g_high = c3["high"], c1["low"]
                filled = any(lookback[k]["close"] > g_high for k in range(i + 1, len(lookback)))
                if not filled and g_high >= price - avg_range * 0.3:
                    sell_zone = ("FVG / Imbalance", g_low, g_high)
            if buy_zone is None and c3["low"] > c1["high"]:
                g_low, g_high = c1["high"], c3["low"]
                filled = any(lookback[k]["close"] < g_low for k in range(i + 1, len(lookback)))
                if not filled and g_low <= price + avg_range * 0.3:
                    buy_zone = ("FVG / Imbalance", g_low, g_high)
            if sell_zone and buy_zone:
                break

    # Fallback jika struktur sangat satu arah (menggunakan swing high/low terdekat)
    if sell_zone is None:
        top = max(c["high"] for c in lookback[-18:])
        if top < price:
            top = price + avg_range * 0.6
        sell_zone = ("Swing High / Supply", top - avg_range * 0.35, top)

    if buy_zone is None:
        bot = min(c["low"] for c in lookback[-18:])
        if bot > price:
            bot = price - avg_range * 0.6
        buy_zone = ("Swing Low / Demand", bot, bot + avg_range * 0.35)

    return {
        "price": price,
        "bias": bias,
        "avg_range": avg_range,
        "sell_zone": sell_zone,
        "buy_zone": buy_zone
    }


# ============================================================
# ENGINE UTAMA 3-TF ANTI-WAIT (SCALPING & INTRA DAY)
# ============================================================

def analyze_hybrid_3tf(symbol, mode):
    """
    Mode:
    - SCALPING : H1 (Penentu Arah Wajib) • M30 (Penentu Zona & Angka) • M15 (Penentu Momentum)
    - INTRADAY : D1 (Penentu Arah Wajib) • H4 (Penentu Zona & Angka) • H1 (Penentu Momentum)
    """
    mode_key = mode.upper()
    if mode_key == "SCALPING":
        tf_high, tf_mid, tf_low = "H1", "M30", "M15"
        mode_title = "HYBRID SCALPING"
    else:
        tf_high, tf_mid, tf_low = "D1", "H4", "H1"
        mode_title = "HYBRID INTRA DAY"

    c_high = get_candles(symbol, tf_high, min_required=30)
    c_mid = get_candles(symbol, tf_mid, min_required=40)
    c_low = get_candles(symbol, tf_low, min_required=40)

    info_high = _evaluate_tf_structure(c_high)
    info_mid = _evaluate_tf_structure(c_mid)
    info_low = _evaluate_tf_structure(c_low)

    current_price = info_low["price"]

    # Voting 3 TF dengan TF Tertinggi sebagai Hakim Utama (Anti-WAIT)
    biases = [info_high["bias"], info_mid["bias"], info_low["bias"]]
    bull_votes = biases.count("Bullish")
    bear_votes = biases.count("Bearish")

    if bull_votes >= 2 and info_high["bias"] == "Bullish":
        direction = "BUY"
    elif bear_votes >= 2 and info_high["bias"] == "Bearish":
        direction = "SELL"
    else:
        # Jika TF menengah/kecil bertabrakan, ikuti arah wajib TF Tertinggi
        direction = "BUY" if info_high["bias"] == "Bullish" else "SELL"

    # Ambil angka Entry, SL, TP dari TF Tengah (M30 untuk Scalping, H4 untuk Intra Day)
    avg_rng = info_mid["avg_range"]

    if direction == "SELL":
        z_name, z_low, z_high = info_mid["sell_zone"]
        # Jika harga saat ini sudah masuk/melewati zona tengah, geser zona ke area harga berjalan agar tetap valid
        if current_price > z_high:
            z_low = current_price
            z_high = current_price + avg_rng * 0.35
            z_name = f"Zona Supply Aktif ({tf_mid})"
        elif z_low - current_price > avg_rng * 3.5:
            # Batasi jarak jemputan agar realistis
            z_low = current_price + avg_rng * 0.65
            z_high = z_low + avg_rng * 0.40
            z_name = f"Area FVG/Supply Terdekat ({tf_mid})"

        entry_low = min(z_low, z_high)
        entry_high = max(z_low, z_high)
        sl = entry_high + avg_rng * 1.15
        risk = max(sl - entry_low, avg_rng * 0.6)
        tp1 = entry_low - risk * 1.0
        tp2 = entry_low - risk * 2.0
        tp3 = entry_low - risk * 3.0
    else:
        z_name, z_low, z_high = info_mid["buy_zone"]
        if current_price < z_low:
            z_high = current_price
            z_low = current_price - avg_rng * 0.35
            z_name = f"Zona Demand Aktif ({tf_mid})"
        elif current_price - z_high > avg_rng * 3.5:
            z_high = current_price - avg_rng * 0.65
            z_low = z_high - avg_rng * 0.40
            z_name = f"Area FVG/Demand Terdekat ({tf_mid})"

        entry_low = min(z_low, z_high)
        entry_high = max(z_low, z_high)
        sl = entry_low - avg_rng * 1.15
        risk = max(entry_high - sl, avg_rng * 0.6)
        tp1 = entry_high + risk * 1.0
        tp2 = entry_high + risk * 2.0
        tp3 = entry_high + risk * 3.0

    # Label status TF terkecil untuk ditampilkan di baris Confluence
    if (direction == "BUY" and info_low["bias"] == "Bullish") or (direction == "SELL" and info_low["bias"] == "Bearish"):
        low_label = f"{info_low['bias']} / Searah"
        exec_note = f"Struktur {tf_high} & {tf_low} kompak {info_high['bias']}. Harga siap merespons {z_name} di {tf_mid}."
    else:
        low_label = "Pullback / Koreksi"
        exec_note = f"Struktur utama {tf_high} {info_high['bias']}. Harga sedang koreksi di {tf_low} menuju {z_name} pada {tf_mid}."

    sesi_nama, _ = get_current_wib_session()

    result = {
        "symbol": symbol,
        "mode": mode_key,
        "mode_title": mode_title,
        "tf_high": tf_high,
        "tf_mid": tf_mid,
        "tf_low": tf_low,
        "bias_high": info_high["bias"],
        "bias_mid": info_mid["bias"],
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

    # Simpan sinyal ke riwayat Winrate
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
