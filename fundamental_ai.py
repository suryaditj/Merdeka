# -*- coding: utf-8 -*-
import re
import time
import json
import html
from datetime import datetime
from config import (
    GEMINI_API_KEY, GEMINI_MODELS, WIB, BULAN_ID,
    SESSION, log, api_get, format_price, extract_raw_time,
    parse_event_dt_wib, format_wib_time
)

LAST_GEMINI_ERROR = ""
RESPONSE_CACHE = {}
CALENDAR_CACHE_TTL = 900   # 15 menit
MACRO_CACHE_TTL = 300      # 5 menit


def call_gemini_json(prompt_text):
    global LAST_GEMINI_ERROR

    if not GEMINI_API_KEY:
        LAST_GEMINI_ERROR = "GEMINI_API_KEY belum terbaca di env workflow."
        return None

    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 1400,
            "responseMimeType": "application/json"
        }
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            resp = SESSION.post(url, headers=headers, json=payload, timeout=18)
            if resp.status_code != 200:
                LAST_GEMINI_ERROR = f"Model {model_name} gagal (HTTP {resp.status_code})"
                continue

            body = resp.json()
            candidates = body.get("candidates") or []
            if not candidates:
                LAST_GEMINI_ERROR = f"Model {model_name} tidak mengembalikan jawaban."
                continue

            parts = candidates[0].get("content", {}).get("parts") or []
            raw_text = "".join(p.get("text", "") for p in parts).strip()
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text).strip()

            parsed = json.loads(raw_text)
            LAST_GEMINI_ERROR = ""
            return parsed
        except Exception as err:
            LAST_GEMINI_ERROR = f"Error koneksi Gemini ({model_name}): {str(err)[:100]}"

    return None


# ============================================================
# ANALISA MAKRO: USOIL & DXY (BAHASA AWAM + DAMPAK KE EMAS)
# ============================================================

def _fetch_macro_candles(symbol):
    """
    Mencoba mengambil candle H4 / H1 untuk USOIL atau DXY dari API.
    """
    symbol_aliases = {
        "USOIL": ["USOIL", "WTI", "CRUDEOIL", "XTIUSD"],
        "DXY": ["DXY", "USDX", "DX"]
    }
    candidates = symbol_aliases.get(symbol, [symbol])
    last_err = None

    for sym in candidates:
        for interval in ("4h", "1h", "1d"):
            try:
                data = api_get(f"/api/{sym}/ohlc", params={"interval": interval, "limit": 50})
                bars = data.get("bars") if isinstance(data, dict) else None
                if isinstance(bars, list) and len(bars) >= 10:
                    valid = []
                    for b in bars:
                        if all(b.get(k) is not None for k in ("open", "high", "low", "close")):
                            valid.append({
                                "open": float(b["open"]),
                                "high": float(b["high"]),
                                "low": float(b["low"]),
                                "close": float(b["close"])
                            })
                    if len(valid) >= 10:
                        return valid
            except Exception as err:
                last_err = err
                continue

    raise ValueError(f"Data harga {symbol} sementara tidak tersedia dari server ({last_err}).")


def get_macro_market_report(symbol, force_refresh=False):
    symbol = symbol.upper()
    cache_key = f"macro_{symbol}"
    now_ts = time.time()
    cached = RESPONSE_CACHE.get(cache_key)
    if not force_refresh and cached and (now_ts - cached[0] < MACRO_CACHE_TTL):
        return cached[1]

    candles = _fetch_macro_candles(symbol)
    cur_price = candles[-1]["close"]
    prev_close = candles[-6]["close"] if len(candles) >= 6 else candles[0]["close"]
    res_level = max(c["high"] for c in candles[-20:])
    sup_level = min(c["low"] for c in candles[-20:])

    is_bullish = cur_price >= prev_close
    now_wib = datetime.now(WIB)
    bulan = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    waktu_str = f"{now_wib.day:02d} {bulan} {now_wib.year} • {now_wib.strftime('%H:%M')} WIB"

    if symbol == "USOIL":
        arah_txt = "🟢 <b>NAIK / MENGUAT (Bullish)</b>" if is_bullish else "🔴 <b>TURUN / MELEMAH (Bearish)</b>"
        kondisi_skrg = (
            "Harga minyak dunia sedang menguat karena permintaan atau ketegangan pasokan energi. "
            "Kenaikan minyak berpotensi memicu kekhawatiran inflasi."
            if is_bullish else
            "Harga minyak dunia sedang dalam tekanan turun karena pasokan masih mencukupi. "
            "Penurunan minyak cenderung meredakan tekanan inflasi global."
        )
        text = (
            "🛢️ <b>ANALISA MARKET: USOIL (Minyak Mentah)</b>\n"
            f"🕒 Update: {waktu_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Arah Tren Harian:</b> {arah_txt}\n"
            f"💵 <b>Harga Saat Ini:</b> <code>${format_price(cur_price)}</code> / barel\n\n"
            "📍 <b>Batas Penting Hari Ini:</b>\n"
            f"• Batas Atas (Resisten): <code>${format_price(res_level)}</code>\n"
            f"• Batas Bawah (Support): <code>${format_price(sup_level)}</code>\n\n"
            "💡 <b>Penjelasan &amp; Dampak ke Emas (XAUUSD):</b>\n"
            f"{kondisi_skrg}\n\n"
            "• <b>Jika USOIL Naik Tajam:</b> Biaya energi &amp; barang ikut mahal (inflasi naik). Investor biasanya memborong <b>Emas (XAUUSD)</b> sebagai pelindung nilai, sehingga Emas terdorong <b>ikut NAIK</b>.\n"
            "• <b>Jika USOIL Turun:</b> Tekanan inflasi mereda, sehingga daya tarik Emas berkurang dan <b>XAUUSD cenderung tertahan atau koreksi TURUN</b> (kecuali ada konflik geopolitik mendadak)."
        )
    else:
        arah_txt = "🟢 <b>MENGUAT (Bullish)</b>" if is_bullish else "🔴 <b>MELEMAH (Bearish)</b>"
        kondisi_skrg = (
            f"Dolar AS sedang kuat. Selama DXY bertahan di atas <code>{format_price(sup_level)}</code> dan mengarah ke <code>{format_price(res_level)}</code>, "
            "harga <b>XAUUSD (Emas) dan BTCUSD cenderung tertekan turun</b>."
            if is_bullish else
            f"Dolar AS sedang melemah. Selama DXY tertahan di bawah <code>{format_price(res_level)}</code> dan mengarah ke <code>{format_price(sup_level)}</code>, "
            "harga <b>XAUUSD (Emas) dan BTCUSD berpeluang besar terdorong NAIK</b>."
        )
        text = (
            "💵 <b>ANALISA MARKET: DXY (Indeks Dolar AS)</b>\n"
            f"🕒 Update: {waktu_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Arah Tren Harian:</b> {arah_txt}\n"
            f"📈 <b>Posisi Indeks:</b> <code>{format_price(cur_price)}</code>\n\n"
            "📍 <b>Batas Penting Hari Ini:</b>\n"
            f"• Batas Atas (Resisten): <code>{format_price(res_level)}</code>\n"
            f"• Batas Bawah (Support): <code>{format_price(sup_level)}</code>\n\n"
            "💡 <b>Dampak ke Emas (XAUUSD) &amp; Crypto (BTCUSD):</b>\n"
            f"{kondisi_skrg}\n\n"
            "<i>Catatan Awam: DXY adalah 'lawan' utama XAUUSD. Jika DXY hijau/naik, Emas sering merah/turun — begitu pula sebaliknya.</i>"
        )

    RESPONSE_CACHE[cache_key] = (now_ts, text)
    return text


# ============================================================
# KALENDER EKONOMI US (WAKTU WIB + BAHASA AWAM)
# ============================================================

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
        return 3, "🔴 DAMPAK BESAR"
    if imp in ("medium", "moderate", "2", "orange", "yellow", "sedang"):
        return 2, "🟠 DAMPAK SEDANG"
    return 1, "🟡 DAMPAK KECIL"


def _analyze_calendar_with_gemini(events_payload):
    prompt = (
        "Analis fundamental Emas (XAUUSD) & Dolar AS untuk orang awam (bahasa Indonesia sehari-hari, singkat, mudah dipahami).\n"
        f"Data jadwal US: {json.dumps(events_payload, ensure_ascii=False)}\n"
        "Jika 'actual' ada isi, bandingkan vs 'forecast' (NAIK/TURUN/DATAR). Jika 'actual' kosong, jelaskan arah emas jika angka aktual lebih tinggi vs lebih rendah.\n"
        "Kembalikan JSON array urut index:\n"
        '[{"index":0,"title_id":"Terjemahan sederhana","summary_awam":"1 kalimat singkat artinya bagi trader","is_released":false,"released_direction":"NAIK|TURUN|DATAR","released_reason":"Singkat","if_higher_dir":"TURUN|NAIK","if_lower_dir":"NAIK|TURUN"}]'
    )
    res = call_gemini_json(prompt)
    if isinstance(res, list):
        return {item.get("index"): item for item in res if isinstance(item, dict)}
    return {}


def format_calendar_data(data):
    items = _extract_list_payload(data)
    if items is None:
        return "<b>📰 BERITA EKONOMI US (WIB)</b>\n\nFormat data tidak dikenali."
    if not items:
        return "<b>📰 BERITA EKONOMI US (WIB)</b>\n\nBelum ada jadwal rilis data ekonomi US."

    us_items = [
        item for item in items
        if isinstance(item, dict) and str(
            item.get("country") or item.get("currency") or item.get("ccy") or item.get("code") or ""
        ).strip().upper() in ("US", "USA", "USD", "UNITED STATES", "UNITED STATES OF AMERICA")
    ]
    filtered = us_items if us_items else [i for i in items if isinstance(i, dict)]
    if not filtered:
        return "<b>📰 BERITA EKONOMI US (WIB)</b>\n\nTidak ada jadwal ekonomi US ditemukan."

    now_wib = datetime.now(WIB)
    today_start_wib = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)

    upcoming_or_today, all_parsed = [], []
    for item in filtered:
        title_en = str(
            item.get("title") or item.get("event") or item.get("name") or item.get("indicator") or "Rilis Ekonomi US"
        ).strip()
        raw_impact = item.get("impact") or item.get("importance") or item.get("volatility") or ""
        dt_wib = parse_event_dt_wib(extract_raw_time(item))
        rank, badge = _impact_rank_and_badge(raw_impact, title_en)
        entry = (dt_wib, rank, badge, title_en, item)
        all_parsed.append(entry)
        if dt_wib is None or dt_wib >= today_start_wib:
            upcoming_or_today.append(entry)

    active_list = upcoming_or_today if upcoming_or_today else all_parsed
    active_list.sort(
        key=lambda x: (
            x[0].date() if x[0] else datetime.max.date(),
            -x[1],
            x[0] if x[0] else datetime.max.replace(tzinfo=WIB)
        )
    )

    top_events = active_list[:6]
    gemini_input = [
        {
            "index": idx,
            "title": title_en,
            "actual": str(item.get("actual") or ""),
            "forecast": str(item.get("forecast") or item.get("consensus") or item.get("estimate") or "")
        }
        for idx, (_, _, _, title_en, item) in enumerate(top_events)
    ]

    ai_map = _analyze_calendar_with_gemini(gemini_input)

    bulan_sekarang = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    tgl_hari_ini = f"{now_wib.day:02d} {bulan_sekarang} {now_wib.year}"

    header = (
        f"📰 <b>JADWAL BERITA EKONOMI US (WIB)</b>\n"
        f"📅 Mulai {tgl_hari_ini} (Waktu Indonesia Barat)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Peringatan Manajemen Risiko:</b>\n"
        "<i>Amankan posisi (pasang SL+ atau kurangi lot) 15 menit sebelum jam rilis berita Dampak Besar di atas karena pergerakan harga biasanya sangat cepat.</i>"
    )

    body_blocks = []
    current_len = len(header) + len(footer)

    for idx, (dt_wib, rank, badge, title_en, item) in enumerate(top_events):
        ai_info = ai_map.get(idx, {})
        title_id = str(ai_info.get("title_id") or title_en).strip()
        display_title = (
            f"{html.escape(title_id)} <i>({html.escape(title_en)})</i>"
            if title_id.lower() != title_en.lower()
            else html.escape(title_en)
        )

        time_wib = html.escape(format_wib_time(extract_raw_time(item)))
        is_today_tag = " <b>[HARI INI]</b>" if (dt_wib and dt_wib.date() == now_wib.date()) else ""

        actual = item.get("actual")
        forecast = item.get("forecast") or item.get("consensus") or item.get("estimate")
        previous = item.get("previous") or item.get("prior")

        act_str = html.escape(str(actual)) if actual not in (None, "") else "⏳ Belum keluar"
        fc_str = html.escape(str(forecast)) if forecast not in (None, "") else "-"
        prev_str = html.escape(str(previous)) if previous not in (None, "") else "-"

        if ai_info:
            summary_awam = html.escape(str(ai_info.get("summary_awam") or ""))
            if ai_info.get("is_released") and actual not in (None, ""):
                r_dir = str(ai_info.get("released_direction") or "DATAR").upper()
                r_reason = html.escape(str(ai_info.get("released_reason") or ""))
                if r_dir == "NAIK":
                    dir_label = "🟢 Emas (XAUUSD) cenderung <b>NAIK</b>"
                elif r_dir == "TURUN":
                    dir_label = "🔴 Emas (XAUUSD) cenderung <b>TURUN</b>"
                else:
                    dir_label = "⚪ Emas (XAUUSD) cenderung <b>DATAR</b>"
                xau_note = f"{summary_awam}\n📊 <b>Hasil Keluar:</b> {dir_label} — {r_reason}"
            else:
                hi_dir = str(ai_info.get("if_higher_dir") or "TURUN").upper()
                lo_dir = str(ai_info.get("if_lower_dir") or "NAIK").upper()
                hi_fmt = "🟢 <b>NAIK</b>" if hi_dir == "NAIK" else "🔴 <b>TURUN</b>"
                lo_fmt = "🟢 <b>NAIK</b>" if lo_dir == "NAIK" else "🔴 <b>TURUN</b>"
                xau_note = (
                    f"{summary_awam}\n"
                    f"💡 <b>Artinya bagi Trader:</b>\n"
                    f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas {hi_fmt}\n"
                    f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas {lo_fmt}"
                )
        else:
            inverse_usd = any(k in title_en.lower() for k in ("unemployment", "jobless claims", "layoff"))
            if inverse_usd:
                xau_note = (
                    "💡 <b>Artinya bagi Trader:</b>\n"
                    "• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Dolar melemah, Emas 🟢 <b>NAIK</b>\n"
                    "• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Dolar menguat, Emas 🔴 <b>TURUN</b>"
                )
            else:
                xau_note = (
                    "💡 <b>Artinya bagi Trader:</b>\n"
                    "• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Dolar menguat, Emas 🔴 <b>TURUN</b>\n"
                    "• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Dolar melemah, Emas 🟢 <b>NAIK</b>"
                )

        block = (
            f"\n<b>{idx + 1}. {display_title}</b>\n"
            f"🕒 <code>{time_wib}</code> | {badge}{is_today_tag}\n"
            f"📊 Aktual: <b>{act_str}</b> | Perkiraan: <b>{fc_str}</b> | Lalu: <b>{prev_str}</b>\n"
            f"<blockquote>{xau_note}</blockquote>\n"
        )
        if current_len + len(block) > 3850:
            break
        body_blocks.append(block)
        current_len += len(block)

    return header + "".join(body_blocks) + footer


def get_cached_calendar(force_refresh=False):
    now_ts = time.time()
    cached = RESPONSE_CACHE.get("calendar")
    if not force_refresh and cached and (now_ts - cached[0] < CALENDAR_CACHE_TTL):
        return cached[1]
    raw_result = api_get("/api/calendar")
    formatted = format_calendar_data(raw_result)
    RESPONSE_CACHE["calendar"] = (now_ts, formatted)
    return formatted
