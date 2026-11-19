# -*- coding: utf-8 -*-
import os
import sys
import json
import base64
import logging
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# ENVIRONMENT & CONSTANTS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
ENV_ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

USERS_FILE = "users.json"
SIGNALS_FILE = "signals_history.json"
BASE_URL = "https://biquote.io"

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash"
]

MARKETS = ["XAUUSD", "BTCUSD"]
MACRO_MARKETS = ["USOIL", "DXY"]

TIMEFRAMES = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d"
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
# LOGGING & HTTP SESSION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

log = logging.getLogger("SMC_BOT")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 SMC-Bot/5.0",
    "Accept": "application/json"
})


def api_get(endpoint, params=None):
    url = BASE_URL + endpoint
    response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        raise ValueError(f"Respons API bukan JSON: {response.text[:500]}")


# ============================================================
# TIME & PRICE FORMATTING HELPERS
# ============================================================

def now_wib():
    return datetime.now(WIB)


def today_wib_date():
    return datetime.now(WIB).date()


def parse_date_ymd(date_str):
    try:
        return datetime.strptime(str(date_str).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def format_price(value):
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}"


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
# GITHUB JSON FILE SYNC
# ============================================================

def github_sync_json_file(filename, data_obj, commit_msg):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data_obj, f, indent=2, ensure_ascii=False)
    except Exception as err:
        log.error("Gagal menulis %s lokal: %s", filename, err)

    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return False, "Tersimpan lokal (GITHUB_TOKEN/REPO tidak tersedia)."

    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{filename}"
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

        content_str = json.dumps(data_obj, indent=2, ensure_ascii=False) + "\n"
        encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        payload = {
            "message": commit_msg,
            "content": encoded
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            return True, "Tersimpan permanen ke GitHub."

        return False, f"Gagal sync GitHub (HTTP {put_resp.status_code})."
    except Exception as err:
        return False, f"Error sync GitHub: {str(err)[:80]}"
