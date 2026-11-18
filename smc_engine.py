# -*- coding: utf-8 -*-
import math
from datetime import datetime, timezone
from config import (
    MARKETS, TIMEFRAMES, CANDLE_COUNT, FETCH_COUNT,
    WIB, log, api_get, format_price
)

# Syarat sinyal arah: minimal konfirmasi searah & selisih minimal terhadap sinyal lawan
MIN_CONFIRMATIONS = 3
MIN_EDGE = 2
MIN_CANDLES_REQUIRED = 70

# ============================================================
# CANDLE DATA PARSER (CLOSED CANDLES — INDEPENDEN PER TF)
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
            number = float(value)  # string numerik (epoch detik / milidetik)
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


def normalize_candles(raw):
    candles = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            required = ["open", "high", "low", "close", "openTime"]
            if any(item.get(k) is None for k in required):
                continue

            # Buang candle yang masih berjalan (belum closed)
            is_open = item.get("isOpen", False)
            if is_open is True or str(is_open).lower() in ("true", "1"):
                continue

            candle = {
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "time": parse_candle_time(item["openTime"]),
                "is_open": False
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

    if len(candles) < CANDLE_COUNT:
        raise ValueError(
            f"Hanya tersedia {len(candles)} candle tertutup. Dibutuhkan {CANDLE_COUNT} candle."
        )

    return candles


def get_candles(symbol, timeframe):
    if symbol not in MARKETS:
        raise ValueError("Market tidak valid")
    if timeframe not in TIMEFRAMES:
        raise ValueError("Timeframe tidak valid")

    interval = TIMEFRAMES[timeframe]
    data = api_get(f"/api/{symbol}/ohlc", params={"interval": interval, "limit": FETCH_COUNT})
    raw = extract_candle_list(data)
    return normalize_candles(raw)


def get_latest_price(symbol):
    candles = get_candles(symbol, "M1")
    return candles[-1]["close"]


# ============================================================
# RADAR SESI PASAR (WINTER TIME WIB) & LIQUIDITY SWEEP
# ============================================================

def get_session_killzone_info(symbol):
    now_wib = datetime.now(WIB)
    t = now_wib.hour * 60 + now_wib.minute
    jam_str = now_wib.strftime("%H:%M WIB")

    if 20 * 60 + 15 <= t <= 22 * 60 + 30:
        return (
            f"🚨 NEW YORK OPEN ({jam_str}) — Volatilitas Ekstrem",
            False,
            "WASPADA NY OPEN (Tunggu Rejection M1/M5, Hindari Limit Buta)"
        )
    if t >= 22 * 60 + 31 or t < 4 * 60:
        return (
            f"🇺🇸 NEW YORK / USA SESSION ({jam_str}) — Likuiditas Amerika",
            True,
            "AMAN PASANG LIMIT (Pantau London Close 00:00 WIB)"
        )
    if 14 * 60 + 45 <= t <= 16 * 60 + 30:
        return (
            f"⚠️ LONDON OPEN ({jam_str}) — Rawan Sapuan Likuiditas",
            False,
            "WASPADA LONDON OPEN (Gunakan Konfirmasi Rejection M1/M5)"
        )
    if 16 * 60 + 31 <= t <= 20 * 60 + 14:
        return (
            f"⚡ LONDON SESSION ({jam_str}) — Struktur Rapi",
            True,
            "AMAN PASANG LIMIT (Zona Struktur Efektif)"
        )
    if 8 * 60 + 45 <= t <= 9 * 60 + 30:
        return (
            f"🌏 ASIA OPEN ({jam_str}) — Likuiditas Penuh Asia",
            True,
            "AMAN PASANG LIMIT (Pantau Range Pagi)"
        )
    if 9 * 60 + 31 <= t <= 14 * 60 + 44:
        return (
            f"🌏 ASIA SESSION ({jam_str}) — Range Terkontrol",
            True,
            "AMAN PASANG LIMIT (Ideal untuk Pantulan Range)"
        )
    if symbol == "BTCUSD":
        return (
            f"🌐 CRYPTO 24/7 SESSION ({jam_str}) — Likuiditas Aktif",
            True,
            "AMAN PASANG LIMIT"
        )
    return (
        f"🇦🇺 SYDNEY / PRE-ASIA SESSION ({jam_str}) — Volume Tipis",
        True,
        "AMAN PASANG LIMIT (Spread & Volume Pagi)"
    )


def get_short_session_badge(symbol):
    now_wib = datetime.now(WIB)
    t = now_wib.hour * 60 + now_wib.minute
    if 20 * 60 + 15 <= t <= 22 * 60 + 30:
        return "🚨 NY Open"
    if t >= 22 * 60 + 31 or t < 4 * 60:
        return "🇺🇸 NY Session"
    if 14 * 60 + 45 <= t <= 16 * 60 + 30:
        return "⚠️ London Open"
    if 16 * 60 + 31 <= t <= 20 * 60 + 14:
        return "⚡ London Session"
    if 8 * 60 + 45 <= t <= 14 * 60 + 44:
        return "🌏 Asia Session"
    if symbol == "BTCUSD":
        return "🌐 Crypto 24/7"
    return "🇦🇺 Sydney / Pre-Asia"


def detect_liquidity_sweep(candles):
    if len(candles) < 35:
        return "NEUTRAL", "Belum ada sapuan likuiditas"

    prior = candles[-35:-5]
    recent_window = candles[-5:]

    prev_high = max(c["high"] for c in prior)
    prev_low = min(c["low"] for c in prior)

    swept_high = any(c["high"] > prev_high and c["close"] < prev_high for c in recent_window)
    swept_low = any(c["low"] < prev_low and c["close"] > prev_low for c in recent_window)

    if swept_low and not swept_high:
        return "BUY", f"SSL Swept @ {format_price(prev_low)} (Potensi Reversal Naik)"
    if swept_high and not swept_low:
        return "SELL", f"BSL Swept @ {format_price(prev_high)} (Potensi Reversal Turun)"
    return "NEUTRAL", "Belum ada sweep signifikan"


# ============================================================
# HELPER STATISTIK CANDLE
# ============================================================

def _calc_avg_range(candles, period=20):
    sl = candles[-period:] if len(candles) >= period else candles
    avg = sum(c["high"] - c["low"] for c in sl) / max(1, len(sl))
    if avg <= 0:
        avg = candles[-1]["close"] * 0.001
    return avg


# ============================================================
# 7 METODE ANALISA MURNI SMC (UNMITIGATED & RETEST-REJECT)
# ============================================================

def detect_ob(candles):
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.30

    for i in range(len(lookback) - 2, 0, -1):
        ob = lookback[i]
        c1 = lookback[i + 1]
        ob_low, ob_high = ob["low"], ob["high"]
        c1_rng = c1["high"] - c1["low"]

        if ob["close"] < ob["open"]:
            c1_body = c1["close"] - c1["open"]
            impulsive = c1_rng > 0 and c1_body >= c1_rng * 0.50 and c1["close"] > ob_high
            if impulsive:
                invalidated = any(lookback[k]["close"] < ob_low - buffer for k in range(i + 2, len(lookback)))
                if not invalidated:
                    in_zone = (ob_low - buffer <= price <= ob_high + buffer) or (last_low <= ob_high and price >= ob_low)
                    if in_zone:
                        return "BUY", "Bullish OB", ob_low, ob_high

        if ob["close"] > ob["open"]:
            c1_body = c1["open"] - c1["close"]
            impulsive = c1_rng > 0 and c1_body >= c1_rng * 0.50 and c1["close"] < ob_low
            if impulsive:
                invalidated = any(lookback[k]["close"] > ob_high + buffer for k in range(i + 2, len(lookback)))
                if not invalidated:
                    in_zone = (ob_low - buffer <= price <= ob_high + buffer) or (last_high >= ob_low and price <= ob_high)
                    if in_zone:
                        return "SELL", "Bearish OB", ob_low, ob_high

    return "NEUTRAL", "-", None, None


def detect_fvg(candles):
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.20
    min_gap = avg_range * 0.10

    for i in range(len(lookback) - 1, 1, -1):
        c1 = lookback[i - 2]
        c3 = lookback[i]

        if c3["low"] - c1["high"] >= min_gap:
            gap_low = c1["high"]
            gap_high = c3["low"]
            filled = any(lookback[k]["close"] < gap_low for k in range(i + 1, len(lookback)))
            if not filled:
                if (gap_low - buffer <= price <= gap_high + buffer) or (last_low <= gap_high and price >= gap_low):
                    return "BUY", gap_low, gap_high

        if c1["low"] - c3["high"] >= min_gap:
            gap_low = c3["high"]
            gap_high = c1["low"]
            filled = any(lookback[k]["close"] > gap_high for k in range(i + 1, len(lookback)))
            if not filled:
                if (gap_low - buffer <= price <= gap_high + buffer) or (last_high >= gap_low and price <= gap_high):
                    return "SELL", gap_low, gap_high

    return "NEUTRAL", None, None


def detect_bos(candles):
    recent = candles[-1]
    previous = candles[-16:-1]
    highest = max(c["high"] for c in previous)
    lowest = min(c["low"] for c in previous)
    if recent["close"] > highest:
        return "BUY"
    if recent["close"] < lowest:
        return "SELL"
    return "NEUTRAL"


def detect_snr(candles):
    last = candles[-1]
    prev_c = candles[-2]
    previous = candles[-36:-1]
    support = min(c["low"] for c in previous)
    resistance = max(c["high"] for c in previous)
    price = last["close"]
    avg_range = _calc_avg_range(previous, 25)
    zone_tol = avg_range * 0.60

    touched_sup = (last["low"] <= support + zone_tol) or (prev_c["low"] <= support + zone_tol)
    rejected_sup = price > support and last["close"] >= last["open"]
    if touched_sup and rejected_sup and abs(price - support) <= avg_range * 1.2:
        return "BUY", support

    touched_res = (last["high"] >= resistance - zone_tol) or (prev_c["high"] >= resistance - zone_tol)
    rejected_res = price < resistance and last["close"] <= last["open"]
    if touched_res and rejected_res and abs(resistance - price) <= avg_range * 1.2:
        return "SELL", resistance

    return "NEUTRAL", None


def detect_sbr_rbs(candles):
    last = candles[-1]
    prev_c = candles[-2]
    price = last["close"]
    lookback = candles[-65:-1]
    if len(lookback) < 12:
        return "NEUTRAL", "-", None, "NONE"

    avg_range = _calc_avg_range(lookback, 25)
    zone_tol = avg_range * 0.55
    prev_idx = len(lookback) - 1

    swing_highs, swing_lows = [], []
    for i in range(2, len(lookback) - 3):
        h, l = lookback[i]["high"], lookback[i]["low"]
        if h >= lookback[i - 1]["high"] and h >= lookback[i - 2]["high"] and h > lookback[i + 1]["high"] and h > lookback[i + 2]["high"]:
            swing_highs.append((i, h))
        if l <= lookback[i - 1]["low"] and l <= lookback[i - 2]["low"] and l < lookback[i + 1]["low"] and l < lookback[i + 2]["low"]:
            swing_lows.append((i, l))

    for idx, res_level in reversed(swing_highs):
        break_bars = [k for k in range(idx + 1, len(lookback)) if lookback[k]["close"] > res_level]
        if not break_bars:
            continue
        first_break = break_bars[0]
        broken_back = any(lookback[k]["close"] < res_level - zone_tol for k in range(first_break + 1, len(lookback)))
        if broken_back:
            continue

        prev_valid = prev_idx > first_break
        retested = (last["low"] <= res_level + zone_tol) or (prev_valid and prev_c["low"] <= res_level + zone_tol)
        rejected = price > res_level and (last["close"] > last["open"] or (last["close"] - last["low"]) > (last["high"] - last["close"]))
        if retested and rejected and abs(price - res_level) <= avg_range * 1.3:
            return "BUY", "RBS", res_level, "CONFIRMED"

        if first_break >= len(lookback) - 3 and price > res_level:
            return "NEUTRAL", "RBS", res_level, "BREAKOUT_WAIT_RETEST"

    for idx, sup_level in reversed(swing_lows):
        break_bars = [k for k in range(idx + 1, len(lookback)) if lookback[k]["close"] < sup_level]
        if not break_bars:
            continue
        first_break = break_bars[0]
        broken_back = any(lookback[k]["close"] > sup_level + zone_tol for k in range(first_break + 1, len(lookback)))
        if broken_back:
            continue

        prev_valid = prev_idx > first_break
        retested = (last["high"] >= sup_level - zone_tol) or (prev_valid and prev_c["high"] >= sup_level - zone_tol)
        rejected = price < sup_level and (last["close"] < last["open"] or (last["high"] - last["close"]) > (last["close"] - last["low"]))
        if retested and rejected and abs(sup_level - price) <= avg_range * 1.3:
            return "SELL", "SBR", sup_level, "CONFIRMED"

        if first_break >= len(lookback) - 3 and price < sup_level:
            return "NEUTRAL", "SBR", sup_level, "BREAKOUT_WAIT_RETEST"

    return "NEUTRAL", "-", None, "NONE"


def detect_snd(candles):
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[-45:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.35

    for i in range(len(lookback) - 1, 1, -1):
        cur = lookback[i]
        bef = lookback[i - 1]
        rng = cur["high"] - cur["low"]
        if rng <= 0:
            continue
        body = abs(cur["close"] - cur["open"])

        if cur["close"] > cur["open"] and body >= rng * 0.60 and cur["close"] > bef["high"]:
            d_low = bef["low"]
            d_high = max(bef["high"], cur["open"])
            broken = any(lookback[k]["close"] < d_low - buffer for k in range(i + 1, len(lookback)))
            if not broken and ((d_low - buffer <= price <= d_high + buffer) or (last_low <= d_high and price >= d_low)):
                return "BUY", d_low, d_high

        if cur["close"] < cur["open"] and body >= rng * 0.60 and cur["close"] < bef["low"]:
            s_low = min(bef["low"], cur["open"])
            s_high = bef["high"]
            broken = any(lookback[k]["close"] > s_high + buffer for k in range(i + 1, len(lookback)))
            if not broken and ((s_low - buffer <= price <= s_high + buffer) or (last_high >= s_low and price <= s_high)):
                return "SELL", s_low, s_high

    return "NEUTRAL", None, None


def detect_rbr_dbd(candles):
    last = candles[-1]
    price = last["close"]
    lookback = candles[-65:-1]
    if len(lookback) < 10:
        return "NEUTRAL", "-", None, None

    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.45

    def is_impulse_bull(c):
        rng = c["high"] - c["low"]
        body = c["close"] - c["open"]
        return rng > 0 and body > 0 and (body / rng) >= 0.48 and rng >= avg_range * 0.65

    def is_impulse_bear(c):
        rng = c["high"] - c["low"]
        body = c["open"] - c["close"]
        return rng > 0 and body > 0 and (body / rng) >= 0.48 and rng >= avg_range * 0.65

    def is_base_candle(c):
        rng = c["high"] - c["low"]
        body = abs(c["close"] - c["open"])
        return rng <= avg_range * 1.25 and (rng <= 0 or (body / rng) <= 0.65)

    for i in range(len(lookback) - 3, 1, -1):
        leg1 = lookback[i]
        for base_len in (1, 2, 3, 4):
            leg2_idx = i + base_len + 1
            if leg2_idx >= len(lookback):
                continue

            base_slice = lookback[i + 1: leg2_idx]
            leg2 = lookback[leg2_idx]

            if not all(is_base_candle(bc) for bc in base_slice):
                continue

            z_low = min(bc["low"] for bc in base_slice)
            z_high = max(bc["high"] for bc in base_slice)

            if is_impulse_bull(leg1) and is_impulse_bull(leg2) and leg2["close"] > z_high:
                invalidated = any(lookback[k]["close"] < z_low - buffer for k in range(leg2_idx + 1, len(lookback)))
                if not invalidated:
                    in_zone = (z_low - buffer <= price <= z_high + buffer) or (last["low"] <= z_high and price >= z_low - buffer)
                    if in_zone:
                        return "BUY", "RBR", z_low, z_high

            if is_impulse_bear(leg1) and is_impulse_bear(leg2) and leg2["close"] < z_low:
                invalidated = any(lookback[k]["close"] > z_high + buffer for k in range(leg2_idx + 1, len(lookback)))
                if not invalidated:
                    in_zone = (z_low - buffer <= price <= z_high + buffer) or (last["high"] >= z_low and price <= z_high + buffer)
                    if in_zone:
                        return "SELL", "DBD", z_low, z_high

    return "NEUTRAL", "-", None, None


# ============================================================
# SINYAL PENDING LIMIT DEKAT HARGA (SELL LIMIT & BUY LIMIT)
# ============================================================

def find_tight_pending_limits(candles):
    price = candles[-1]["close"]
    lookback = candles[-70:-1]
    avg_range = _calc_avg_range(candles, 20)

    min_dist = avg_range * 0.45
    max_dist = avg_range * 4.00

    sell_candidates = []
    buy_candidates = []

    for i in range(len(lookback) - 2, 2, -1):
        c = lookback[i]
        c1 = lookback[i + 1]
        c1_rng = c1["high"] - c1["low"]
        if c1_rng <= 0:
            continue

        if c["close"] > c["open"] and c1["close"] < c["low"] and (c1["open"] - c1["close"]) >= c1_rng * 0.50:
            z_low, z_high = c["low"], c["high"]
            dist = z_low - price
            if min_dist <= dist <= max_dist:
                mitigated = any(lookback[k]["close"] > z_high for k in range(i + 2, len(lookback)))
                if not mitigated:
                    sell_candidates.append((dist, "Fresh Bearish OB", z_low, z_high))

        if c["close"] < c["open"] and c1["close"] > c["high"] and (c1["close"] - c1["open"]) >= c1_rng * 0.50:
            z_low, z_high = c["low"], c["high"]
            dist = price - z_high
            if min_dist <= dist <= max_dist:
                mitigated = any(lookback[k]["close"] < z_low for k in range(i + 2, len(lookback)))
                if not mitigated:
                    buy_candidates.append((dist, "Fresh Bullish OB", z_low, z_high))

    for i in range(len(lookback) - 1, 2, -1):
        c1 = lookback[i - 2]
        c3 = lookback[i]
        if c3["high"] < c1["low"]:
            g_low, g_high = c3["high"], c1["low"]
            dist = g_low - price
            if min_dist <= dist <= max_dist:
                filled = any(lookback[k]["close"] > g_high for k in range(i + 1, len(lookback)))
                if not filled:
                    sell_candidates.append((dist, "Fresh Bearish FVG", g_low, g_high))

        if c3["low"] > c1["high"]:
            g_low, g_high = c1["high"], c3["low"]
            dist = price - g_high
            if min_dist <= dist <= max_dist:
                filled = any(lookback[k]["close"] < g_low for k in range(i + 1, len(lookback)))
                if not filled:
                    buy_candidates.append((dist, "Fresh Bullish FVG", g_low, g_high))

    if sell_candidates:
        sell_candidates.sort(key=lambda x: x[0])
        _, s_label, s_low, s_high = sell_candidates[0]
    else:
        recent_high = max(c["high"] for c in lookback[-25:])
        target_high = min(recent_high, price + avg_range * 3.2)
        if target_high <= price + min_dist:
            target_high = price + avg_range * 1.4
        s_label = "Nearest Supply / Swing High"
        s_low = target_high - avg_range * 0.20
        s_high = target_high

    s_entry = round((s_low + s_high) / 2.0, 4)
    s_sl = s_high + avg_range * 0.90
    s_risk = max(s_sl - s_entry, avg_range * 0.5)
    sell_limit = {
        "label": s_label,
        "entry": s_entry,
        "zone_low": s_low,
        "zone_high": s_high,
        "sl": s_sl,
        "tp1": s_entry - s_risk * 1.5,
        "tp2": s_entry - s_risk * 2.5,
        "tp3": s_entry - s_risk * 3.5
    }

    if buy_candidates:
        buy_candidates.sort(key=lambda x: x[0])
        _, b_label, b_low, b_high = buy_candidates[0]
    else:
        recent_low = min(c["low"] for c in lookback[-25:])
        target_low = max(recent_low, price - avg_range * 3.2)
        if target_low >= price - min_dist:
            target_low = price - avg_range * 1.4
        b_label = "Nearest Demand / Swing Low"
        b_low = target_low
        b_high = target_low + avg_range * 0.20

    b_entry = round((b_low + b_high) / 2.0, 4)
    b_sl = b_low - avg_range * 0.90
    b_risk = max(b_entry - b_sl, avg_range * 0.5)
    buy_limit = {
        "label": b_label,
        "entry": b_entry,
        "zone_low": b_low,
        "zone_high": b_high,
        "sl": b_sl,
        "tp1": b_entry + b_risk * 1.5,
        "tp2": b_entry + b_risk * 2.5,
        "tp3": b_entry + b_risk * 3.5
    }

    return sell_limit, buy_limit


# ============================================================
# KALKULATOR LOT & ANALISA SINGLE TIMEFRAME
# ============================================================

def calculate_lot_recommendation(symbol, balance, risk_pct, entry_price, sl_price, rr=2.0):
    try:
        if not balance or not risk_pct or not sl_price or not entry_price:
            return None
        risk_usd = float(balance) * (float(risk_pct) / 100.0)
        sl_dist = abs(float(entry_price) - float(sl_price))
    except (TypeError, ValueError):
        return None
    if sl_dist <= 0 or risk_usd <= 0:
        return None

    contract_size = 100.0 if symbol == "XAUUSD" else 1.0
    raw_lot = risk_usd / (sl_dist * contract_size)

    lot = math.floor(raw_lot * 100 + 1e-9) / 100.0
    over_risk = False
    if lot < 0.01:
        lot = 0.01
        over_risk = (lot * sl_dist * contract_size) > risk_usd * 1.0001

    actual_loss = lot * sl_dist * contract_size
    return {
        "balance": float(balance),
        "risk_pct": float(risk_pct),
        "risk_usd": risk_usd,
        "lot": lot,
        "actual_loss": actual_loss,
        "tp2_profit": actual_loss * rr,
        "over_risk": over_risk
    }


def _worst_entry(result):
    if result["direction"] == "BUY":
        return result["entry_high"]
    if result["direction"] == "SELL":
        return result["entry_low"]
    return result["price"]


def _lot_text(lot_info):
    txt = f"{lot_info['lot']:.2f} Lot (SL -${lot_info['actual_loss']:,.2f})"
    if lot_info.get("over_risk"):
        txt += " ⚠️ lot minimum melebihi batas risiko"
    return txt


def analyze_market(symbol, timeframe):
    candles = get_candles(symbol, timeframe)
    if len(candles) < MIN_CANDLES_REQUIRED:
        raise ValueError(f"Candle kurang untuk analisa ({len(candles)} < {MIN_CANDLES_REQUIRED})")
    last = candles[-1]
    price = last["close"]

    ob, ob_type, ob_low, ob_high = detect_ob(candles)
    fvg, fvg_low, fvg_high = detect_fvg(candles)
    bos = detect_bos(candles)
    snr, snr_level = detect_snr(candles)
    sbr_rbs, sbr_rbs_type, sbr_rbs_level, sbr_rbs_phase = detect_sbr_rbs(candles)
    snd, snd_low, snd_high = detect_snd(candles)
    rbr_dbd, rbr_dbd_type, rbr_dbd_low, rbr_dbd_high = detect_rbr_dbd(candles)
    sweep_dir, sweep_desc = detect_liquidity_sweep(candles)
    sell_limit, buy_limit = find_tight_pending_limits(candles)
    session_info, limit_safe, limit_note = get_session_killzone_info(symbol)

    signals = {
        "OB": ob,
        "FVG": fvg,
        "BOS": bos,
        "SNR": snr,
        "SBR_RBS": sbr_rbs,
        "SND": snd,
        "RBR_DBD": rbr_dbd
    }

    buy_votes = sum(v == "BUY" for v in signals.values())
    sell_votes = sum(v == "SELL" for v in signals.values())

    if buy_votes > sell_votes:
        bias, lead, opp = "BUY", buy_votes, sell_votes
    elif sell_votes > buy_votes:
        bias, lead, opp = "SELL", sell_votes, buy_votes
    else:
        bias, lead, opp = "WAIT", max(buy_votes, sell_votes), min(buy_votes, sell_votes)

    if bias != "WAIT" and lead >= MIN_CONFIRMATIONS and (lead - opp) >= MIN_EDGE:
        direction = bias
        if lead >= 5:
            status = "SANGAT KUAT"
        else:
            status = "KUAT"
    else:
        direction = "WAIT"
        if bias != "WAIT" and lead >= MIN_CONFIRMATIONS and opp >= 2:
            status = "BELUM ADA SETUP INSTAN KUAT (SINYAL BERTENTANGAN)"
        else:
            status = "BELUM ADA SETUP INSTAN KUAT"
    confirmations = lead

    avg_range = _calc_avg_range(candles, 14)

    if direction == "BUY":
        entry_low = price
        entry_high = price + avg_range * 0.20
        sl = price - avg_range * 1.4
        risk = entry_high - sl
        tp1 = entry_high + risk
        tp2 = entry_high + risk * 2
        tp3 = entry_high + risk * 3
    elif direction == "SELL":
        entry_low = price - avg_range * 0.20
        entry_high = price
        sl = price + avg_range * 1.4
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
        "bias": bias,
        "buy_votes": buy_votes,
        "sell_votes": sell_votes,
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
        "ob_type": ob_type,
        "ob_low": ob_low,
        "ob_high": ob_high,
        "fvg_low": fvg_low,
        "fvg_high": fvg_high,
        "snr_level": snr_level,
        "sbr_rbs_type": sbr_rbs_type,
        "sbr_rbs_level": sbr_rbs_level,
        "sbr_rbs_phase": sbr_rbs_phase,
        "snd_low": snd_low,
        "snd_high": snd_high,
        "rbr_dbd_type": rbr_dbd_type,
        "rbr_dbd_low": rbr_dbd_low,
        "rbr_dbd_high": rbr_dbd_high,
        "session_info": session_info,
        "limit_safe": limit_safe,
        "limit_note": limit_note,
        "sweep_dir": sweep_dir,
        "sweep_desc": sweep_desc,
        "sell_limit": sell_limit,
        "buy_limit": buy_limit,
        "candles": candles
    }


def _format_pending_limits_section(result, risk_profile=None):
    sl_ord = result["sell_limit"]
    bl_ord = result["buy_limit"]
    badge = "✅" if result.get("limit_safe") else "⚠️"
    sym = result["symbol"]

    sell_lot_txt = ""
    buy_lot_txt = ""
    if risk_profile:
        s_lot = calculate_lot_recommendation(
            sym, risk_profile.get("balance"), risk_profile.get("risk_pct"),
            sl_ord["entry"], sl_ord["sl"], rr=2.5
        )
        b_lot = calculate_lot_recommendation(
            sym, risk_profile.get("balance"), risk_profile.get("risk_pct"),
            bl_ord["entry"], bl_ord["sl"], rr=2.5
        )
        if s_lot:
            sell_lot_txt = f" | Lot: {s_lot['lot']:.2f}" + (" ⚠️" if s_lot["over_risk"] else "")
        if b_lot:
            buy_lot_txt = f" | Lot: {b_lot['lot']:.2f}" + (" ⚠️" if b_lot["over_risk"] else "")

    return [
        "",
        f"🎯 SINYAL PENDING ORDER (JEMPUTAN — {result['timeframe']})",
        f"Status Sesi: {badge} {result.get('limit_note', '-')}",
        "",
        f"🔴 SELL LIMIT @ {format_price(sl_ord['entry'])} ({sl_ord['label']})",
        f"   Zona: {format_price(sl_ord['zone_low'])} - {format_price(sl_ord['zone_high'])}{sell_lot_txt}",
        f"   SL: {format_price(sl_ord['sl'])} | TP1: {format_price(sl_ord['tp1'])} | TP2: {format_price(sl_ord['tp2'])}",
        "",
        f"🟢 BUY LIMIT @ {format_price(bl_ord['entry'])} ({bl_ord['label']})",
        f"   Zona: {format_price(bl_ord['zone_low'])} - {format_price(bl_ord['zone_high'])}{buy_lot_txt}",
        f"   SL: {format_price(bl_ord['sl'])} | TP1: {format_price(bl_ord['tp1'])} | TP2: {format_price(bl_ord['tp2'])}"
    ]


def create_report(result, risk_profile=None, title_prefix="📊 ANALISA SMC SCALPING PRO"):
    direction = result["direction"]
    emoji = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}.get(direction, "⚪")

    ob_sig = result["signals"]["OB"]
    ob_text = (
        f"{ob_sig} ({result['ob_type']} @ {format_price(result['ob_low'])} - {format_price(result['ob_high'])})"
        if ob_sig != "NEUTRAL" and result["ob_low"] is not None else "NEUTRAL (Belum menyentuh OB segar)"
    )

    fvg_sig = result["signals"]["FVG"]
    fvg_text = (
        f"{fvg_sig} (@ {format_price(result['fvg_low'])} - {format_price(result['fvg_high'])})"
        if fvg_sig != "NEUTRAL" and result["fvg_low"] is not None else "NEUTRAL (Belum di zona FVG segar)"
    )

    snr_sig = result["signals"]["SNR"]
    snr_text = (
        f"{snr_sig} (Reject @ {format_price(result['snr_level'])})"
        if snr_sig != "NEUTRAL" and result["snr_level"] is not None else "NEUTRAL"
    )

    sbr_sig = result["signals"]["SBR_RBS"]
    sbr_phase = result.get("sbr_rbs_phase", "NONE")
    if sbr_sig != "NEUTRAL" and result["sbr_rbs_level"] is not None:
        sbr_text = f"✅ {sbr_sig} — BREAKOUT → RETEST → REJECT ({result['sbr_rbs_type']} @ {format_price(result['sbr_rbs_level'])})"
    elif sbr_phase == "BREAKOUT_WAIT_RETEST" and result["sbr_rbs_level"] is not None:
        sbr_text = f"⏳ BREAKOUT {result['sbr_rbs_type']} @ {format_price(result['sbr_rbs_level'])} (Tunggu Retest)"
    else:
        sbr_text = "NEUTRAL"

    snd_sig = result["signals"]["SND"]
    snd_text = (
        f"{snd_sig} (@ {format_price(result['snd_low'])} - {format_price(result['snd_high'])})"
        if snd_sig != "NEUTRAL" and result["snd_low"] is not None else "NEUTRAL"
    )

    rbr_sig = result["signals"]["RBR_DBD"]
    rbr_text = (
        f"{rbr_sig} ({result['rbr_dbd_type']} Base @ {format_price(result['rbr_dbd_low'])} - {format_price(result['rbr_dbd_high'])})"
        if rbr_sig != "NEUTRAL" and result["rbr_dbd_low"] is not None else "NEUTRAL"
    )

    lines = [
        title_prefix,
        "━━━━━━━━━━━━━━━━━━",
        f"Market: {result['symbol']} | TF: {result['timeframe']} (Independen)",
        f"Harga: {format_price(result['price'])}",
        f"Sesi: {result['session_info']}",
        f"Likuiditas: {result['sweep_desc']}",
        "",
        f"{emoji} ARAH INSTAN: {direction}",
        f"Status: {result['status']} ({result['confirmations']}/{result['total_indicators']} Konfirmasi)"
    ]

    if direction == "WAIT" and result.get("bias") in ("BUY", "SELL"):
        lines.append(
            f"Condong: {result['bias']} (BUY {result['buy_votes']} vs SELL {result['sell_votes']}) — belum cukup untuk eksekusi"
        )

    lines.extend([
        "",
        "📌 KONFIRMASI SMC & PRICE ACTION",
        f"• OB Segar: {ob_text}",
        f"• FVG Segar: {fvg_text}",
        f"• BOS: {result['signals']['BOS']}",
        f"• SNR: {snr_text}",
        f"• SBR/RBS: {sbr_text}",
        f"• SND Segar: {snd_text}",
        f"• RBR/DBD: {rbr_text}"
    ])

    if direction in ("BUY", "SELL"):
        lines.extend([
            "",
            "⚡ EKSEKUSI INSTAN (MARKET ORDER)",
            f"• Entry: {format_price(result['entry_low'])} - {format_price(result['entry_high'])}",
            f"• Stop Loss: {format_price(result['sl'])}",
            f"• TP1: {format_price(result['tp1'])} | TP2: {format_price(result['tp2'])} | TP3: {format_price(result['tp3'])}"
        ])

        if risk_profile:
            lot_info = calculate_lot_recommendation(
                result["symbol"],
                risk_profile.get("balance"),
                risk_profile.get("risk_pct"),
                _worst_entry(result),
                result["sl"]
            )
            if lot_info:
                lines.append(
                    f"💼 Lot Instan (${lot_info['balance']:,.0f} | Risk {lot_info['risk_pct']:.1f}%): "
                    f"{_lot_text(lot_info)}"
                )

    lines.extend(_format_pending_limits_section(result, risk_profile))

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Hapus pending limit menjelang buka sesi London/NY & rilis news US."
    ])

    return "\n".join(lines)


# ============================================================
# MULTI-TIMEFRAME CONFLUENCE PRO (6 TF — RINGKAS & MAYORITAS)
# ============================================================

def analyze_confluence_pro(symbol, risk_profile=None):
    res_h4 = analyze_market(symbol, "H4")
    res_h1 = analyze_market(symbol, "H1")
    res_m30 = analyze_market(symbol, "M30")
    res_m15 = analyze_market(symbol, "M15")
    res_m5 = analyze_market(symbol, "M5")
    res_m1 = analyze_market(symbol, "M1")

    all_res = [res_h4, res_h1, res_m30, res_m15, res_m5, res_m1]
    dirs = [r["direction"] for r in all_res]
    buy_tf = dirs.count("BUY")
    sell_tf = dirs.count("SELL")

    def icon(r):
        return {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}.get(r["direction"], "⚪")

    matriks_str = (
        f"H4 {icon(res_h4)} | H1 {icon(res_h1)} | M30 {icon(res_m30)} | "
        f"M15 {icon(res_m15)} | M5 {icon(res_m5)} | M1 {icon(res_m1)}"
    )

    if sell_tf > buy_tf:
        majority_dir = "SELL"
        verdict = f"🔴 SELL ⬇️ (Unggul {sell_tf}-{buy_tf})"
    elif buy_tf > sell_tf:
        majority_dir = "BUY"
        verdict = f"🟢 BUY ⬆️ (Unggul {buy_tf}-{sell_tf})"
    else:
        majority_dir = "WAIT"
        verdict = f"⚪ WAIT ⏸️ (Imbang {buy_tf}-{sell_tf})"

    lines = [
        f"⚡ CONFLUENCE 6-TF — {symbol}",
        "━━━━━━━━━━━━━━━━━━",
        f"💰 Harga: {format_price(res_m1['price'])} | {get_short_session_badge(symbol)}",
        f"🧭 Matriks: {matriks_str}",
        "",
        f"🏆 HASIL SINYAL: {verdict}"
    ]

    exec_res = None
    if majority_dir in ("BUY", "SELL"):
        priority_order = [res_m5, res_m15, res_m30, res_m1, res_h1, res_h4]
        for candidate in priority_order:
            if candidate["direction"] == majority_dir:
                exec_res = candidate
                break

    if exec_res is not None:
        exec_badge = "🔴 SELL ⬇️" if majority_dir == "SELL" else "🟢 BUY ⬆️"
        lines.extend([
            "",
            f"⚡ EKSEKUSI INSTAN : {exec_badge}",
            f"• Entry: {format_price(exec_res['entry_low'])} - {format_price(exec_res['entry_high'])}",
            f"• SL: {format_price(exec_res['sl'])}",
            f"• TP1: {format_price(exec_res['tp1'])} | TP2: {format_price(exec_res['tp2'])} | TP3: {format_price(exec_res['tp3'])}"
        ])

        if risk_profile:
            lot_info = calculate_lot_recommendation(
                symbol,
                risk_profile.get("balance"),
                risk_profile.get("risk_pct"),
                _worst_entry(exec_res),
                exec_res["sl"]
            )
            if lot_info:
                lines.append(f"• 💼 Lot: {_lot_text(lot_info)}")

    sl_ord = res_m15["sell_limit"]
    bl_ord = res_m15["buy_limit"]
    limit_status = "✅ Aman Pasang Limit" if res_m15.get("limit_safe") else "⚠️ Waspada Buka Sesi"

    lines.extend([
        "",
        "🎯 SETTING PENDING ORDER",
        f"• Status: {limit_status}",
        f"• 🔴 SELL LIMIT: {format_price(sl_ord['entry'])} (SL {format_price(sl_ord['sl'])} | TP {format_price(sl_ord['tp1'])})",
        f"• 🟢 BUY LIMIT: {format_price(bl_ord['entry'])} (SL {format_price(bl_ord['sl'])} | TP {format_price(bl_ord['tp1'])})",
        "━━━━━━━━━━━━━━━━━━"
    ])

    return "\n".join(lines)


# ============================================================
# AUTO-SCANNER 3 TF (H1 + M15 + M5)
# ============================================================

def check_triple_tf_signal(symbol):
    res_h1 = analyze_market(symbol, "H1")
    res_m15 = analyze_market(symbol, "M15")
    res_m5 = analyze_market(symbol, "M5")

    d_h1 = res_h1["direction"]
    d_m15 = res_m15["direction"]
    d_m5 = res_m5["direction"]

    if d_h1 in ("BUY", "SELL") and d_h1 == d_m15 == d_m5:
        if max(res_m15["confirmations"], res_m5["confirmations"]) >= 4:
            return {
                "symbol": symbol,
                "direction": d_h1,
                "h1": res_h1,
                "m15": res_m15,
                "m5": res_m5
            }
    return None


def format_auto_signal_alert(sig_bundle, risk_profile=None):
    sym = sig_bundle["symbol"]
    direction = sig_bundle["direction"]
    r_h1 = sig_bundle["h1"]
    r_m15 = sig_bundle["m15"]
    r_m5 = sig_bundle["m5"]

    emoji = "🟢" if direction == "BUY" else "🔴"

    lines = [
        f"🚨 SINYAL OTOMATIS 3-TF (H1 + M15 + M5) — {sym}",
        "━━━━━━━━━━━━━━━━━━",
        f"{emoji} ARAH KOMPAK: {direction} (3/3 TIMEFRAME SEARAH)",
        f"Harga: {format_price(r_m5['price'])}",
        f"Sesi: {r_m5['session_info']}",
        "",
        "📊 KEKUATAN KONFIRMASI SMC",
        f"• H1 (Tren Intraday): {r_h1['direction']} ({r_h1['confirmations']}/7 • {r_h1['status']})",
        f"• M15 (Zona Setup): {r_m15['direction']} ({r_m15['confirmations']}/7 • {r_m15['status']})",
        f"• M5 (Trigger Scalping): {r_m5['direction']} ({r_m5['confirmations']}/7 • {r_m5['status']})",
        "",
        "🎯 RENCANA EKSEKUSI (ACUAN M5)",
        f"• Entry: {format_price(r_m5['entry_low'])} - {format_price(r_m5['entry_high'])}",
        f"• Stop Loss: {format_price(r_m5['sl'])}",
        f"• TP1: {format_price(r_m5['tp1'])}",
        f"• TP2: {format_price(r_m5['tp2'])}",
        f"• TP3: {format_price(r_m5['tp3'])}"
    ]

    if risk_profile:
        lot_info = calculate_lot_recommendation(
            sym,
            risk_profile.get("balance"),
            risk_profile.get("risk_pct"),
            _worst_entry(r_m5),
            r_m5["sl"]
        )
        if lot_info:
            warn = " ⚠️ lot minimum melebihi batas risiko" if lot_info["over_risk"] else ""
            lines.extend([
                "",
                f"💼 Rekomendasi Lot (${lot_info['balance']:,.0f} | Risk {lot_info['risk_pct']:.1f}%): {lot_info['lot']:.2f} Lot{warn}"
            ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Notifikasi otomatis dikirim karena H1, M15, dan M5 searah."
    ])

    return "\n".join(lines)
