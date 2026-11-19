# -*- coding: utf-8 -*-
import os
import json
import time
import base64
import logging
import threading
from datetime import datetime, timezone, timedelta
import requests

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("smc_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BIQUOTE_BASE_URL", "https://biquote.io").strip().rstrip("/")
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
    or os.getenv("GEMINI_KEY", "").strip()
)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_REF_NAME", "main").strip() or "main"

USERS_FILE = "users_db.json"
SIGNALS_FILE = "signals_history.json"

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

MARKETS = ["XAUUSD", "BTCUSD"]
MACRO_MARKETS = ["USOIL", "DXY"]

TIMEFRAMES = {
    "D1": "1d",
    "H4": "4h",
    "H1": "1h",
    "M30": "30m",
    "M15": "15m",
    "M5": "5m"
}

CANDLE_COUNT = 120
FETCH_COUNT = 260

WIB = timezone(timedelta(hours=7))

BULAN_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "Mei", 6: "Jun", 7: "Jul", 8: "Agu",
    9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"
}

_THREAD_LOCAL = threading.local()
_GITHUB_SYNC_LOCK = threading.Lock()


def get_http_session():
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; SMCHybridBot/5.0)",
            "Accept": "application/json"
        })
        _THREAD_LOCAL.session = sess
    return sess


def api_get(path, params=None, retries=2):
    url = f"{BASE_URL}{path}"
    last_err = None
    sess = get_http_session()
    for attempt in range(retries + 1):
        try:
            resp = sess.get(url, params=params, timeout=14)
            resp.raise_for_status()
            return resp.json()
        except Exception as err:
            last_err = err
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Gagal mengambil data dari {path}: {last_err}")


def format_price(value):
    if value is None:
        return "-"
    v = float(value)
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def today_wib_date():
    return datetime.now(WIB).date()


def parse_date_ymd(date_str):
    try:
        return datetime.strptime(str(date_str).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def extract_raw_time(item):
    for key in (
        "time", "date", "datetime", "timestamp",
        "releaseTime", "eventTime", "publishedAt", "startTime"
    ):
        val = item.get(key)
        if val not in (None, ""):
            return val
    return None


def parse_event_dt_wib(raw_time):
    if raw_time in (None, ""):
        return None
    try:
        if isinstance(raw_time, (int, float)):
            ts = float(raw_time)
            if ts > 100000000000:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(WIB)

        s = str(raw_time).strip()
        if not s:
            return None

        if s.isdigit():
            ts = float(s)
            if ts > 100000000000:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(WIB)

        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WIB)
    except Exception:
        return None


def format_wib_time(raw_time):
    dt_wib = parse_event_dt_wib(raw_time)
    if dt_wib is not None:
        bulan = BULAN_ID.get(dt_wib.month, dt_wib.strftime("%b"))
        return f"{dt_wib.day:02d} {bulan} {dt_wib.year}, {dt_wib.strftime('%H:%M')} WIB"
    if raw_time not in (None, ""):
        return f"{str(raw_time).strip()} (WIB)"
    return "Jadwal Segera (WIB)"


def github_sync_json_file(file_path, payload_dict, commit_msg):
    """
    Sinkronisasi JSON ke GitHub dengan thread lock agar aman dari konflik SHA (HTTP 409).
    """
    with _GITHUB_SYNC_LOCK:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload_dict, f, indent=2, ensure_ascii=False)
        except Exception as err:
            log.error("Gagal simpan lokal %s: %s", file_path, err)

        if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
            return False, "Tersimpan lokal (GITHUB_TOKEN belum aktif)"

        api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{file_path}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        sess = get_http_session()
        try:
            sha = None
            get_resp = sess.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=12)
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")
            elif get_resp.status_code not in (200, 404):
                return False, f"Gagal cek GitHub ({get_resp.status_code})"

            raw_json = json.dumps(payload_dict, indent=2, ensure_ascii=False)
            encoded = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

            body = {
                "message": commit_msg,
                "content": encoded,
                "branch": GITHUB_BRANCH
            }
            if sha:
                body["sha"] = sha

            put_resp = sess.put(api_url, headers=headers, json=body, timeout=14)
            if put_resp.status_code in (200, 201):
                return True, "Tersinkron otomatis ke GitHub"
            return False, f"Gagal push GitHub ({put_resp.status_code})"
        except Exception as err:
            return False, f"Error sync GitHub: {str(err)[:70]}"


def github_sync_json_async(file_path, payload_dict, commit_msg):
    """
    Menjalankan commit GitHub di background thread agar bot merespons tanpa delay.
    """
    snapshot = json.loads(json.dumps(payload_dict))
    t = threading.Thread(
        target=github_sync_json_file,
        args=(file_path, snapshot, commit_msg),
        daemon=True
    )
    t.start()
