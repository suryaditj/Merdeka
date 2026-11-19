# -*- coding: utf-8 -*-
import os
import json
import math
from datetime import datetime
from config import (
    SIGNALS_FILE, WIB, BULAN_ID, log,
    format_price, github_sync_json_async
)

SIGNALS_DB = {"records": []}


# ============================================================
# 1. ZONA SESI MARKET (WIB) & PANDUAN STRATEGI SMC
# ============================================================

def get_current_wib_session():
    now = datetime.now(WIB)
    t = now.hour * 60 + now.minute

    if 6 * 60 <= t < 7 * 60:
        return (
            "AKTIVITAS AWAL ASIA (06.00 – 07.00 WIB)",
            "Sebagian broker baru membuka perdagangan kembali. Likuiditas masih tipis, sebaiknya amati arah awal dan tunggu spread stabil."
        )
    if 7 * 60 <= t < 8 * 60:
        return (
            "MENJELANG AKTIVITAS TOKYO (07.00 – 08.00 WIB)",
            "Fase persiapan sebelum bursa utama Tokyo buka pukul 08.00 WIB. Belum ada dorongan volume besar."
        )
    if 8 * 60 <= t < 14 * 60:
        if 8 * 60 <= t < 10 * 60:
            fase = "AWAL TOKYO & ASIA MULAI RAMAI (08.00 – 10.00 WIB)"
        elif 10 * 60 <= t < 12 * 60:
            fase = "PERGERAKAN SESI ASIA (10.00 – 12.00 WIB)"
        else:
            fase = "SESI UTAMA ASIA / TOKYO (08.00 – 14.00 WIB)"
        return (
            fase,
            "Rentang waktu pembentukan batas Asia High dan Asia Low. Tandai titik tertinggi dan terendah sesi ini sebagai target likuiditas saat London buka nanti."
        )
    if 14 * 60 <= t < 15 * 60:
        return (
            "TRANSISI PRE-LONDON (14.00 – 15.00 WIB)",
            "Batas Asia High dan Asia Low (08.00–14.00 WIB) sudah terbentuk. Bersiap mengamati pergerakan awal menjelang London Open pukul 15.00 WIB."
        )
    if 15 * 60 <= t < 20 * 60:
        return (
            "PEMBUKAAN & SESI LONDON (15.00 – 20.00 WIB)",
            "London sudah aktif sejak pukul 15.00 WIB. Amati potensi Liquidity Sweep (sapuan ekor) terhadap Asia High / Asia Low sebelum harga menentukan tren utama."
        )
    if t >= 20 * 60 or t < 5 * 60:
        return (
            "PEMBUKAAN & SESI NEW YORK (20.00 – 05.00 WIB)",
            "Sesi Amerika aktif sejak pukul 20.00 WIB dengan volatilitas tertinggi harian serta jadwal rilis berita ekonomi US. Wajib disiplin dengan Stop Loss."
        )
    return (
        "PERGANTIAN HARI BROKER / ROLLOVER (05.00 – 06.00 WIB)",
        "Pergantian candle harian (Daily Open). Spread broker sempat melebar, tunggu hingga pukul 06.00 WIB."
    )


def build_session_report():
    now = datetime.now(WIB)
    bulan = BULAN_ID.get(now.month, now.strftime("%b"))
    waktu_str = f"{now.day:02d} {bulan} {now.year} • {now.strftime('%H:%M')} WIB"
    status_aktif, catatan_strategi = get_current_wib_session()

    return (
        "⏰ <b>JADWAL &amp; STATUS SESI MARKET (WIB)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 Waktu Sekarang: <b>{waktu_str}</b>\n"
        f"🟢 Status Aktif: <b>{status_aktif}</b>\n\n"
        "📋 <b>Patokan Jam Sesi &amp; Strategi SMC (WIB):</b>\n"
        "• <b>06.00 – 07.00 WIB:</b> Aktivitas awal Asia <i>(awal perdagangan kembali pada sejumlah broker)</i>.\n"
        "• <b>07.00 – 08.00 WIB:</b> Menjelang aktivitas Tokyo.\n"
        "• <b>08.00 WIB:</b> Patokan populer pembukaan utama Tokyo.\n"
        "• <b>08.00 – 10.00 WIB:</b> Asia mulai ramai.\n"
        "• <b>10.00 – 12.00 WIB:</b> Pergerakan sesi Asia <i>(08.00–14.00 WIB digunakan sebagai rentang pembentukan Asia High &amp; Asia Low)</i>.\n"
        "• <b>15.00 WIB:</b> Pembukaan London <i>(momen krusial mengamati sweep terhadap Asia High/Low)</i>.\n"
        "• <b>20.00 WIB:</b> Pembukaan New York <i>(puncak volatilitas &amp; berita ekonomi AS)</i>.\n\n"
        f"💡 <b>Catatan Strategi Saat Ini:</b>\n{catatan_strategi}\n\n"
        "<i>Catatan: XAUUSD diperdagangkan hampir 24 jam pada hari kerja, sehingga jam sesi di atas dipakai sebagai acuan masuknya likuiditas besar.</i>"
    )


# ============================================================
# 2. KALKULATOR LOT & RISIKO
# ============================================================

def calculate_lot_for_pair(symbol, balance, risk_pct, sl_points):
    risk_usd = float(balance) * (float(risk_pct) / 100.0)
    contract_size = 100.0 if symbol == "XAUUSD" else 1.0
    raw_lot = risk_usd / (float(sl_points) * contract_size)
    lot = math.floor(raw_lot * 100 + 1e-9) / 100.0
    over_risk = False
    if lot < 0.01:
        lot = 0.01
        over_risk = (lot * float(sl_points) * contract_size) > risk_usd * 1.0001
    actual_loss = lot * float(sl_points) * contract_size
    return lot, actual_loss, risk_usd, over_risk


def build_lot_calculator_guide(risk_profile=None):
    profil_txt = "<i>Belum diatur</i>"
    if risk_profile:
        bal = risk_profile.get("balance", 0)
        rp = risk_profile.get("risk_pct", 0)
        profil_txt = f"Modal <b>${bal:,.2f}</b> | Risiko <b>{rp:.1f}% (${bal * rp / 100:,.2f})</b>"

    return (
        "🧮 <b>KALKULATOR LOT &amp; MANAJEMEN RISIKO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Profil Risiko Tersimpan: {profil_txt}\n\n"
        "<b>1️⃣ Simpan Profil Otomatis (Muncul di Setiap Sinyal):</b>\n"
        "Ketik: <code>/setrisk MODAL PERSEN</code>\n"
        "Contoh: <code>/setrisk 500 2</code> <i>(Modal $500, risiko 2% per transaksi)</i>\n\n"
        "<b>2️⃣ Hitung Cepat Lot Manual:</b>\n"
        "Ketik: <code>/lot MARKET MODAL RISIKO% JARAK_SL</code>\n"
        "• Contoh Emas (SL 5 poin / 50 pips):\n"
        "  <code>/lot XAUUSD 200 2 5</code>\n"
        "• Contoh Bitcoin (SL 500 poin):\n"
        "  <code>/lot BTCUSD 200 2 500</code>\n\n"
        "<i>Rumus ini membantu kamu terhindar dari overlot dan menjaga ketahanan akun.</i>"
    )


# ============================================================
# 3. PENCATATAN & EVALUASI CANDLE MAJU UNTUK REKAP WINRATE
# ============================================================

def load_signals_db():
    global SIGNALS_DB
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                SIGNALS_DB = data
                return
        except Exception as err:
            log.warning("Gagal membaca %s: %s", SIGNALS_FILE, err)
    SIGNALS_DB = {"records": []}


def _parse_rec_timestamp(rec):
    if rec.get("created_ts"):
        return int(rec["created_ts"])
    try:
        dt = datetime.strptime(str(rec.get("id", ""))[:14], "%Y%m%d%H%M%S").replace(tzinfo=WIB)
        return int(dt.timestamp())
    except Exception:
        return 0


def record_new_signal(sig_data):
    now = datetime.now(WIB)
    now_ts = int(now.timestamp())
    entry_low = round(sig_data["entry_low"], 4)
    entry_high = round(sig_data["entry_high"], 4)
    entry_mid = round((entry_low + entry_high) / 2.0, 4)
    cur_price = float(sig_data.get("price", entry_mid))

    initial_triggered = (
        (sig_data["direction"] == "BUY" and cur_price <= entry_high)
        or (sig_data["direction"] == "SELL" and cur_price >= entry_low)
    )

    for rec in reversed(SIGNALS_DB["records"][-10:]):
        rec_ts = _parse_rec_timestamp(rec)
        if (
            rec.get("symbol") == sig_data["symbol"]
            and rec.get("mode") == sig_data["mode"]
            and rec.get("direction") == sig_data["direction"]
            and (now_ts - rec_ts) < 900
        ):
            return

    record = {
        "id": now.strftime("%Y%m%d%H%M%S"),
        "created_ts": now_ts,
        "time_wib": now.strftime("%d/%m %H:%M WIB"),
        "date_ymd": now.strftime("%Y-%m-%d"),
        "symbol": sig_data["symbol"],
        "mode": sig_data["mode"],
        "direction": sig_data["direction"],
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": entry_mid,
        "sl": round(sig_data["sl"], 4),
        "tp1": round(sig_data["tp1"], 4),
        "tp2": round(sig_data["tp2"], 4),
        "tp3": round(sig_data["tp3"], 4),
        "triggered": initial_triggered,
        "status": "OPEN"
    }

    SIGNALS_DB["records"].append(record)
    SIGNALS_DB["records"] = SIGNALS_DB["records"][-100:]
    github_sync_json_async(SIGNALS_FILE, SIGNALS_DB, f"chore: log signal {sig_data['symbol']} {sig_data['mode']}")


def evaluate_open_signals(candles_fetcher_fn):
    active_records = [r for r in SIGNALS_DB["records"] if r.get("status") in ("OPEN", "TP1", "TP2")]
    if not active_records:
        return

    candles_by_sym = {}
    for sym in ("XAUUSD", "BTCUSD"):
        if any(r.get("symbol") == sym for r in active_records):
            try:
                candles_by_sym[sym] = candles_fetcher_fn(sym, "M5", min_required=10)
            except Exception:
                pass

    changed = False
    for rec in active_records:
        sym = rec.get("symbol")
        candles = candles_by_sym.get(sym)
        if not candles:
            continue

        created_ts = _parse_rec_timestamp(rec)
        forward_candles = [c for c in candles if c["time"] >= (created_ts - 300)]
        if not forward_candles:
            forward_candles = [candles[-1]]

        d = rec.get("direction")
        e_low = float(rec.get("entry_low", rec.get("entry_mid", 0)))
        e_high = float(rec.get("entry_high", rec.get("entry_mid", 0)))
        sl = float(rec["sl"])
        tp1 = float(rec["tp1"])
        tp2 = float(rec["tp2"])
        tp3 = float(rec["tp3"])
        st = rec.get("status", "OPEN")

        triggered = bool(rec.get("triggered", True if "triggered" not in rec else False))

        for c in forward_candles:
            c_high = c["high"]
            c_low = c["low"]

            if not triggered:
                if d == "BUY" and c_low <= e_high:
                    triggered = True
                    rec["triggered"] = True
                    changed = True
                elif d == "SELL" and c_high >= e_low:
                    triggered = True
                    rec["triggered"] = True
                    changed = True

            if not triggered:
                continue

            if d == "BUY":
                if c_low <= sl and st == "OPEN":
                    st = "SL"
                    changed = True
                    break
                if c_high >= tp3 and st != "TP3":
                    st = "TP3"
                    changed = True
                elif c_high >= tp2 and st not in ("TP2", "TP3"):
                    st = "TP2"
                    changed = True
                elif c_high >= tp1 and st == "OPEN":
                    st = "TP1"
                    changed = True
            elif d == "SELL":
                if c_high >= sl and st == "OPEN":
                    st = "SL"
                    changed = True
                    break
                if c_low <= tp3 and st != "TP3":
                    st = "TP3"
                    changed = True
                elif c_low <= tp2 and st not in ("TP2", "TP3"):
                    st = "TP2"
                    changed = True
                elif c_low <= tp1 and st == "OPEN":
                    st = "TP1"
                    changed = True

        rec["status"] = st

    if changed:
        github_sync_json_async(SIGNALS_FILE, SIGNALS_DB, "chore: update signal winrate from forward candles")


def build_winrate_report():
    records = SIGNALS_DB.get("records", [])
    if not records:
        return (
            "📈 <b>REKAP AKURASI SINYAL (WINRATE)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Belum ada riwayat sinyal yang tercatat.\n\n"
            "<i>Setiap kali kamu atau member menekan analisa XAUUSD / BTCUSD, sinyal otomatis tercatat di sini untuk dipantau hasilnya.</i>"
        )

    tp_count = sum(1 for r in records if r.get("status") in ("TP1", "TP2", "TP3"))
    sl_count = sum(1 for r in records if r.get("status") == "SL")
    open_count = sum(1 for r in records if r.get("status") == "OPEN")
    closed_total = tp_count + sl_count
    winrate_pct = (tp_count / closed_total * 100.0) if closed_total > 0 else 0.0

    lines = [
        "📈 <b>REKAP AKURASI SINYAL (WINRATE)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Total Sinyal Tercatat: <b>{len(records)}</b>",
        f"• Kena Target (TP1–TP3): <b>🟢 {tp_count}</b>",
        f"• Kena Stop Loss (SL): <b>🔴 {sl_count}</b>",
        f"• Masih Berjalan (OPEN): <b>⏳ {open_count}</b>",
        f"• Persentase Akurasi: <b>{winrate_pct:.1f}%</b>",
        "",
        "📋 <b>8 Sinyal Terakhir (Waktu WIB):</b>"
    ]

    status_badge = {
        "TP3": "🏆 TP3",
        "TP2": "🎯 TP2",
        "TP1": "✅ TP1",
        "SL": "❌ SL",
        "OPEN": "⏳ OPEN"
    }

    for rec in reversed(records[-8:]):
        arah_icon = "🟢" if rec.get("direction") == "BUY" else "🔴"
        st_lbl = status_badge.get(rec.get("status"), "⏳ OPEN")
        lines.append(
            f"• <code>{rec.get('time_wib')}</code> | <b>{rec.get('symbol')}</b> ({rec.get('mode')})\n"
            f"  {arah_icon} {rec.get('direction')} @ {format_price(rec.get('entry_mid'))} ➔ <b>{st_lbl}</b>"
        )

    lines.append("\n<i>Data dievaluasi otomatis dari pergerakan harga sejak jam sinyal diterbitkan.</i>")
    return "\n".join(lines)
