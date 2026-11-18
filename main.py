# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT
# XAUUSD + BTCUSD
# TELEGRAM + BIQUOTE + GITHUB ACTIONS
#
# ANALISA CANDLE: 100% MURNI TEKNIKAL SMC (100 CLOSED CANDLES)
# OB VALID + FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD
#
# KALENDER EKONOMI US & BERITA MARKET:
# DIDUKUNG GEMINI AI FAST MODE + CACHE (KHUSUS ISU XAUUSD)
#
# MANAJEMEN AKSES MEMBER:
# FULL ADMIN COMMAND VIA TELEGRAM + AUTO-SYNC GITHUB
# ============================================================

import os
import re
import sys
import time
import json
import html
import base64
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
ENV_ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

USERS_FILE = "users.json"
BASE_URL = "https://biquote.io"

# Urutan model dari yang paling cepat (minimal thinking)
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash"
]

LAST_GEMINI_ERROR = ""

# Cache agar buka menu berulang langsung instan (< 0.1 detik)
RESPONSE_CACHE = {}
CALENDAR_CACHE_TTL = 900   # 15 menit
NEWS_CACHE_TTL = 600       # 10 menit

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
    "User-Agent": "Mozilla/5.0 SMC-Bot/3.0",
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
# USER ACCESS & GITHUB AUTO-SYNC MANAGER
# ============================================================

USERS_DB = {
    "admin_ids": [],
    "admin_username": "",
    "trial_limit": 2,
    "members": {},
    "trials": {}
}


def today_wib_date():
    return datetime.now(WIB).date()


def parse_date_ymd(date_str):
    try:
        return datetime.strptime(str(date_str).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def sync_users_to_github():
    """
    Menyimpan users.json secara lokal DAN langsung commit ke GitHub repo
    melalui GitHub API agar data member tetap tersimpan permanen saat
    GitHub Actions restart.
    """
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(USERS_DB, f, indent=2, ensure_ascii=False)
    except Exception as err:
        log.error("Gagal menulis %s lokal: %s", USERS_FILE, err)

    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return False, "Tersimpan lokal (GITHUB_TOKEN/REPO tidak tersedia)."

    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{USERS_FILE}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    try:
        sha = None
        get_resp = requests.get(api_url, headers=headers, timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        content_str = json.dumps(USERS_DB, indent=2, ensure_ascii=False) + "\n"
        encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        payload = {
            "message": "chore: update users.json via Telegram Admin",
            "content": encoded
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            log.info("users.json berhasil disinkronkan ke GitHub.")
            return True, "Tersimpan permanen ke GitHub."

        log.warning("Gagal sync GitHub HTTP %s: %s", put_resp.status_code, put_resp.text[:200])
        return False, f"Gagal sync GitHub (HTTP {put_resp.status_code})."

    except Exception as err:
        log.warning("Error sync GitHub: %s", err)
        return False, f"Error sync GitHub: {str(err)[:80]}"


def load_users_db():
    global USERS_DB

    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                USERS_DB["admin_ids"] = [int(x) for x in data.get("admin_ids", []) if str(x).isdigit()]
                USERS_DB["admin_username"] = str(data.get("admin_username", "")).strip().lstrip("@")
                USERS_DB["trial_limit"] = int(data.get("trial_limit", 2))
                USERS_DB["members"] = data.get("members", {}) if isinstance(data.get("members"), dict) else {}
                USERS_DB["trials"] = data.get("trials", {}) if isinstance(data.get("trials"), dict) else {}
        except Exception as err:
            log.error("Gagal membaca %s: %s", USERS_FILE, err)

    if ENV_ADMIN_ID.isdigit():
        env_id = int(ENV_ADMIN_ID)
        if env_id not in USERS_DB["admin_ids"]:
            USERS_DB["admin_ids"].append(env_id)
            sync_users_to_github()


def is_admin(user_id):
    return int(user_id) in USERS_DB["admin_ids"]


def check_user_access(user_id):
    """
    Mengembalikan tuple:
    (allowed: bool, role: str, info_text: str)
    role: 'ADMIN' | 'VIP' | 'TRIAL' | 'LOCKED'
    """
    uid = int(user_id)
    uid_str = str(uid)

    if is_admin(uid):
        return True, "ADMIN", "Admin (Akses Penuh Tanpa Batas)"

    today = today_wib_date()
    member = USERS_DB["members"].get(uid_str)

    if isinstance(member, dict):
        exp_str = str(member.get("expired", "")).strip()
        exp_date = parse_date_ymd(exp_str)
        if exp_date is not None:
            if today <= exp_date:
                sisa = (exp_date - today).days
                return True, "VIP", f"Member VIP Aktif (s/d {exp_str} • Sisa {sisa} hari)"
            else:
                return False, "LOCKED", f"Masa aktif habis pada {exp_str}"

    trial_limit = max(0, int(USERS_DB.get("trial_limit", 2)))
    used_trials = int(USERS_DB["trials"].get(uid_str, 0))

    if used_trials < trial_limit:
        sisa_trial = trial_limit - used_trials
        return True, "TRIAL", f"Mode Trial Gratis (Sisa {sisa_trial}x analisa)"

    return False, "LOCKED", "Belum terdaftar sebagai Member VIP"


def consume_trial_if_needed(user_id):
    uid = int(user_id)
    if is_admin(uid):
        return

    uid_str = str(uid)
    today = today_wib_date()
    member = USERS_DB["members"].get(uid_str)
    if isinstance(member, dict):
        exp_date = parse_date_ymd(member.get("expired", ""))
        if exp_date and today <= exp_date:
            return

    used = int(USERS_DB["trials"].get(uid_str, 0))
    USERS_DB["trials"][uid_str] = used + 1
    sync_users_to_github()


def build_locked_message_and_markup(user):
    uid = user.id
    _, _, reason = check_user_access(uid)
    admin_user = USERS_DB.get("admin_username", "").strip().lstrip("@")

    text = (
        "🔒 <b>AKSES TERBATAS (KHUSUS MEMBER VIP)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Telegram Kamu: <code>{uid}</code>\n"
        f"📌 Status: <b>{html.escape(reason)}</b>\n\n"
        "Untuk mengaktifkan akses penuh semua market &amp; timeframe, "
        "salin <b>ID Telegram</b> di atas lalu kirimkan ke Admin."
    )

    buttons = []
    if admin_user:
        buttons.append([
            InlineKeyboardButton(
                "💬 Hubungi Admin untuk Aktivasi",
                url=f"https://t.me/{admin_user}"
            )
        ])
    elif USERS_DB["admin_ids"]:
        first_admin = USERS_DB["admin_ids"][0]
        buttons.append([
            InlineKeyboardButton(
                "💬 Hubungi Admin untuk Aktivasi",
                url=f"tg://user?id={first_admin}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔄 Cek Status Saya", callback_data="check_my_status")
    ])

    return text, InlineKeyboardMarkup(buttons)


# ============================================================
# GEMINI AI ENGINE (KHUSUS KALENDER EKONOMI & BERITA MARKET)
# ============================================================

def call_gemini_json(prompt_text):
    """
    Memanggil Gemini API mode cepat khusus untuk analisa fundamental
    kalender ekonomi dan berita market. Tidak digunakan untuk analisa candle.
    """
    global LAST_GEMINI_ERROR

    if not GEMINI_API_KEY:
        LAST_GEMINI_ERROR = "GEMINI_API_KEY belum terbaca di env workflow (.github/workflows/bot.yml)."
        log.warning(LAST_GEMINI_ERROR)
        return None

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text}
                ]
            }
        ],
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
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent"
        )
        try:
            resp = SESSION.post(url, headers=headers, json=payload, timeout=18)
            if resp.status_code != 200:
                err_msg = resp.text[:220]
                LAST_GEMINI_ERROR = f"Model {model_name} gagal (HTTP {resp.status_code})"
                log.warning("Gemini %s HTTP %s: %s", model_name, resp.status_code, err_msg)
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
            LAST_GEMINI_ERROR = f"Error koneksi Gemini ({model_name}): {str(err)[:120]}"
            log.warning(LAST_GEMINI_ERROR)

    return None


# ============================================================
# TIME & METRIC HELPERS
# ============================================================

def extract_raw_time(item):
    if not isinstance(item, dict):
        return None

    keys = (
        "publishedAt", "published_at", "pubDate", "publishedDate",
        "time", "date", "datetime", "releaseTime",
        "createdAt", "created_at", "updatedAt", "timestamp"
    )
    for k in keys:
        val = item.get(k)
        if val not in (None, ""):
            return val
    return None


def parse_event_dt_wib(value):
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
    dt_wib = parse_event_dt_wib(value)
    if dt_wib is not None:
        bulan = BULAN_ID.get(dt_wib.month, dt_wib.strftime("%b"))
        return f"{dt_wib.day:02d} {bulan} {dt_wib.year} | {dt_wib.strftime('%H:%M')} WIB"

    if value is None or str(value).strip() == "":
        return "Terkini (Hari Ini)"

    return str(value).strip()


# ============================================================
# CANDLE DATA PARSER - BIQUOTE (MURNI TEKNIKAL)
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
# SMC: VALID ORDER BLOCK (OB)
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

        ob_low = ob["low"]
        ob_high = ob["high"]

        prev_slice = lookback[max(0, i - 8):i]
        impulse_slice = lookback[i + 1: min(len(lookback), i + 4)]

        # 1. Valid Bullish OB
        if ob["close"] < ob["open"]:
            c1_range = c1["high"] - c1["low"]
            c1_body = c1["close"] - c1["open"]

            strong_impulse = (
                c1_range > 0
                and c1_body >= c1_range * 0.55
                and c1["close"] > ob_high
            )
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

        # 2. Valid Bearish OB
        if ob["close"] > ob["open"]:
            c1_range = c1["high"] - c1["low"]
            c1_body = c1["open"] - c1["close"]

            strong_impulse = (
                c1_range > 0
                and c1_body >= c1_range * 0.55
                and c1["close"] < ob_low
            )
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


# ============================================================
# SMC: FVG (FAIR VALUE GAP)
# ============================================================

def detect_fvg(candles):

    for i in range(len(candles) - 1, 1, -1):

        first = candles[i - 2]
        third = candles[i]

        if third["low"] > first["high"]:
            return "BUY", first["high"], third["low"]

        if third["high"] < first["low"]:
            return "SELL", third["high"], first["low"]

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

        if (
            current["close"] > current["open"]
            and body >= candle_range * 0.65
            and current["close"] > before["high"]
        ):
            demand = current["low"]

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
# ANALYSIS ENGINE (100% TEKNIKAL SMC - TANPA GEMINI)
# ============================================================

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

    signals = {
        "OB": ob,
        "FVG": fvg,
        "BOS": bos,
        "SNR": snr,
        "SBR_RBS": sbr_rbs,
        "SND": snd,
        "RBR_DBD": rbr_dbd
    }

    buy_votes = sum(value == "BUY" for value in signals.values())
    sell_votes = sum(value == "SELL" for value in signals.values())

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
        "ob_type": ob_type,
        "ob_low": ob_low,
        "ob_high": ob_high,
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
# REPORT TEKNIKAL
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

    ob_sig = result["signals"]["OB"]
    if (
        ob_sig != "NEUTRAL"
        and result["ob_low"] is not None
        and result["ob_high"] is not None
    ):
        ob_text = (
            f"{ob_sig} ({result['ob_type']} @ "
            f"{format_price(result['ob_low'])} - "
            f"{format_price(result['ob_high'])})"
        )
    else:
        ob_text = ob_sig

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
        f"OB Valid: {ob_text}",
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
# CALENDAR & NEWS (US FOCUS + GEMINI AI FAST MODE + CACHE)
# ============================================================

def get_calendar():
    return api_get("/api/calendar")


def get_news():
    return api_get(
        "/api/news/market",
        params={
            "language": "en",
            "country": "US",
            "maxResults": 8
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

        raw_time = extract_raw_time(item)
        dt_wib = parse_event_dt_wib(raw_time)
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

    gemini_input = []
    for idx, (dt_wib, rank, badge, title_en, item) in enumerate(top_events):
        gemini_input.append({
            "index": idx,
            "title": title_en,
            "actual": str(item.get("actual") or ""),
            "forecast": str(
                item.get("forecast")
                or item.get("consensus")
                or item.get("estimate")
                or ""
            )
        })

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
        if title_id.lower() != title_en.lower():
            display_title = f"{html.escape(title_id)} <i>({html.escape(title_en)})</i>"
        else:
            display_title = html.escape(title_en)

        rel = str(ai_info.get("gold_relevance") or "").upper()
        if rel == "HIGH":
            display_badge = "🔥 DAMPAK BESAR KE EMAS"
        elif rel == "MEDIUM":
            display_badge = "⚡ DAMPAK SEDANG KE EMAS"
        elif rel == "LOW":
            display_badge = "🔹 DAMPAK KECIL KE EMAS"
        else:
            display_badge = badge

        raw_time = extract_raw_time(item)
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
            inverse_usd = any(
                k in title_en.lower() for k in ("unemployment", "jobless claims", "layoff")
            )
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

        if len(summary_en) > 160:
            summary_en = summary_en[:157].rsplit(" ", 1)[0] + "..."

        valid_items.append((title_en, summary_en, item))

    if not valid_items:
        return "<b>📰 BERITA MARKET US &amp; EMAS (XAUUSD)</b>\n\nBelum ada berita valid."

    sample_items = valid_items[:7]
    gemini_input = [
        {
            "index": idx,
            "title": title_en,
            "snippet": summary_en
        }
        for idx, (title_en, summary_en, _) in enumerate(sample_items)
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
                str(
                    raw_item.get("source")
                    or raw_item.get("publisher")
                    or raw_item.get("site")
                    or "Market Wire"
                ).strip()
            )

            raw_time = extract_raw_time(raw_item)
            time_wib = html.escape(format_wib_time(raw_time))

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
        diag_note = html.escape(
            LAST_GEMINI_ERROR
            or "Pastikan GEMINI_API_KEY sudah ditambahkan di GitHub Secrets & file bot.yml."
        )
        for idx, (title_en, summary_en, raw_item) in enumerate(sample_items[:5]):
            source = html.escape(
                str(
                    raw_item.get("source")
                    or raw_item.get("publisher")
                    or raw_item.get("site")
                    or "Market Wire"
                ).strip()
            )
            raw_time = extract_raw_time(raw_item)
            time_wib = html.escape(format_wib_time(raw_time))

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

    raw_result = get_calendar()
    formatted = format_calendar_data(raw_result)
    RESPONSE_CACHE["calendar"] = (now_ts, formatted)
    return formatted


def get_cached_news(force_refresh=False):
    now_ts = time.time()
    cached = RESPONSE_CACHE.get("news")
    if not force_refresh and cached and (now_ts - cached[0] < NEWS_CACHE_TTL):
        return cached[1]

    raw_result = get_news()
    formatted = format_news_data(raw_result)
    RESPONSE_CACHE["news"] = (now_ts, formatted)
    return formatted


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
        ],
        [
            InlineKeyboardButton(
                "👤 Status Akun Saya",
                callback_data="check_my_status"
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
# TELEGRAM USER COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    allowed, role, status_info = check_user_access(user.id)

    log.info("/start dari %s (%s) -> %s", user.id, user.first_name, role)

    if not USERS_DB["admin_ids"]:
        await update.message.reply_html(
            "🤖 <b>MASTER SMC SCALPING BOT</b>\n\n"
            "⚠️ <b>Admin belum diklaim.</b>\n"
            f"ID Telegram kamu: <code>{user.id}</code>\n\n"
            "Ketik <code>/claimadmin</code> sekarang untuk mengunci akunmu sebagai <b>Admin Utama</b>."
        )
        return

    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return

    admin_hint = "\n👑 Ketik /admin untuk membuka panel kelola member." if role == "ADMIN" else ""

    await update.message.reply_html(
        "🤖 <b>MASTER SMC SCALPING BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Kamu: <code>{user.id}</code>\n"
        f"📌 Status: <b>{html.escape(status_info)}</b>\n\n"
        "• <b>Analisa Chart:</b> 100% Murni SMC (OB Valid + FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD)\n"
        "• <b>Kalender &amp; Berita:</b> Fundamental Gemini AI"
        f"{admin_hint}",
        reply_markup=main_menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    _, _, user_status = check_user_access(user.id)

    if GEMINI_API_KEY:
        ai_status = f"Terdeteksi (...{GEMINI_API_KEY[-4:]})"
    else:
        ai_status = "TIDAK TERDETEKSI"

    gh_sync_status = "Aktif (Auto-Commit)" if (GITHUB_TOKEN and GITHUB_REPOSITORY) else "Lokal Saja"

    await update.message.reply_html(
        "🟢 <b>STATUS BOT &amp; AKUN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Kamu: <code>{user.id}</code>\n"
        f"👤 Akses Kamu: <b>{html.escape(user_status)}</b>\n"
        f"🤖 Gemini AI: <b>{ai_status}</b>\n"
        f"☁️ Sync Member GitHub: <b>{gh_sync_status}</b>\n"
        f"👥 Total Member Terdaftar: <b>{len(USERS_DB['members'])}</b>"
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    allowed, _, _ = check_user_access(user.id)

    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return

    await update.message.reply_text(
        "Pilih menu:",
        reply_markup=main_menu()
    )


# ============================================================
# TELEGRAM ADMIN COMMANDS (FULL CONTROL VIA TELEGRAM)
# ============================================================

async def claim_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if USERS_DB["admin_ids"]:
        if is_admin(user.id):
            await update.message.reply_text("✅ Kamu sudah terdaftar sebagai Admin.")
        else:
            await update.message.reply_text("⛔ Admin sudah diklaim oleh pemilik bot.")
        return

    USERS_DB["admin_ids"].append(int(user.id))
    if user.username:
        USERS_DB["admin_username"] = user.username

    ok, sync_msg = await asyncio.to_thread(sync_users_to_github)

    await update.message.reply_html(
        "👑 <b>ADMIN BERHASIL DIKLAIM!</b>\n"
        f"ID Admin: <code>{user.id}</code>\n"
        f"Status Simpan: <i>{html.escape(sync_msg)}</i>\n\n"
        "Ketik /admin untuk melihat daftar perintah kelola member.",
        reply_markup=main_menu()
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    today = today_wib_date()
    aktif_count = 0
    expired_count = 0
    for m in USERS_DB["members"].values():
        exp = parse_date_ymd(m.get("expired", ""))
        if exp and today <= exp:
            aktif_count += 1
        else:
            expired_count += 1

    contact_disp = f"@{USERS_DB['admin_username']}" if USERS_DB["admin_username"] else "(Belum diatur)"

    text = (
        "👑 <b>PANEL ADMIN BOT SMC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Member VIP Aktif: <b>{aktif_count}</b>\n"
        f"• Member Expired: <b>{expired_count}</b>\n"
        f"• Jatah Trial User Baru: <b>{USERS_DB['trial_limit']}x</b>\n"
        f"• Kontak Admin: <b>{html.escape(contact_disp)}</b>\n\n"
        "📋 <b>DAFTAR PERINTAH ADMIN:</b>\n"
        "1️⃣ <b>Tambah Member Baru:</b>\n"
        "<code>/add 987654321 30 Budi</code>\n"
        "<i>(Format: /add ID_TELEGRAM JUMLAH_HARI NAMA)</i>\n\n"
        "2️⃣ <b>Tambah / Perpanjang Masa Aktif:</b>\n"
        "<code>/perpanjang 987654321 30</code>\n"
        "<i>(Menambah hari ke masa aktif user)</i>\n\n"
        "3️⃣ <b>Cek Detail User:</b>\n"
        "<code>/cekuser 987654321</code>\n\n"
        "4️⃣ <b>Hapus Member:</b>\n"
        "<code>/hapus 987654321</code>\n\n"
        "5️⃣ <b>Lihat Semua Member:</b>\n"
        "<code>/listuser</code>\n\n"
        "6️⃣ <b>Atur Jatah Trial Gratis (0 = Kunci Total):</b>\n"
        "<code>/settrial 2</code>\n\n"
        "7️⃣ <b>Atur Username Kontak Admin:</b>\n"
        "<code>/setcontact username_kamu</code>"
    )

    await update.message.reply_html(text)


async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/add 987654321 30 Budi</code>"
        )
        return

    target_id = args[0].strip()
    days = int(args[1])
    nama = " ".join(args[2:]).strip() if len(args) >= 3 else "Member VIP"

    if days <= 0:
        await update.message.reply_text("⚠️ Jumlah hari harus lebih dari 0.")
        return

    today = today_wib_date()
    exp_date = today + timedelta(days=days)
    exp_str = exp_date.strftime("%Y-%m-%d")

    USERS_DB["members"][target_id] = {
        "nama": nama,
        "expired": exp_str,
        "updated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    }

    _, sync_msg = await asyncio.to_thread(sync_users_to_github)

    await update.message.reply_html(
        "✅ <b>MEMBER BERHASIL DITAMBAHKAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Nama: <b>{html.escape(nama)}</b>\n"
        f"⏳ Durasi: <b>{days} hari</b>\n"
        f"📅 Aktif s/d: <b>{exp_str}</b>\n"
        f"☁️ Status: <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_extend_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/perpanjang 987654321 30</code>"
        )
        return

    target_id = args[0].strip()
    add_days = int(args[1])

    if add_days <= 0:
        await update.message.reply_text("⚠️ Jumlah hari harus lebih dari 0.")
        return

    today = today_wib_date()
    existing = USERS_DB["members"].get(target_id)

    if isinstance(existing, dict):
        nama = existing.get("nama", "Member VIP")
        old_exp = parse_date_ymd(existing.get("expired", ""))
        base_date = old_exp if (old_exp and old_exp > today) else today
    else:
        nama = "Member VIP"
        base_date = today

    new_exp = base_date + timedelta(days=add_days)
    new_exp_str = new_exp.strftime("%Y-%m-%d")
    sisa_hari = (new_exp - today).days

    USERS_DB["members"][target_id] = {
        "nama": nama,
        "expired": new_exp_str,
        "updated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    }

    _, sync_msg = await asyncio.to_thread(sync_users_to_github)

    await update.message.reply_html(
        "🔄 <b>MASA AKTIF DIPERPANJANG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Nama: <b>{html.escape(nama)}</b>\n"
        f"➕ Tambahan: <b>+{add_days} hari</b>\n"
        f"📅 Expired Baru: <b>{new_exp_str}</b> (Sisa {sisa_hari} hari)\n"
        f"☁️ Status: <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/hapus 987654321</code>"
        )
        return

    target_id = args[0].strip()
    removed = USERS_DB["members"].pop(target_id, None)

    if not removed:
        await update.message.reply_html(
            f"ℹ️ ID <code>{target_id}</code> tidak ditemukan di daftar member."
        )
        return

    _, sync_msg = await asyncio.to_thread(sync_users_to_github)
    nama = removed.get("nama", "Member")

    await update.message.reply_html(
        "🗑️ <b>MEMBER DIHAPUS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Nama: <b>{html.escape(nama)}</b>\n"
        f"☁️ Status: <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/cekuser 987654321</code>"
        )
        return

    target_id = args[0].strip()
    member = USERS_DB["members"].get(target_id)
    today = today_wib_date()

    if int(target_id) in USERS_DB["admin_ids"]:
        await update.message.reply_html(
            f"🆔 ID: <code>{target_id}</code>\n👑 Status: <b>ADMIN UTAMA</b>"
        )
        return

    if isinstance(member, dict):
        nama = html.escape(str(member.get("nama", "Member VIP")))
        exp_str = str(member.get("expired", "-"))
        exp_date = parse_date_ymd(exp_str)

        if exp_date and today <= exp_date:
            sisa = (exp_date - today).days
            status_badge = f"🟢 AKTIF (Sisa {sisa} hari)"
        else:
            status_badge = "🔴 EXPIRED (Habis)"

        await update.message.reply_html(
            "🔍 <b>DETAIL MEMBER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: <code>{target_id}</code>\n"
            f"👤 Nama: <b>{nama}</b>\n"
            f"📌 Status: <b>{status_badge}</b>\n"
            f"📅 Expired: <b>{html.escape(exp_str)}</b>"
        )
    else:
        used = int(USERS_DB["trials"].get(target_id, 0))
        limit = int(USERS_DB["trial_limit"])
        await update.message.reply_html(
            "🔍 <b>DETAIL USER (NON-VIP)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: <code>{target_id}</code>\n"
            f"📌 Status: <b>Belum Terdaftar VIP</b>\n"
            f"🎁 Trial Terpakai: <b>{used}/{limit}</b>"
        )


async def cmd_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    members = USERS_DB["members"]
    if not members:
        await update.message.reply_text("📭 Belum ada member VIP yang terdaftar.")
        return

    today = today_wib_date()
    rows = []

    for uid_str, info in members.items():
        nama = html.escape(str(info.get("nama", "Member")))
        exp_str = str(info.get("expired", "-"))
        exp_date = parse_date_ymd(exp_str)

        if exp_date and today <= exp_date:
            sisa = (exp_date - today).days
            rows.append((0, sisa, f"🟢 <code>{uid_str}</code> • <b>{nama}</b>\n    └ Exp: {exp_str} (Sisa {sisa} hr)"))
        else:
            rows.append((1, 0, f"🔴 <code>{uid_str}</code> • <b>{nama}</b>\n    └ Exp: {exp_str} (HABIS)"))

    rows.sort(key=lambda x: (x[0], -x[1]))

    header = f"👥 <b>DAFTAR MEMBER VIP ({len(rows)} Orang)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    body = "\n".join(r[2] for r in rows)

    full_msg = header + body
    if len(full_msg) > 3900:
        full_msg = full_msg[:3850] + "\n\n<i>...dan lainnya.</i>"

    await update.message.reply_html(full_msg)


async def cmd_set_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/settrial 2</code> (atau <code>/settrial 0</code> untuk kunci tanpa trial)"
        )
        return

    new_limit = int(args[0])
    USERS_DB["trial_limit"] = new_limit
    _, sync_msg = await asyncio.to_thread(sync_users_to_github)

    mode_desc = "Kunci Total (Tanpa Trial)" if new_limit == 0 else f"{new_limit}x Analisa Gratis"

    await update.message.reply_html(
        "⚙️ <b>PENGATURAN TRIAL DIPERBARUI</b>\n"
        f"Mode User Baru: <b>{mode_desc}</b>\n"
        f"☁️ Status: <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_set_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1:
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/setcontact suryaditj</code>"
        )
        return

    uname = args[0].strip().lstrip("@")
    USERS_DB["admin_username"] = uname
    _, sync_msg = await asyncio.to_thread(sync_users_to_github)

    await update.message.reply_html(
        "💬 <b>KONTAK ADMIN DIPERBARUI</b>\n"
        f"Tombol aktivasi sekarang mengarah ke: <b>@{html.escape(uname)}</b>\n"
        f"☁️ Status: <i>{html.escape(sync_msg)}</i>"
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    user = query.from_user

    await query.answer()

    data = query.data

    log.info("Callback [%s]: %s", user.id, data)

    if data == "check_my_status":
        allowed, _, status_info = check_user_access(user.id)
        if allowed:
            await query.message.reply_html(
                f"✅ <b>AKSES AKTIF</b>\n"
                f"🆔 ID Kamu: <code>{user.id}</code>\n"
                f"📌 Status: <b>{html.escape(status_info)}</b>",
                reply_markup=main_menu()
            )
        else:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
        return

    if data == "menu":

        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

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

        allowed, role, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

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

            report_text = create_report(result)

            if role == "TRIAL":
                await asyncio.to_thread(consume_trial_if_needed, user.id)
                _, _, new_status = check_user_access(user.id)
                report_text += f"\n\n🎁 {new_status}"

            await processing.edit_text(
                report_text,
                reply_markup=analysis_menu(symbol, timeframe)
            )

            log.info(
                "Analisis selesai: %s %s (user=%s)",
                symbol,
                timeframe,
                user.id
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

    if data in ("calendar", "calendar_refresh"):

        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        force = (data == "calendar_refresh")
        has_cache = ("calendar" in RESPONSE_CACHE) and not force

        if has_cache:
            try:
                formatted_text = get_cached_calendar(force_refresh=False)
                await query.message.reply_text(
                    formatted_text,
                    parse_mode="HTML",
                    reply_markup=info_menu("calendar_refresh")
                )
                return
            except Exception:
                pass

        message = await query.message.reply_text(
            "⏳ Mengambil jadwal ekonomi US..."
        )

        try:

            formatted_text = await asyncio.to_thread(
                get_cached_calendar,
                force
            )

            await message.edit_text(
                formatted_text,
                parse_mode="HTML",
                reply_markup=info_menu("calendar_refresh")
            )

        except Exception as error:

            log.exception("CALENDAR ERROR")

            await message.edit_text(
                f"❌ Kalender gagal:\n{str(error)[:2000]}",
                reply_markup=info_menu("calendar_refresh")
            )

        return

    if data in ("news", "news_refresh"):

        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        force = (data == "news_refresh")
        has_cache = ("news" in RESPONSE_CACHE) and not force

        if has_cache:
            try:
                formatted_text = get_cached_news(force_refresh=False)
                await query.message.reply_text(
                    formatted_text,
                    parse_mode="HTML",
                    reply_markup=info_menu("news_refresh")
                )
                return
            except Exception:
                pass

        message = await query.message.reply_text(
            "⏳ Membaca berita market US..."
        )

        try:

            formatted_text = await asyncio.to_thread(
                get_cached_news,
                force
            )

            await message.edit_text(
                formatted_text,
                parse_mode="HTML",
                reply_markup=info_menu("news_refresh")
            )

        except Exception as error:

            log.exception("NEWS ERROR")

            await message.edit_text(
                f"❌ Berita gagal:\n{str(error)[:2000]}",
                reply_markup=info_menu("news_refresh")
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
    log.info("Candles: 100 CLOSED CANDLES (PURE SMC)")
    log.info("Strategy: OB Valid + FVG + BOS + SNR + SBR/RBS + SND + RBR/DBD")
    log.info("Gemini AI: %s", "ENABLED (Fast Mode)" if GEMINI_API_KEY else "DISABLED")
    log.info("Admin IDs: %s | Members: %s", USERS_DB["admin_ids"], len(USERS_DB["members"]))
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

    load_users_db()

    log.info("Membangun Telegram application...")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # User Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("menu", menu_command))

    # Admin Commands
    application.add_handler(CommandHandler("claimadmin", claim_admin))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("add", cmd_add_user))
    application.add_handler(CommandHandler("perpanjang", cmd_extend_user))
    application.add_handler(CommandHandler("extend", cmd_extend_user))
    application.add_handler(CommandHandler("hapus", cmd_delete_user))
    application.add_handler(CommandHandler("delete", cmd_delete_user))
    application.add_handler(CommandHandler("cekuser", cmd_check_user))
    application.add_handler(CommandHandler("listuser", cmd_list_users))
    application.add_handler(CommandHandler("settrial", cmd_set_trial))
    application.add_handler(CommandHandler("setcontact", cmd_set_contact))

    application.add_handler(CallbackQueryHandler(callback_handler))
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
