# -*- coding: utf-8 -*-
import os
import json
import html
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import (
    USERS_FILE, ENV_ADMIN_ID, MARKETS, WIB, log,
    today_wib_date, parse_date_ymd, github_sync_json_file
)

USERS_DB = {
    "admin_ids": [],
    "admin_username": "",
    "free_mode": True,       # Default GRATIS SEMUA; bisa diubah Admin via tombol inline di /admin
    "trial_limit": 2,
    "members": {},
    "trials": {},
    "risk_profiles": {},
    "price_alerts": [],
    "signal_subs": {}        # Format: {"user_id": "ALL" | "XAUUSD" | "BTCUSD" | "OFF"}
}


def sync_users():
    return github_sync_json_file(
        USERS_FILE,
        USERS_DB,
        "chore: update users.json via Telegram Bot"
    )


def load_users_db():
    global USERS_DB
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                USERS_DB["admin_ids"] = [int(x) for x in data.get("admin_ids", []) if str(x).isdigit()]
                USERS_DB["admin_username"] = str(data.get("admin_username", "")).strip().lstrip("@")
                USERS_DB["free_mode"] = bool(data.get("free_mode", True))
                USERS_DB["trial_limit"] = int(data.get("trial_limit", 2))
                USERS_DB["members"] = data.get("members", {}) if isinstance(data.get("members"), dict) else {}
                USERS_DB["trials"] = data.get("trials", {}) if isinstance(data.get("trials"), dict) else {}
                USERS_DB["risk_profiles"] = data.get("risk_profiles", {}) if isinstance(data.get("risk_profiles"), dict) else {}
                USERS_DB["price_alerts"] = data.get("price_alerts", []) if isinstance(data.get("price_alerts"), list) else []
                USERS_DB["signal_subs"] = data.get("signal_subs", {}) if isinstance(data.get("signal_subs"), dict) else {}
        except Exception as err:
            log.error("Gagal membaca %s: %s", USERS_FILE, err)

    if ENV_ADMIN_ID.isdigit():
        env_id = int(ENV_ADMIN_ID)
        if env_id not in USERS_DB["admin_ids"]:
            USERS_DB["admin_ids"].append(env_id)
            sync_users()


def is_admin(user_id):
    return int(user_id) in USERS_DB["admin_ids"]


def is_free_mode():
    return bool(USERS_DB.get("free_mode", True))


def toggle_free_mode():
    USERS_DB["free_mode"] = not is_free_mode()
    return sync_users()


def check_user_access(user_id):
    uid = int(user_id)
    uid_str = str(uid)

    if is_admin(uid):
        return True, "ADMIN", "Admin (Akses Penuh Tanpa Batas)"

    today = today_wib_date()
    member = USERS_DB["members"].get(uid_str)
    if isinstance(member, dict):
        exp_str = str(member.get("expired", "")).strip()
        exp_date = parse_date_ymd(exp_str)
        if exp_date is not None and today <= exp_date:
            sisa = (exp_date - today).days
            return True, "VIP", f"Member VIP Aktif (s/d {exp_str} • Sisa {sisa} hari)"

    if is_free_mode():
        return True, "FREE", "Mode Gratis Terbuka (Semua Fitur Aktif)"

    if isinstance(member, dict):
        exp_str = str(member.get("expired", "")).strip()
        if exp_str:
            return False, "LOCKED", f"Masa aktif habis pada {exp_str}"

    trial_limit = max(0, int(USERS_DB.get("trial_limit", 2)))
    used_trials = int(USERS_DB["trials"].get(uid_str, 0))
    if used_trials < trial_limit:
        sisa_trial = trial_limit - used_trials
        return True, "TRIAL", f"Mode Trial Gratis (Sisa {sisa_trial}x analisa)"

    return False, "LOCKED", "Belum terdaftar sebagai Member VIP"


def consume_trial_if_needed(user_id):
    if is_free_mode():
        return
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
    USERS_DB["trials"][uid_str] = int(USERS_DB["trials"].get(uid_str, 0)) + 1
    sync_users()


# ============================================================
# SIGNAL SUBSCRIPTIONS (3-TF H1 + M15 + M5)
# ============================================================

def get_user_signal_sub(user_id):
    val = str(USERS_DB["signal_subs"].get(str(int(user_id)), "OFF")).upper()
    return val if val in ("ALL", "XAUUSD", "BTCUSD", "OFF") else "OFF"


def set_user_signal_sub(user_id, mode):
    uid_str = str(int(user_id))
    mode_clean = str(mode).upper()
    if mode_clean == "OFF":
        USERS_DB["signal_subs"].pop(uid_str, None)
    else:
        USERS_DB["signal_subs"][uid_str] = mode_clean
    return sync_users()


def get_signal_subscribers_for(symbol):
    """
    Mengembalikan list user_id yang berlangganan sinyal otomatis untuk symbol tertentu
    dan memiliki akses aktif (Free Mode, VIP Aktif, atau Admin).
    """
    recipients = []
    for uid_str, mode in USERS_DB["signal_subs"].items():
        if not uid_str.isdigit():
            continue
        mode_up = str(mode).upper()
        if mode_up in ("ALL", symbol):
            uid = int(uid_str)
            allowed, role, _ = check_user_access(uid)
            if allowed and role in ("ADMIN", "VIP", "FREE"):
                recipients.append(uid)
    return recipients


# ============================================================
# RISK PROFILE & PRICE ALERTS
# ============================================================

def set_user_risk_profile(user_id, balance, risk_pct):
    uid_str = str(int(user_id))
    USERS_DB["risk_profiles"][uid_str] = {
        "balance": float(balance),
        "risk_pct": float(risk_pct)
    }
    return sync_users()


def get_user_risk_profile(user_id):
    return USERS_DB["risk_profiles"].get(str(int(user_id)))


def add_price_alert(user_id, symbol, target_price, current_price):
    uid = int(user_id)
    user_alerts = [a for a in USERS_DB["price_alerts"] if int(a.get("user_id", 0)) == uid]
    if len(user_alerts) >= 5:
        return False, "Maksimal 5 alert aktif per akun. Hapus dulu dengan /delalert."

    condition = "ABOVE" if target_price >= current_price else "BELOW"
    alert_id = f"{uid}_{int(datetime.now().timestamp())}"
    USERS_DB["price_alerts"].append({
        "id": alert_id,
        "user_id": uid,
        "symbol": symbol,
        "target": float(target_price),
        "condition": condition,
        "created_at": datetime.now(WIB).strftime("%d/%m %H:%M WIB")
    })
    _, sync_msg = sync_users()
    return True, f"Alert {symbol} @ {target_price} dipasang ({condition}). {sync_msg}"


def get_user_alerts(user_id):
    uid = int(user_id)
    return [a for a in USERS_DB["price_alerts"] if int(a.get("user_id", 0)) == uid]


def clear_user_alerts(user_id):
    uid = int(user_id)
    before = len(USERS_DB["price_alerts"])
    USERS_DB["price_alerts"] = [a for a in USERS_DB["price_alerts"] if int(a.get("user_id", 0)) != uid]
    removed = before - len(USERS_DB["price_alerts"])
    if removed > 0:
        sync_users()
    return removed


def build_locked_message_and_markup(user):
    uid = user.id
    _, _, reason = check_user_access(uid)
    admin_user = USERS_DB.get("admin_username", "").strip().lstrip("@")

    text = (
        "🔒 <b>AKSES TERBATAS (KHUSUS MEMBER VIP)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Telegram Kamu: <code>{uid}</code>\n"
        f"📌 Status: <b>{html.escape(reason)}</b>\n\n"
        "Untuk mengaktifkan akses penuh semua fitur VIP, "
        "salin <b>ID Telegram</b> di atas lalu kirimkan ke Admin."
    )

    buttons = []
    if admin_user:
        buttons.append([
            InlineKeyboardButton("💬 Hubungi Admin untuk Aktivasi", url=f"https://t.me/{admin_user}")
        ])
    elif USERS_DB["admin_ids"]:
        buttons.append([
            InlineKeyboardButton("💬 Hubungi Admin untuk Aktivasi", url=f"tg://user?id={USERS_DB['admin_ids'][0]}")
        ])

    buttons.append([
        InlineKeyboardButton("🔄 Cek Status Saya", callback_data="check_my_status"),
        InlineKeyboardButton("📖 Panduan Bot", callback_data="help_commands")
    ])

    return text, InlineKeyboardMarkup(buttons)
