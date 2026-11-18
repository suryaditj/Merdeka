# -*- coding: utf-8 -*-
import re
import time
import json
import html
from datetime import datetime
from config import (
    GEMINI_API_KEY, GEMINI_MODELS, WIB, BULAN_ID,
    SESSION, log, api_get, extract_raw_time,
    parse_event_dt_wib, format_wib_time
)

LAST_GEMINI_ERROR = ""
RESPONSE_CACHE = {}
CALENDAR_CACHE_TTL = 900   # 15 menit
NEWS_CACHE_TTL = 600       # 10 menit


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
        return 3, "🔥 DAMPAK BESAR"
    if imp in ("medium", "moderate", "2", "orange", "yellow", "sedang"):
        return 2, "⚡ DAMPAK SEDANG"
    return 1, "🔹 DAMPAK KECIL"


def _analyze_calendar_with_gemini(events_payload):
    prompt = (
        "Analis fundamental Emas (XAUUSD) untuk pemula (bahasa Indonesia sehari-hari, singkat, jelas).\n"
        f"Data jadwal US: {json.dumps(events_payload, ensure_ascii=False)}\n"
        "Aturan: Bedakan penggerak utama emas (CPI/PPI/PCE, Fed/FOMC, NFP/Jobless, GDP, PMI) vs efek kecil ke emas (EIA Crude Oil dll).\n"
        "Jika 'actual' ada isi, bandingkan vs 'forecast' (NAIK/TURUN/DATAR + %). Jika 'actual' kosong, beri arah jika hasil lebih tinggi vs lebih rendah.\n"
        "Kembalikan JSON array urut index:\n"
        '[{"index":0,"title_id":"Terjemahan","gold_relevance":"HIGH|MEDIUM|LOW","summary_awam":"1 kalimat singkat efeknya ke emas","is_released":false,"released_direction":"NAIK|TURUN|DATAR","released_pct":"80%","released_reason":"Singkat","if_higher_dir":"TURUN|NAIK","if_lower_dir":"NAIK|TURUN","scenario_pct":"80%"}]'
    )
    res = call_gemini_json(prompt)
    if isinstance(res, list):
        return {item.get("index"): item for item in res if isinstance(item, dict)}
    return {}


def format_calendar_data(data):
    items = _extract_list_payload(data)
    if items is None:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nFormat data tidak dikenali."
    if not items:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nBelum ada jadwal rilis data."

    us_items = [
        item for item in items
        if isinstance(item, dict) and str(
            item.get("country") or item.get("currency") or item.get("ccy") or item.get("code") or ""
        ).strip().upper() in ("US", "USA", "USD", "UNITED STATES", "UNITED STATES OF AMERICA")
    ]
    filtered = us_items if us_items else [i for i in items if isinstance(i, dict)]
    if not filtered:
        return "<b>📅 KALENDER EKONOMI US</b>\n\nTidak ada jadwal ekonomi US ditemukan."

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
    ai_active = bool(ai_map)

    bulan_sekarang = BULAN_ID.get(now_wib.month, now_wib.strftime("%b"))
    tgl_hari_ini = f"{now_wib.day:02d} {bulan_sekarang} {now_wib.year}"
    mode_label = "Analisa AI Gemini" if ai_active else "Mode Standar"

    header = (
        f"<b>📅 JADWAL EKONOMI US (PANDUAN EMAS)</b>\n"
        f"<i>Mulai {tgl_hari_ini} (WIB) • {mode_label}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>⚠️ Hindari masuk posisi 15 menit sebelum &amp; sesudah berita Dampak Besar karena harga emas bergejolak cepat.</i>"
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

        rel = str(ai_info.get("gold_relevance") or "").upper()
        display_badge = {
            "HIGH": "🔥 DAMPAK BESAR KE EMAS",
            "MEDIUM": "⚡ DAMPAK SEDANG KE EMAS",
            "LOW": "🔹 DAMPAK KECIL KE EMAS"
        }.get(rel, badge)

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
                r_pct = html.escape(str(ai_info.get("released_pct") or "65%"))
                r_reason = html.escape(str(ai_info.get("released_reason") or ""))
                if r_dir == "NAIK":
                    dir_label = f"🟢 Emas berpotensi <b>NAIK 📈 (Peluang {r_pct})</b>"
                elif r_dir == "TURUN":
                    dir_label = f"🔴 Emas berpotensi <b>TURUN 📉 (Peluang {r_pct})</b>"
                else:
                    dir_label = f"⚪ Emas cenderung <b>DATAR ⚖️ (Peluang {r_pct})</b>"
                xau_note = f"{summary_awam}\n📊 <b>Hasil Keluar:</b> {dir_label} — {r_reason}"
            else:
                hi_dir = str(ai_info.get("if_higher_dir") or "TURUN").upper()
                lo_dir = str(ai_info.get("if_lower_dir") or "NAIK").upper()
                sc_pct = html.escape(str(ai_info.get("scenario_pct") or "70%"))
                hi_fmt = "🟢 <b>NAIK 📈</b>" if hi_dir == "NAIK" else "🔴 <b>TURUN 📉</b>"
                lo_fmt = "🟢 <b>NAIK 📈</b>" if lo_dir == "NAIK" else "🔴 <b>TURUN 📉</b>"
                xau_note = (
                    f"{summary_awam}\n"
                    f"💡 <b>Panduan Emas (Peluang {sc_pct}):</b>\n"
                    f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas {hi_fmt}\n"
                    f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas {lo_fmt}"
                )
        else:
            pct_fallback = {3: "80%", 2: "65%", 1: "55%"}.get(rank, "65%")
            inverse_usd = any(k in title_en.lower() for k in ("unemployment", "jobless claims", "layoff"))
            if inverse_usd:
                xau_note = (
                    f"💡 <b>Panduan Emas (Peluang {pct_fallback}):</b>\n"
                    f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas 🟢 <b>NAIK 📈</b>\n"
                    f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas 🔴 <b>TURUN 📉</b>"
                )
            else:
                xau_note = (
                    f"💡 <b>Panduan Emas (Peluang {pct_fallback}):</b>\n"
                    f"• Jika hasil <b>LEBIH TINGGI</b> dari perkiraan ➔ Emas 🔴 <b>TURUN 📉</b>\n"
                    f"• Jika hasil <b>LEBIH RENDAH</b> dari perkiraan ➔ Emas 🟢 <b>NAIK 📈</b>"
                )

        block = (
            f"\n<b>{idx + 1}. {display_title}</b>\n"
            f"🇺🇸 USD | {display_badge}{is_today_tag}\n"
            f"🕒 <code>{time_wib}</code>\n"
            f"📊 Hasil Asli: <b>{act_str}</b> | Perkiraan: <b>{fc_str}</b> | Lalu: <b>{prev_str}</b>\n"
            f"<blockquote>{xau_note}</blockquote>\n"
        )
        if current_len + len(block) > 3850:
            break
        body_blocks.append(block)
        current_len += len(block)

    return header + "".join(body_blocks) + footer


def _analyze_news_with_gemini(news_payload):
    prompt = (
        "Analis pasar untuk pemula (bahasa Indonesia sehari-hari, singkat, jelas).\n"
        f"Berita US: {json.dumps(news_payload, ensure_ascii=False)}\n"
        "Pilih maksimal 5 berita terpenting (utamakan penggerak Emas XAUUSD: The Fed, inflasi, Dolar AS, geopolitik). Tentukan efek ke emas (NAIK/TURUN/NETRAL + %).\n"
        "Kembalikan JSON array:\n"
        '[{"index":0,"is_gold_focus":true,"title_id":"Judul ID","issue_summary":"1 kalimat inti isu","gold_direction":"NAIK|TURUN|NETRAL","probability_pct":"75%","gold_reason":"Alasan singkat"}]'
    )
    res = call_gemini_json(prompt)
    if isinstance(res, list):
        return [item for item in res if isinstance(item, dict)]
    return []


def format_news_data(data):
    items = _extract_list_payload(data)
    if items is None:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nFormat data tidak dikenali."
    if not items:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nBelum ada berita terbaru."

    valid_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title_en = str(item.get("title") or item.get("headline") or item.get("name") or "").strip()
        if not title_en:
            continue
        summary_en = str(
            item.get("summary") or item.get("description") or item.get("snippet") or item.get("text") or ""
        ).strip()
        summary_en = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", summary_en)).strip()
        if len(summary_en) > 160:
            summary_en = summary_en[:157].rsplit(" ", 1)[0] + "..."
        valid_items.append((title_en, summary_en, item))

    if not valid_items:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nBelum ada berita valid."

    sample_items = valid_items[:7]
    gemini_input = [
        {"index": idx, "title": t, "snippet": s}
        for idx, (t, s, _) in enumerate(sample_items)
    ]

    ai_news_list = _analyze_news_with_gemini(gemini_input)
    ai_active = bool(ai_news_list)
    mode_label = "Analisa Isu AI Gemini" if ai_active else "Mode Standar"

    header = (
        f"<b>📰 BERITA MARKET US &amp; EFEK KE EMAS (XAUUSD)</b>\n"
        f"<i>Bahasa Indonesia Sederhana • {mode_label}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>💡 Berita bertanda <b>FOKUS EMAS</b> adalah isu yang paling berpengaruh pada pergerakan harga XAUUSD.</i>"
    )

    body_blocks = []
    current_len = len(header) + len(footer)

    if ai_active:
        for order_num, ai_item in enumerate(ai_news_list[:5]):
            orig_idx = ai_item.get("index", 0)
            if not isinstance(orig_idx, int) or orig_idx < 0 or orig_idx >= len(sample_items):
                orig_idx = min(order_num, len(sample_items) - 1)

            title_en, _, raw_item = sample_items[orig_idx]
            is_gold = bool(ai_item.get("is_gold_focus", False))
            title_id = html.escape(str(ai_item.get("title_id") or title_en).strip())
            issue_summary = html.escape(str(ai_item.get("issue_summary") or "").strip())
            direction = str(ai_item.get("gold_direction") or "NETRAL").upper()
            prob_pct = html.escape(str(ai_item.get("probability_pct") or "60%").strip())
            reason = html.escape(str(ai_item.get("gold_reason") or "").strip())

            source = html.escape(
                str(raw_item.get("source") or raw_item.get("publisher") or raw_item.get("site") or "Market Wire").strip()
            )
            time_wib = html.escape(format_wib_time(extract_raw_time(raw_item)))
            tag = "🥇 <b>FOKUS EMAS</b>" if is_gold else "🇺🇸 <b>BERITA US</b>"

            if direction == "NAIK":
                effect_line = f"🟢 <b>Efek ke Emas:</b> Berpotensi <b>NAIK 📈 (Peluang {prob_pct})</b> — {reason}"
            elif direction == "TURUN":
                effect_line = f"🔴 <b>Efek ke Emas:</b> Berpotensi <b>TURUN 📉 (Peluang {prob_pct})</b> — {reason}"
            else:
                effect_line = f"⚪ <b>Efek ke Emas:</b> Cenderung <b>NETRAL ({prob_pct})</b> — {reason}"

            summary_part = f"📌 <i>Inti Isu:</i> {issue_summary}\n" if issue_summary else ""
            block = (
                f"\n{tag} • <i>{source}</i>\n"
                f"<b>{order_num + 1}. {title_id}</b>\n"
                f"🕒 <code>{time_wib}</code>\n"
                f"<blockquote>{summary_part}{effect_line}</blockquote>\n"
            )
            if current_len + len(block) > 3850:
                break
            body_blocks.append(block)
            current_len += len(block)
    else:
        diag_note = html.escape(LAST_GEMINI_ERROR or "Pastikan GEMINI_API_KEY sudah diatur.")
        for idx, (title_en, summary_en, raw_item) in enumerate(sample_items[:5]):
            source = html.escape(
                str(raw_item.get("source") or raw_item.get("publisher") or raw_item.get("site") or "Market Wire").strip()
            )
            time_wib = html.escape(format_wib_time(extract_raw_time(raw_item)))
            block = (
                f"\n🇺🇸 <b>BERITA US</b> • <i>{source}</i>\n"
                f"<b>{idx + 1}. {html.escape(title_en)}</b>\n"
                f"🕒 <code>{time_wib}</code>\n"
                f"<blockquote>{html.escape(summary_en or 'Tidak ada ringkasan.')}\n"
                f"⚠️ <i>{diag_note}</i></blockquote>\n"
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


def get_cached_news(force_refresh=False):
    now_ts = time.time()
    cached = RESPONSE_CACHE.get("news")
    if not force_refresh and cached and (now_ts - cached[0] < NEWS_CACHE_TTL):
        return cached[1]
    raw_result = api_get("/api/news/market", params={"language": "en", "country": "US", "maxResults": 8})
    formatted = format_news_data(raw_result)
    RESPONSE_CACHE["news"] = (now_ts, formatted)
    return formatted
