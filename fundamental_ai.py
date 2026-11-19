# -*- coding: utf-8 -*-
import html
import time
from datetime import datetime
from config import (
    GEMINI_API_KEY, GEMINI_MODELS, MACRO_MARKETS,
    WIB, BULAN_ID, log, api_get, get_http_session, format_price,
    extract_raw_time, parse_event_dt_wib, format_wib_time
)

CALENDAR_CACHE = {"ts": 0, "text": ""}
MACRO_CACHE = {}


def fetch_us_economic_calendar():
    endpoints = ["/api/calendar", "/api/economic-calendar", "/api/news"]
    raw_items = []
    for ep in endpoints:
        try:
            data = api_get(ep, retries=1)
            if isinstance(data, list):
                raw_items = data
            elif isinstance(data, dict):
                for key in ("events", "calendar", "data", "items", "news"):
                    if isinstance(data.get(key), list):
                        raw_items = data[key]
                        break
            if raw_items:
                break
        except Exception:
            continue

    if not raw_items:
        return []

    us_events = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        country = str(item.get("country", item.get("currency", ""))).upper()
        if country not in ("US", "USD", "UNITED STATES", "USA", ""):
            continue

        impact = str(item.get("impact", item.get("importance", "Medium"))).capitalize()
        title = str(item.get("title", item.get("event", item.get("name", "Berita Ekonomi US")))).strip()
        raw_t = extract_raw_time(item)
        dt_wib = parse_event_dt_wib(raw_t)

        us_events.append({
            "title": title,
            "impact": impact,
            "time_wib": format_wib_time(raw_t),
            "sort_ts": dt_wib.timestamp() if dt_wib else 0,
            "actual": item.get("actual", "-"),
            "forecast": item.get("forecast", "-"),
            "previous": item.get("previous", "-")
        })

    us_events.sort(key=lambda x: x["sort_ts"])
    return us_events[:8]


def _ask_gemini_summary(prompt_text):
    if not GEMINI_API_KEY:
        return ""
    sess = get_http_session()
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 350}
        }
        try:
            resp = sess.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    if parts and parts[0].get("text"):
                        return parts[0]["text"].strip()
        except Exception:
            continue
    return ""


def get_cached_calendar(force_refresh=False):
    now_ts = time.time()
    if not force_refresh and CALENDAR_CACHE["text"] and (now_ts - CALENDAR_CACHE["ts"] < 900):
        return CALENDAR_CACHE["text"]

    now_wib = datetime.now(WIB)
    bulan = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    waktu_str = f"{now_wib.day:02d} {bulan} {now_wib.year} • {now_wib.strftime('%H:%M')} WIB"

    events = fetch_us_economic_calendar()
    if not events:
        msg = (
            "📰 <b>KALENDER EKONOMI US (WIB)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 Update: <b>{waktu_str}</b>\n\n"
            "ℹ️ Belum ada rilis berita ekonomi US berdampak tinggi dalam jadwal terdekat.\n"
            "<i>Tetap waspadai lonjakan volatilitas saat pembukaan sesi New York pukul 20.00 WIB.</i>"
        )
        CALENDAR_CACHE["ts"] = now_ts
        CALENDAR_CACHE["text"] = msg
        return msg

    lines = [
        "📰 <b>KALENDER EKONOMI US (WIB)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🕒 Update: <b>{waktu_str}</b>\n"
    ]

    summary_input = []
    for ev in events:
        imp_icon = "🔴" if "high" in ev["impact"].lower() else ("🟠" if "med" in ev["impact"].lower() else "🟡")
        lines.append(
            f"{imp_icon} <b>{html.escape(ev['title'])}</b>\n"
            f"   ⏰ {html.escape(ev['time_wib'])}\n"
            f"   📊 Act: <code>{ev['actual']}</code> | Est: <code>{ev['forecast']}</code> | Prev: <code>{ev['previous']}</code>"
        )
        summary_input.append(f"{ev['title']} ({ev['time_wib']}, Impact: {ev['impact']})")

    ai_note = _ask_gemini_summary(
        "Berikan ringkasan singkat 2 kalimat bahasa Indonesia tentang dampak jadwal berita US berikut terhadap XAUUSD & BTCUSD:\n"
        + "\n".join(summary_input)
    )
    if ai_note:
        lines.append(f"\n🤖 <b>Catatan Dampak Pasar:</b>\n<i>{html.escape(ai_note)}</i>")

    final_text = "\n".join(lines)
    CALENDAR_CACHE["ts"] = now_ts
    CALENDAR_CACHE["text"] = final_text
    return final_text


def get_macro_market_report(symbol, force_refresh=False):
    sym = symbol.upper()
    if sym not in MACRO_MARKETS:
        raise ValueError("Simbol makro tidak dikenal")

    now_ts = time.time()
    cached = MACRO_CACHE.get(sym)
    if not force_refresh and cached and (now_ts - cached["ts"] < 180):
        return cached["text"]

    data = api_get(f"/api/{sym}/ohlc", params={"interval": "1h", "limit": 60})
    bars = data.get("bars", []) if isinstance(data, dict) else []
    valid = [b for b in bars if isinstance(b, dict) and b.get("close") is not None]
    if len(valid) < 10:
        raise ValueError(f"Data harga {sym} tidak mencukupi")

    cur = float(valid[-1]["close"])
    prev_24 = float(valid[-25]["close"]) if len(valid) >= 25 else float(valid[0]["close"])
    diff = cur - prev_24
    pct = (diff / prev_24 * 100.0) if prev_24 else 0.0
    high_24 = max(float(b["high"]) for b in valid[-24:])
    low_24 = min(float(b["low"]) for b in valid[-24:])

    arah = "🟢 MENGUAT (Bullish)" if diff >= 0 else "🔴 MELEMAH (Bearish)"
    if sym == "DXY":
        korelasi = (
            "DXY menguat cenderung menekan XAUUSD & BTCUSD turun."
            if diff >= 0 else
            "DXY melemah cenderung memberi dorongan naik bagi XAUUSD & BTCUSD."
        )
        icon = "💵"
        nama = "US DOLLAR INDEX (DXY)"
    else:
        korelasi = (
            "Kenaikan USOIL memicu ekspektasi inflasi yang sering menopang pergerakan XAUUSD."
            if diff >= 0 else
            "Penurunan USOIL meredakan tekanan inflasi jangka pendek pada pasar komoditas."
        )
        icon = "🛢️"
        nama = "CRUDE OIL (USOIL)"

    now_wib = datetime.now(WIB)
    bulan = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    waktu_str = f"{now_wib.day:02d} {bulan} {now_wib.year} • {now_wib.strftime('%H:%M')} WIB"

    report = (
        f"{icon} <b>MONITOR MAKRO: {nama}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 Update: <b>{waktu_str}</b>\n"
        f"💲 Harga Saat Ini: <b>{format_price(cur)}</b> ({pct:+.2f}%)\n"
        f"📈 Tren H1 (24 Jam): <b>{arah}</b>\n"
        f"📊 High / Low 24J: <code>{format_price(high_24)}</code> / <code>{format_price(low_24)}</code>\n\n"
        f"💡 <b>Korelasi ke XAUUSD &amp; BTCUSD:</b>\n{korelasi}"
    )

    MACRO_CACHE[sym] = {"ts": now_ts, "text": report}
    return report
