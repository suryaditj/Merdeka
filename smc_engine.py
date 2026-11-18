# -*- coding: utf-8 -*-
from datetime import datetime
from config import (
    MARKETS, TIMEFRAMES, CANDLE_COUNT, FETCH_COUNT,
    WIB, log, api_get, format_price
)

# ============================================================
# CANDLE DATA PARSER (100 CLOSED CANDLES)
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
# RADAR SESI KILLZONES & LIQUIDITY SWEEP (BSL / SSL)
# ============================================================

def get_session_killzone_info(symbol):
    now_wib = datetime.now(WIB)
    hour = now_wib.hour
    jam_str = now_wib.strftime("%H:%M WIB")

    if 19 <= hour < 23:
        return f"🔥 PRIME KILLZONE ({jam_str}) — Overlap London & NY (Volume Institusi Tertinggi)"
    if 14 <= hour < 19:
        return f"⚡ LONDON SESSION ({jam_str}) — Likuiditas Mulai Meningkat"
    if 23 <= hour or hour < 2:
        return f"🇺🇸 NEW YORK SESSION ({jam_str}) — Volatilitas Aktif"
    if symbol == "BTCUSD":
        return f"🌐 CRYPTO 24/7 SESSION ({jam_str}) — Likuiditas Berjalan"
    return f"🌙 ASIA SESSION ({jam_str}) — Volume Tipis (Waspada Sideways Emas)"


def detect_liquidity_sweep(candles):
    """
    Mendeteksi sapuan likuiditas (BSL / SSL) dalam 5 candle terakhir
    terhadap swing high/low 30 candle sebelumnya.
    """
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
# 7 METODE ANALISA MURNI SMC
# ============================================================

def detect_ob(candles):
    recent = candles[-1]
    price = recent["close"]
    lookback = candles[-60:-1]
    if len(lookback) < 12:
        return "NEUTRAL", "-", None, None

    avg_range = sum(c["high"] - c["low"] for c in lookback) / len(lookback)
    if avg_range <= 0:
        avg_range = price * 0.001
    buffer = avg_range * 0.45

    for i in range(len(lookback) - 4, 4, -1):
        ob = lookback[i]
        c1 = lookback[i + 1]
        c2 = lookback[i + 2]
        ob_low, ob_high = ob["low"], ob["high"]

        prev_slice = lookback[max(0, i - 8):i]
        impulse_slice = lookback[i + 1: min(len(lookback), i + 4)]

        # Valid Bullish OB
        if ob["close"] < ob["open"]:
            c1_range = c1["high"] - c1["low"]
            c1_body = c1["close"] - c1["open"]
            strong_impulse = c1_range > 0 and c1_body >= c1_range * 0.55 and c1["close"] > ob_high
            has_fvg = c2["low"] > ob_high
            prev_swing_high = max(c["high"] for c in prev_slice)
            broke_structure = any(c["close"] > prev_swing_high for c in impulse_slice)

            if strong_impulse and has_fvg and broke_structure:
                invalidated = any(
                    lookback[k]["close"] < ob_low - (buffer * 0.5)
                    for k in range(i + 3, len(lookback))
                )
                if not invalidated and (ob_low - buffer <= price <= ob_high + buffer):
                    return "BUY", "Bullish OB", ob_low, ob_high

        # Valid Bearish OB
        if ob["close"] > ob["open"]:
            c1_range = c1["high"] - c1["low"]
            c1_body = c1["open"] - c1["close"]
            strong_impulse = c1_range > 0 and c1_body >= c1_range * 0.55 and c1["close"] < ob_low
            has_fvg = c2["high"] < ob_low
            prev_swing_low = min(c["low"] for c in prev_slice)
            broke_structure = any(c["close"] < prev_swing_low for c in impulse_slice)

            if strong_impulse and has_fvg and broke_structure:
                invalidated = any(
                    lookback[k]["close"] > ob_high + (buffer * 0.5)
                    for k in range(i + 3, len(lookback))
                )
                if not invalidated and (ob_low - buffer <= price <= ob_high + buffer):
                    return "SELL", "Bearish OB", ob_low, ob_high

    return "NEUTRAL", "-", None, None


def detect_fvg(candles):
    for i in range(len(candles) - 1, 1, -1):
        first = candles[i - 2]
        third = candles[i]
        if third["low"] > first["high"]:
            return "BUY", first["high"], third["low"]
        if third["high"] < first["low"]:
            return "SELL", third["high"], first["low"]
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
    avg_range = sum(c["high"] - c["low"] for c in previous) / len(previous)
    threshold = avg_range * 1.5
    if abs(price - support) <= threshold:
        return "BUY", support
    if abs(resistance - price) <= threshold:
        return "SELL", resistance
    return "NEUTRAL", None


def detect_sbr_rbs(candles):
    recent = candles[-1]
    price = recent["close"]
    lookback = candles[-60:-1]
    if len(lookback) < 10:
        return "NEUTRAL", "-", None

    avg_range = sum(c["high"] - c["low"] for c in lookback) / len(lookback)
    if avg_range <= 0:
        avg_range = price * 0.001
    threshold = avg_range * 1.2

    swing_highs, swing_lows = [], []
    for i in range(2, len(lookback) - 2):
        h, l = lookback[i]["high"], lookback[i]["low"]
        if h >= lookback[i - 1]["high"] and h >= lookback[i - 2]["high"] and h > lookback[i + 1]["high"] and h > lookback[i + 2]["high"]:
            swing_highs.append((i, h))
        if l <= lookback[i - 1]["low"] and l <= lookback[i - 2]["low"] and l < lookback[i + 1]["low"] and l < lookback[i + 2]["low"]:
            swing_lows.append((i, l))

    for idx, res_level in reversed(swing_highs):
        if any(lookback[k]["close"] > res_level for k in range(idx + 2, len(lookback))):
            if abs(price - res_level) <= threshold and price >= res_level - (threshold * 0.5):
                return "BUY", "RBS", res_level

    for idx, sup_level in reversed(swing_lows):
        if any(lookback[k]["close"] < sup_level for k in range(idx + 2, len(lookback))):
            if abs(price - sup_level) <= threshold and price <= sup_level + (threshold * 0.5):
                return "SELL", "SBR", sup_level

    return "NEUTRAL", "-", None


def detect_snd(candles):
    recent = candles[-1]
    previous = candles[-20:-1]
    price = recent["close"]
    demand = supply = None

    for i in range(1, len(previous)):
        cur, bef = previous[i], previous[i - 1]
        rng = cur["high"] - cur["low"]
        if rng <= 0:
            continue
        body = abs(cur["close"] - cur["open"])
        if cur["close"] > cur["open"] and body >= rng * 0.65 and cur["close"] > bef["high"]:
            demand = cur["low"]
        if cur["close"] < cur["open"] and body >= rng * 0.65 and cur["close"] < bef["low"]:
            supply = cur["high"]

    cur_rng = max(recent["high"] - recent["low"], price * 0.0001)
    if demand is not None and demand <= price <= demand + cur_rng * 2:
        return "BUY", demand
    if supply is not None and supply - cur_rng * 2 <= price <= supply:
        return "SELL", supply
    return "NEUTRAL", None


def detect_rbr_dbd(candles):
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

    def is_base(c):
        rng = c["high"] - c["low"]
        return rng <= 0 or (abs(c["close"] - c["open"]) / rng) <= 0.50

    for i in range(len(lookback) - 4, -1, -1):
        leg1 = lookback[i]
        for base_len in (1, 2):
            leg2_idx = i + base_len + 1
            if leg2_idx >= len(lookback):
                continue
            base_candles = lookback[i + 1: leg2_idx]
            leg2 = lookback[leg2_idx]
            if not all(is_base(bc) for bc in base_candles):
                continue

            z_low = min(bc["low"] for bc in base_candles)
            z_high = max(bc["high"] for bc in base_candles)

            if is_strong_bull(leg1) and is_strong_bull(leg2) and leg2["close"] > z_high:
                inv = any(lookback[k]["close"] < z_low - buffer for k in range(leg2_idx + 1, len(lookback)))
                if not inv and (z_low - buffer <= price <= z_high + buffer):
                    return "BUY", "RBR", z_low, z_high

            if is_strong_bear(leg1) and is_strong_bear(leg2) and leg2["close"] < z_low:
                inv = any(lookback[k]["close"] > z_high + buffer for k in range(leg2_idx + 1, len(lookback)))
                if not inv and (z_low - buffer <= price <= z_high + buffer):
                    return "SELL", "DBD", z_low, z_high

    return "NEUTRAL", "-", None, None


# ============================================================
# SINGLE TIMEFRAME ANALYSIS & LOT CALCULATOR
# ============================================================

def calculate_lot_recommendation(symbol, balance, risk_pct, entry_price, sl_price):
    if not balance or not risk_pct or not sl_price or not entry_price:
        return None
    risk_usd = float(balance) * (float(risk_pct) / 100.0)
    sl_dist = abs(float(entry_price) - float(sl_price))
    if sl_dist <= 0:
        return None

    # Kontrak standar forex/CFD: XAUUSD = 100 oz/lot, BTCUSD = 1 BTC/lot
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
    sbr_rbs, sbr_rbs_type, sbr_rbs_level = detect_sbr_rbs(candles)
    snd, snd_level = detect_snd(candles)
    rbr_dbd, rbr_dbd_type, rbr_dbd_low, rbr_dbd_high = detect_rbr_dbd(candles)
    sweep_dir, sweep_desc = detect_liquidity_sweep(candles)

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
        status = "BELUM ADA SETUP KUAT"
    elif confirmations in (3, 4):
        status = "KUAT"
    else:
        status = "SANGAT KUAT"

    recent = candles[-14:]
    avg_range = sum(c["high"] - c["low"] for c in recent) / len(recent)
    if avg_range <= 0:
        avg_range = price * 0.001

    if direction == "BUY":
        entry_low = price
        entry_high = price + avg_range * 0.20
        sl = price - avg_range * 1.5
        risk = entry_high - sl
        tp1 = entry_high + risk
        tp2 = entry_high + risk * 2
        tp3 = entry_high + risk * 3
    elif direction == "SELL":
        entry_low = price - avg_range * 0.20
        entry_high = price
        sl = price + avg_range * 1.5
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
        "sbr_rbs_type": sbr_rbs_type,
        "sbr_rbs_level": sbr_rbs_level,
        "rbr_dbd_type": rbr_dbd_type,
        "rbr_dbd_low": rbr_dbd_low,
        "rbr_dbd_high": rbr_dbd_high,
        "session_info": get_session_killzone_info(symbol),
        "sweep_dir": sweep_dir,
        "sweep_desc": sweep_desc
    }


def create_report(result, risk_profile=None, title_prefix="📊 ANALISA SMC SCALPING PRO"):
    direction = result["direction"]
    emoji = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}.get(direction, "⚪")

    ob_sig = result["signals"]["OB"]
    if ob_sig != "NEUTRAL" and result["ob_low"] is not None:
        ob_text = f"{ob_sig} ({result['ob_type']} @ {format_price(result['ob_low'])} - {format_price(result['ob_high'])})"
    else:
        ob_text = ob_sig

    sbr_sig = result["signals"]["SBR_RBS"]
    if sbr_sig != "NEUTRAL" and result["sbr_rbs_level"] is not None:
        sbr_text = f"{sbr_sig} ({result['sbr_rbs_type']} @ {format_price(result['sbr_rbs_level'])})"
    else:
        sbr_text = sbr_sig

    rbr_sig = result["signals"]["RBR_DBD"]
    if rbr_sig != "NEUTRAL" and result["rbr_dbd_low"] is not None:
        rbr_text = f"{rbr_sig} ({result['rbr_dbd_type']} @ {format_price(result['rbr_dbd_low'])} - {format_price(result['rbr_dbd_high'])})"
    else:
        rbr_text = rbr_sig

    lines = [
        title_prefix,
        "━━━━━━━━━━━━━━━━━━",
        f"Market: {result['symbol']} | TF: {result['timeframe']}",
        f"Harga: {format_price(result['price'])}",
        f"Sesi: {result['session_info']}",
        f"Likuiditas: {result['sweep_desc']}",
        "",
        f"{emoji} ARAH: {direction}",
        f"Status: {result['status']} ({result['confirmations']}/{result['total_indicators']} Konfirmasi)",
        "",
        "📌 KONFIRMASI SMC & PRICE ACTION",
        f"• OB Valid: {ob_text}",
        f"• FVG: {result['signals']['FVG']}",
        f"• BOS: {result['signals']['BOS']}",
        f"• SNR: {result['signals']['SNR']}",
        f"• SBR/RBS: {sbr_text}",
        f"• SND: {result['signals']['SND']}",
        f"• RBR/DBD: {rbr_text}"
    ]

    if direction in ("BUY", "SELL"):
        entry_mid = (result["entry_low"] + result["entry_high"]) / 2.0
        lines.extend([
            "",
            "🎯 RENCANA LEVEL",
            f"• Entry: {format_price(result['entry_low'])} - {format_price(result['entry_high'])}",
            f"• Stop Loss: {format_price(result['sl'])}",
            f"• TP1: {format_price(result['tp1'])}",
            f"• TP2: {format_price(result['tp2'])}",
            f"• TP3: {format_price(result['tp3'])}"
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
                lines.extend([
                    "",
                    f"💼 KALKULATOR LOT (Modal ${lot_info['balance']:,.0f} | Risk {lot_info['risk_pct']:.1f}%)",
                    f"• Ukuran Lot: {lot_info['lot']:.2f} Lot",
                    f"• Est. Risiko SL: -${lot_info['actual_loss']:,.2f} | Target TP2: +${lot_info['tp2_profit']:,.2f}"
                ])
        else:
            lines.append("\n💡 Atur modal otomatis ketik: /setrisk 500 1")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Skor = jumlah konfirmasi teknikal, bukan jaminan profit."
    ])

    return "\n".join(lines)


# ============================================================
# MULTI-TIMEFRAME CONFLUENCE PRO (H4 + H1 + M15)
# ============================================================

def analyze_confluence_pro(symbol, risk_profile=None):
    res_h4 = analyze_market(symbol, "H4")
    res_h1 = analyze_market(symbol, "H1")
    res_m15 = analyze_market(symbol, "M15")

    dirs = [res_h4["direction"], res_h1["direction"], res_m15["direction"]]
    buy_tf = dirs.count("BUY")
    sell_tf = dirs.count("SELL")

    if buy_tf == 3:
        verdict = "🟢 HIGH PROBABILITY BUY (3/3 TF SEARAH BULLISH)"
    elif sell_tf == 3:
        verdict = "🔴 HIGH PROBABILITY SELL (3/3 TF SEARAH BEARISH)"
    elif res_h4["direction"] == res_h1["direction"] and res_h4["direction"] in ("BUY", "SELL"):
        verdict = f"⚡ TREN UTAMA {res_h4['direction']} (H4 & H1 Searah, M15 {res_m15['direction']})"
    elif res_h4["direction"] in ("BUY", "SELL") and res_m15["direction"] in ("BUY", "SELL") and res_h4["direction"] != res_m15["direction"]:
        verdict = f"⚠️ COUNTER-TREND RISK (M15 {res_m15['direction']} melawan tren H4 {res_h4['direction']})"
    else:
        verdict = "⚪ BELUM ADA KESELARASAN KUAT (Tunggu Konfirmasi)"

    def badge(r):
        ic = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}.get(r["direction"], "⚪")
        return f"{ic} {r['direction']} ({r['confirmations']}/7 • {r['status']})"

    lines = [
        f"⚡ ANALISA CONFLUENCE PRO — {symbol}",
        "━━━━━━━━━━━━━━━━━━",
        f"Harga Saat Ini: {format_price(res_m15['price'])}",
        f"Sesi: {res_m15['session_info']}",
        f"Likuiditas M15: {res_m15['sweep_desc']}",
        "",
        "🧭 MATRIKS MULTI-TIMEFRAME",
        f"• H4 (Tren Besar): {badge(res_h4)}",
        f"• H1 (Struktur Intraday): {badge(res_h1)}",
        f"• M15 (Zona Eksekusi): {badge(res_m15)}",
        "",
        "🏆 KESIMPULAN CONFLUENCE",
        verdict
    ]

    # Gunakan level eksekusi dari M15 jika aktif, atau fallback ke H1
    exec_res = res_m15 if res_m15["direction"] in ("BUY", "SELL") else res_h1
    if exec_res["direction"] in ("BUY", "SELL"):
        entry_mid = (exec_res["entry_low"] + exec_res["entry_high"]) / 2.0
        lines.extend([
            "",
            f"🎯 RENCANA EKSEKUSI ({exec_res['timeframe']} — {exec_res['direction']})",
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
                    f"💼 Lot Rekomendasi (${lot_info['balance']:,.0f} | {lot_info['risk_pct']:.1f}%): "
                    f"{lot_info['lot']:.2f} Lot (SL -${lot_info['actual_loss']:,.2f})"
                )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "Gunakan kombinasi H4 + H1 sebagai arah utama sebelum entry M15."
    ])

    return "\n".join(lines)
