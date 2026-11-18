# -*- coding: utf-8 -*-
from datetime import datetime
from config import (
    MARKETS, TIMEFRAMES, CANDLE_COUNT, FETCH_COUNT,
    WIB, log, api_get, format_price
)

# ============================================================
# CANDLE DATA PARSER (100 CLOSED CANDLES — INDEPENDEN PER TF)
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
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(value)
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
            if any(item.get(k) is None for k in required):
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
            if candle["high"] < max(candle["open"], candle["close"]):
                continue
            if candle["low"] > min(candle["open"], candle["close"]):
                continue

            is_open = candle["is_open"]
            if is_open is True or str(is_open).lower() in ("true", "1"):
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
    hour = now_wib.hour
    minute = now_wib.minute
    total_min = hour * 60 + minute
    jam_str = now_wib.strftime("%H:%M WIB")

    # Pembagian Sesi Akurat (Winter Time WIB):
    # - USA / New York Session : 20:15 WIB - 03:59 WIB (termasuk jam 00:00 WIB)
    # - Sydney / Pre-Asia      : 04:00 WIB - 08:44 WIB
    # - Asia Utama (HK/SG/TKY) : 08:45 WIB - 14:44 WIB
    # - London / Eropa Session : 14:45 WIB - 20:14 WIB

    is_ny_open = (20 * 60 + 15) <= total_min <= (22 * 60 + 30)
    is_london_open = (14 * 60 + 45) <= total_min <= (16 * 60 + 30)
    is_asia_open = (8 * 60 + 45) <= total_min <= (9 * 60 + 30)

    if is_ny_open:
        return (
            f"🚨 NEW YORK OPEN ({jam_str}) — Volatilitas Ekstrem",
            False,
            "WASPADA NY OPEN (Tunggu Rejection M1/M5, Hindari Limit Buta)"
        )
    if (22 * 60 + 31) <= total_min <= (23 * 60 + 59) or hour < 4:
        return (
            f"🇺🇸 NEW YORK / USA SESSION ({jam_str}) — Likuiditas Amerika",
            True,
            "AMAN PASANG LIMIT (Pantau London Close 00:00 WIB)"
        )
    if is_london_open:
        return (
            f"⚠️ LONDON OPEN ({jam_str}) — Rawan Sapuan Likuiditas",
            False,
            "WASPADA LONDON OPEN (Gunakan Konfirmasi Rejection M1/M5)"
        )
    if 15 <= hour < 20:
        return (
            f"⚡ LONDON SESSION ({jam_str}) — Struktur Rapi",
            True,
            "AMAN PASANG LIMIT (Zona Struktur Efektif)"
        )
    if is_asia_open:
        return (
            f"🌏 ASIA OPEN ({jam_str}) — Likuiditas Penuh Asia",
            True,
            "AMAN PASANG LIMIT (Pantau Range Pagi)"
        )
    if 8 <= hour < 15:
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
    """
    1. Order Block (OB) Segar (Unmitigated):
    Mencari candle terakhir berlawanan arah sebelum dorongan impulsif yang belum pernah
    ditembus close candle berikutnya, lalu memeriksa apakah harga sekarang sedang
    menyentuh zona OB tersebut.
    """
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.30

    for i in range(len(lookback) - 3, 4, -1):
        ob = lookback[i]
        c1 = lookback[i + 1]
        ob_low, ob_high = ob["low"], ob["high"]

        # Bullish OB: candle turun diikuti dorongan naik kuat
        if ob["close"] < ob["open"]:
            c1_rng = c1["high"] - c1["low"]
            c1_body = c1["close"] - c1["open"]
            impulsive = c1_rng > 0 and c1_body >= c1_rng * 0.50 and c1["close"] > ob_high
            if impulsive:
                invalidated = any(lookback[k]["close"] < ob_low - buffer for k in range(i + 2, len(lookback)))
                if not invalidated:
                    in_zone = (ob_low - buffer <= price <= ob_high + buffer) or (last_low <= ob_high and price >= ob_low)
                    if in_zone:
                        return "BUY", "Bullish OB", ob_low, ob_high

        # Bearish OB: candle naik diikuti dorongan turun kuat
        if ob["close"] > ob["open"]:
            c1_rng = c1["high"] - c1["low"]
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
    """
    2. Fair Value Gap (FVG) Segar (Unmitigated):
    Hanya aktif jika ada FVG yang belum tertutup penuh oleh candle berikutnya DAN
    harga berjalan saat ini sedang berada/retest di dalam celah FVG tersebut.
    """
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.20

    for i in range(len(lookback) - 1, 2, -1):
        c1 = lookback[i - 2]
        c3 = lookback[i]

        # Bullish FVG (Gap Bawah)
        if c3["low"] > c1["high"]:
            gap_low = c1["high"]
            gap_high = c3["low"]
            filled = any(lookback[k]["close"] < gap_low for k in range(i + 1, len(lookback)))
            if not filled:
                if (gap_low - buffer <= price <= gap_high + buffer) or (last_low <= gap_high and price >= gap_low):
                    return "BUY", gap_low, gap_high

        # Bearish FVG (Gap Atas)
        if c3["high"] < c1["low"]:
            gap_low = c3["high"]
            gap_high = c1["low"]
            filled = any(lookback[k]["close"] > gap_high for k in range(i + 1, len(lookback)))
            if not filled:
                if (gap_low - buffer <= price <= gap_high + buffer) or (last_high >= gap_low and price <= gap_high):
                    return "SELL", gap_low, gap_high

    return "NEUTRAL", None, None


def detect_bos(candles):
    """
    3. Break of Structure (BOS):
    Mengecek penembusan struktur swing 15 candle terakhir dengan body close valid.
    """
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
    """
    4. Support & Resistance (SNR) dengan Konfirmasi Reject Nyata:
    Aktif jika harga menyentuh Support/Resistance 35 candle terakhir dan menunjukkan
    penolakan (ekor menusuk zona lalu close mental balik).
    """
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
    """
    5. Breakout -> Retest -> Reject (RBS & SBR):
    Mendeteksi urutan lengkap:
      Tahap 1: Body close menembus Swing High (RBS) atau Swing Low (SBR)
      Tahap 2: Harga berbalik menyentuh kembali level tembusan (Retest)
      Tahap 3: Candle menolak level tersebut (Reject) -> Sinyal BUY/SELL aktif
    """
    last = candles[-1]
    prev_c = candles[-2]
    price = last["close"]
    lookback = candles[-65:-1]
    if len(lookback) < 12:
        return "NEUTRAL", "-", None, "NONE"

    avg_range = _calc_avg_range(lookback, 25)
    zone_tol = avg_range * 0.55

    swing_highs, swing_lows = [], []
    for i in range(2, len(lookback) - 3):
        h, l = lookback[i]["high"], lookback[i]["low"]
        if h >= lookback[i - 1]["high"] and h >= lookback[i - 2]["high"] and h > lookback[i + 1]["high"] and h > lookback[i + 2]["high"]:
            swing_highs.append((i, h))
        if l <= lookback[i - 1]["low"] and l <= lookback[i - 2]["low"] and l < lookback[i + 1]["low"] and l < lookback[i + 2]["low"]:
            swing_lows.append((i, l))

    # Cek RBS (Resistance Become Support)
    for idx, res_level in reversed(swing_highs):
        break_bars = [k for k in range(idx + 1, len(lookback)) if lookback[k]["close"] > res_level]
        if not break_bars:
            continue
        first_break = break_bars[0]
        # Pastikan tidak jebol balik ke bawah secara permanen
        broken_back = any(lookback[k]["close"] < res_level - zone_tol for k in range(first_break + 1, len(lookback)))
        if broken_back:
            continue

        # Cek apakah sudah retest & reject di level res_level
        retested = (last["low"] <= res_level + zone_tol) or (prev_c["low"] <= res_level + zone_tol)
        rejected = price > res_level and (last["close"] > last["open"] or (last["close"] - last["low"]) > (last["high"] - last["close"]))
        if retested and rejected and abs(price - res_level) <= avg_range * 1.3:
            return "BUY", "RBS", res_level, "CONFIRMED"

        # Jika baru breakout dalam 3 candle terakhir & harga belum jauh -> tandai fase BREAKOUT
        if first_break >= len(lookback) - 3 and price > res_level:
            return "NEUTRAL", "RBS", res_level, "BREAKOUT_WAIT_RETEST"

    # Cek SBR (Support Become Resistance)
    for idx, sup_level in reversed(swing_lows):
        break_bars = [k for k in range(idx + 1, len(lookback)) if lookback[k]["close"] < sup_level]
        if not break_bars:
            continue
        first_break = break_bars[0]
        broken_back = any(lookback[k]["close"] > sup_level + zone_tol for k in range(first_break + 1, len(lookback)))
        if broken_back:
            continue

        retested = (last["high"] >= sup_level - zone_tol) or (prev_c["high"] >= sup_level - zone_tol)
        rejected = price < sup_level and (last["close"] < last["open"] or (last["high"] - last["close"]) > (last["close"] - last["low"]))
        if retested and rejected and abs(sup_level - price) <= avg_range * 1.3:
            return "SELL", "SBR", sup_level, "CONFIRMED"

        if first_break >= len(lookback) - 3 and price < sup_level:
            return "NEUTRAL", "SBR", sup_level, "BREAKOUT_WAIT_RETEST"

    return "NEUTRAL", "-", None, "NONE"


def detect_snd(candles):
    """
    6. Supply & Demand (SND) Segar (Unmitigated):
    Hanya mengambil zona Demand/Supply segar yang belum pernah dijebol close berikutnya
    dan sedang disentuh oleh harga berjalan.
    """
    price = candles[-1]["close"]
    last_low = candles[-1]["low"]
    last_high = candles[-1]["high"]
    lookback = candles[-45:-1]
    avg_range = _calc_avg_range(lookback, 25)
    buffer = avg_range * 0.35

    for i in range(len(lookback) - 2, 1, -1):
        cur = lookback[i]
        bef = lookback[i - 1]
        rng = cur["high"] - cur["low"]
        if rng <= 0:
            continue
        body = abs(cur["close"] - cur["open"])

        # Fresh Demand Base
        if cur["close"] > cur["open"] and body >= rng * 0.60 and cur["close"] > bef["high"]:
            d_low = bef["low"]
            d_high = max(bef["high"], cur["open"])
            broken = any(lookback[k]["close"] < d_low - buffer for k in range(i + 1, len(lookback)))
            if not broken and ((d_low - buffer <= price <= d_high + buffer) or (last_low <= d_high and price >= d_low)):
                return "BUY", d_low, d_high

        # Fresh Supply Base
        if cur["close"] < cur["open"] and body >= rng * 0.60 and cur["close"] < bef["low"]:
            s_low = min(bef["low"], cur["open"])
            s_high = bef["high"]
            broken = any(lookback[k]["close"] > s_high + buffer for k in range(i + 1, len(lookback)))
            if not broken and ((s_low - buffer <= price <= s_high + buffer) or (last_high >= s_low and price <= s_high)):
                return "SELL", s_low, s_high

    return "NEUTRAL", None, None


def detect_rbr_dbd(candles):
    """
    7. RBR (Rally-Base-Rally) & DBD (Drop-Base-Drop) Realistis (1-4 Candle Base):
    - DBD: Candle turun panjang -> 1 s/d 4 candle base (koreksi kecil/naik tipis) -> candle turun panjang menembus base.
    - RBR: Candle naik panjang -> 1 s/d 4 candle base (koreksi kecil/turun tipis) -> candle naik panjang menembus base.
    - Hanya zona segar (unmitigated) yang belum pernah dijebol close berikutnya.
    """
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

            # RBR: Rally -> Base (1-4 candle) -> Rally menembus z_high
            if is_impulse_bull(leg1) and is_impulse_bull(leg2) and leg2["close"] > z_high:
                invalidated = any(lookback[k]["close"] < z_low - buffer for k in range(leg2_idx + 1, len(lookback)))
                if not invalidated:
                    in_zone = (z_low - buffer <= price <= z_high + buffer) or (last["low"] <= z_high and price >= z_low - buffer)
                    if in_zone:
                        return "BUY", "RBR", z_low, z_high

            # DBD: Drop -> Base (1-4 candle) -> Drop menembus z_low
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
    """
    Mencari zona segar terdekat di atas harga (SELL LIMIT) dan di bawah harga (BUY LIMIT)
    dengan jarak pip terbatas (maksimal 4x avg_range) agar realistis cepat dijemput.
    """
    price = candles[-1]["close"]
    lookback = candles[-70:-1]
    avg_range = _calc_avg_range(candles, 20)

    min_dist = avg_range * 0.45
    max_dist = avg_range * 4.00

    sell_candidates = []
    buy_candidates = []

    # 1. Kandidat dari Unmitigated OB & Unmitigated Base (RBR/DBD) dalam jarak 0.45x - 4x avg_range
    for i in range(len(lookback) - 2, 2, -1):
        c = lookback[i]
        c1 = lookback[i + 1]

        # Zona Atas (Bearish OB / Supply Base) -> SELL LIMIT
        if c["close"] > c["open"] and c1["close"] < c["low"]:
            z_low, z_high = c["low"], c["high"]
            dist = z_low - price
            if min_dist <= dist <= max_dist:
                mitigated = any(lookback[k]["close"] > z_high for k in range(i + 2, len(lookback)))
                if not mitigated:
                    sell_candidates.append((dist, "Fresh Bearish OB", z_low, z_high))

        # Zona Bawah (Bullish OB / Demand Base) -> BUY LIMIT
        if c["close"] < c["open"] and c1["close"] > c["high"]:
            z_low, z_high = c["low"], c["high"]
            dist = price - z_high
            if min_dist <= dist <= max_dist:
                mitigated = any(lookback[k]["close"] < z_low for k in range(i + 2, len(lookback)))
                if not mitigated:
                    buy_candidates.append((dist, "Fresh Bullish OB", z_low, z_high))

    # 2. Kandidat dari Unmitigated FVG dalam jarak dekat
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

    # Pilih kandidat terdekat yang paling cepat dijemput
    if sell_candidates:
        sell_candidates.sort(key=lambda x: x[0])
        _, s_label, s_low, s_high = sell_candidates[0]
    else:
        # Fallback terdekat dari swing high dalam 25 candle terakhir (di-clamp maks 3.5x avg_range)
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

def calculate_lot_recommendation(symbol, balance, risk_pct, entry_price, sl_price):
    if not balance or not risk_pct or not sl_price or not entry_price:
        return None
    risk_usd = float(balance) * (float(risk_pct) / 100.0)
    sl_dist = abs(float(entry_price) - float(sl_price))
    if sl_dist <= 0:
        return None

    contract_size = 100.0 if symbol == "XAUUSD" else 1.0
    raw_lot = risk_usd / (sl_dist * contract_size)
    lot = max(0.01, round(raw_lot, 2))
    actual_loss = lot * sl_dist * contract_size
    return {
        "balance": float(balance),
        "risk_pct": float(risk_pct),
        "risk_usd": risk_usd,
        "lot": lot,
        "actual_loss": actual_loss,
        "tp2_profit": actual_loss * 2.0
    }


def analyze_market(symbol, timeframe):
    candles = get_candles(symbol, timeframe)
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
        direction = "BUY"
        confirmations = buy_votes
    elif sell_votes > buy_votes:
        direction = "SELL"
        confirmations = sell_votes
    else:
        direction = "WAIT"
        confirmations = max(buy_votes, sell_votes)

    if direction == "WAIT" or confirmations < 3:
        status = "BELUM ADA SETUP INSTAN KUAT"
    elif confirmations in (3, 4):
        status = "KUAT"
    else:
        status = "SANGAT KUAT"

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
        "buy_limit": buy_limit
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
            sl_ord["entry"], sl_ord["sl"]
        )
        b_lot = calculate_lot_recommendation(
            sym, risk_profile.get("balance"), risk_profile.get("risk_pct"),
            bl_ord["entry"], bl_ord["sl"]
        )
        if s_lot:
            sell_lot_txt = f" | Lot: {s_lot['lot']:.2f}"
        if b_lot:
            buy_lot_txt = f" | Lot: {b_lot['lot']:.2f}"

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
        f"Status: {result['status']} ({result['confirmations']}/{result['total_indicators']} Konfirmasi)",
        "",
        "📌 KONFIRMASI SMC & PRICE ACTION",
        f"• OB Segar: {ob_text}",
        f"• FVG Segar: {fvg_text}",
        f"• BOS: {result['signals']['BOS']}",
        f"• SNR: {snr_text}",
        f"• SBR/RBS: {sbr_text}",
        f"• SND Segar: {snd_text}",
        f"• RBR/DBD: {rbr_text}"
    ]

    if direction in ("BUY", "SELL"):
        entry_mid = (result["entry_low"] + result["entry_high"]) / 2.0
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
                entry_mid,
                result["sl"]
            )
            if lot_info:
                lines.append(
                    f"💼 Lot Instan (${lot_info['balance']:,.0f} | Risk {lot_info['risk_pct']:.1f}%): "
                    f"{lot_info['lot']:.2f} Lot (SL -${lot_info['actual_loss']:,.2f})"
                )

    lines.extend(_format_pending_limits_section(result, risk_profile))

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Hapus pending limit menjelang buka sesi London/NY & rilis news US."
    ])

    return "\n".join(lines)


# ============================================================
# MULTI-TIMEFRAME CONFLUENCE PRO (6 TF: H4 + H1 + M30 + M15 + M5 + M1)
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
    scalp_dirs = [res_h1["direction"], res_m15["direction"], res_m5["direction"]]

    if buy_tf >= 5:
        verdict = f"🟢 STRONG BULLISH CONFLUENCE ({buy_tf}/6 TF SEARAH BUY)"
    elif sell_tf >= 5:
        verdict = f"🔴 STRONG BEARISH CONFLUENCE ({sell_tf}/6 TF SEARAH SELL)"
    elif scalp_dirs == ["BUY", "BUY", "BUY"]:
        verdict = "⚡ HIGH PROBABILITY SCALP BUY (H1 + M15 + M5 Searah)"
    elif scalp_dirs == ["SELL", "SELL", "SELL"]:
        verdict = "⚡ HIGH PROBABILITY SCALP SELL (H1 + M15 + M5 Searah)"
    elif res_h4["direction"] in ("BUY", "SELL") and res_m5["direction"] in ("BUY", "SELL") and res_h4["direction"] != res_m5["direction"]:
        verdict = f"⚠️ COUNTER-TREND RISK (M5 {res_m5['direction']} melawan tren H4 {res_h4['direction']})"
    else:
        verdict = "⚪ BELUM ADA KESELARASAN KUAT (Gunakan Sinyal Pending Limit)"

    def badge(r):
        ic = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}.get(r["direction"], "⚪")
        return f"{ic} {r['direction']} ({r['confirmations']}/7 • {r['status']})"

    lines = [
        f"⚡ ANALISA CONFLUENCE PRO — {symbol}",
        "━━━━━━━━━━━━━━━━━━",
        f"Harga Saat Ini: {format_price(res_m1['price'])}",
        f"Sesi: {res_m1['session_info']}",
        f"Likuiditas M5: {res_m5['sweep_desc']}",
        "",
        "🧭 MATRIKS 6 TIMEFRAME (H4 • H1 • M30 • M15 • M5 • M1)",
        f"• H4  (Tren Besar): {badge(res_h4)}",
        f"• H1  (Struktur Intraday): {badge(res_h1)}",
        f"• M30 (Transisi Sesi): {badge(res_m30)}",
        f"• M15 (Zona Setup): {badge(res_m15)}",
        f"• M5  (Konfirmasi Entry): {badge(res_m5)}",
        f"• M1  (Mikro Trigger): {badge(res_m1)}",
        "",
        "🏆 KESIMPULAN CONFLUENCE",
        verdict
    ]

    exec_res = res_m5 if res_m5["direction"] in ("BUY", "SELL") else (
        res_m15 if res_m15["direction"] in ("BUY", "SELL") else res_h1
    )
    if exec_res["direction"] in ("BUY", "SELL"):
        entry_mid = (exec_res["entry_low"] + exec_res["entry_high"]) / 2.0
        lines.extend([
            "",
            f"⚡ EKSEKUSI INSTAN ({exec_res['timeframe']} — {exec_res['direction']})",
            f"• Entry: {format_price(exec_res['entry_low'])} - {format_price(exec_res['entry_high'])}",
            f"• Stop Loss: {format_price(exec_res['sl'])}",
            f"• TP1: {format_price(exec_res['tp1'])} | TP2: {format_price(exec_res['tp2'])} | TP3: {format_price(exec_res['tp3'])}"
        ])

        if risk_profile:
            lot_info = calculate_lot_recommendation(
                symbol,
                risk_profile.get("balance"),
                risk_profile.get("risk_pct"),
                entry_mid,
                exec_res["sl"]
            )
            if lot_info:
                lines.append(
                    f"💼 Lot Instan (${lot_info['balance']:,.0f} | {lot_info['risk_pct']:.1f}%): "
                    f"{lot_info['lot']:.2f} Lot (SL -${lot_info['actual_loss']:,.2f})"
                )

    # Sinyal Pending Limit dari M15
    lines.extend(_format_pending_limits_section(res_m15, risk_profile))

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Kombinasi terbaik: H4/H1 untuk arah tren, M15/M5/M1 untuk presisi entry."
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
    entry_mid = (r_m5["entry_low"] + r_m5["entry_high"]) / 2.0

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
            entry_mid,
            r_m5["sl"]
        )
        if lot_info:
            lines.extend([
                "",
                f"💼 Rekomendasi Lot (${lot_info['balance']:,.0f} | Risk {lot_info['risk_pct']:.1f}%): {lot_info['lot']:.2f} Lot"
            ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Notifikasi otomatis dikirim karena H1, M15, dan M5 searah."
    ])

    return "\n".join(lines)
