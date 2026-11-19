# -*- coding: utf-8 -*-
import os
import json
import html
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import (
    USERS_FILE, WIB, log,
    today_wib_date, parse_date_ymd, github_sync_json_file
)

USERS_DB = {
    "free_mode": False,
    "trial_limit": 2,
    "admin_username": "",
    "admin_ids": [],
    "members": {},
    "trials": {},
    "risk_profiles": {},
    "price_alerts": []
}


def load_users_db():
    global USERS_DB
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                USERS_DB["free_mode"] = bool(data.get("free_mode", False))
                USERS_DB["trial_limit"] = int(data.get("trial_limit", 2))
                USERS_DB["admin_username"] = str(data.get("admin_username", "")).strip().lstrip("@")
                USERS_DB["admin_ids"] = [int(x) for x in data.get("admin_ids", []) if str(x).isdigit()]
                USERS_DB["members"] = data.get("members", {}) if isinstance(data.get("members"), dict) else {}
                USERS_DB["trials"] = data.get("trials", {}) if isinstance(data.get("trials"), dict) else {}
                USERS_DB["risk_profiles"] = data.get("risk_profiles", {}) if isinstance(data.get("risk_profiles"), dict) else {}
                USERS_DB["price_alerts"] = data.get("price_alerts", []) if isinstance(data.get("price_alerts"), list) else []
                return
        except Exception as err:
            log.warning("Gagal membaca %s: %s", USERS_FILE, err)
    sync_users()


def sync_users():
    return github_sync_json_file(USERS_FILE, USERS_DB, "chore: update users_db.json")


def is_admin(user_id):
    return int(user_id) in USERS_DB["admin_ids"]


def is_free_mode():
    return bool(USERS_DB.get("free_mode", False))


def toggle_free_mode():
    USERS_DB["free_mode"] = not is_free_mode()
    return sync_users()


def check_user_access(user_id):
    uid_str = str(user_id)
    if is_admin(user_id):
        return True, "ADMIN", "Admin Utama (Akses Penuh)"

    if is_free_mode():
        return True, "FREE_MODE", "Mode Gratis Aktif (Akses Terbuka)"

    member = USERS_DB["members"].get(uid_str)
    if isinstance(member, dict):
        exp_str = str(member.get("expired", ""))
        exp_date = parse_date_ymd(exp_str)
        today = today_wib_date()
        if exp_date and today <= exp_date:
            sisa = (exp_date - today).days
            return True, "VIP", f"Member VIP Aktif (Sisa {sisa} hari • s/d {exp_str})"

    used = int(USERS_DB["trials"].get(uid_str, 0))
    limit = int(USERS_DB.get("trial_limit", 2))
    if used < limit:
        sisa_trial = limit - used
        return True, "TRIAL", f"Akses Trial Gratis (Sisa {sisa_trial}x analisa)"

    return False, "LOCKED", "Masa aktif VIP / Trial habis"


def consume_trial_if_needed(user_id):
    uid_str = str(user_id)
    USERS_DB["trials"][uid_str] = int(USERS_DB["trials"].get(uid_str, 0)) + 1
    sync_users()


def set_user_risk_profile(user_id, balance, risk_pct):
    uid_str = str(user_id)
    USERS_DB["risk_profiles"][uid_str] = {
        "balance": round(float(balance), 2),
        "risk_pct": round(float(risk_pct), 2)
    }
    return sync_users()


def get_user_risk_profile(user_id):
    return USERS_DB["risk_profiles"].get(str(user_id))


def add_price_alert(user_id, symbol, target, current_price):
    uid_str = str(user_id)
    user_alerts = [a for a in USERS_DB["price_alerts"] if str(a.get("user_id")) == uid_str]
    if len(user_alerts) >= 5:
        return False, "Maksimal 5 alert aktif per akun. Ketik /delalert untuk menghapus alert lama."

    condition = "ABOVE" if float(target) >= float(current_price) else "BELOW"
    now = datetime.now(WIB)
    alert_item = {
        "id": f"{uid_str}_{int(now.timestamp())}",
        "user_id": int(user_id),
        "symbol": symbol,
        "target": round(float(target), 4),
        "condition": condition,
        "created_at": now.strftime("%d/%m %H:%M WIB")
    }
    USERS_DB["price_alerts"].append(alert_item)
    sync_users()
    return True, "Alert berhasil dipasang."


def get_user_alerts(user_id):
    uid_str = str(user_id)
    return [a for a in USERS_DB["price_alerts"] if str(a.get("user_id")) == uid_str]


def clear_user_alerts(user_id):
    uid_str = str(user_id)
    before = len(USERS_DB["price_alerts"])
    USERS_DB["price_alerts"] = [a for a in USERS_DB["price_alerts"] if str(a.get("user_id")) != uid_str]
    removed = before - len(USERS_DB["price_alerts"])
    if removed > 0:
        sync_users()
    return removed


def build_locked_message_and_markup(user):
    nama = html.escape(user.first_name or "Trader")
    text = (
        f"🔒 <b>AKSES TERKUNCI — HALO {nama}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Jatah trial gratis atau masa aktif VIP kamu sudah habis.\n\n"
        f"🆔 ID Telegram Kamu: <code>{user.id}</code>\n"
        "Silakan kirim ID di atas ke Admin untuk perpanjangan akses."
    )
    buttons = []
    if USERS_DB.get("admin_username"):
        buttons.append([
            InlineKeyboardButton("💬 Hubungi Admin", url=f"https://t.me/{USERS_DB['admin_username']}")
        ])
    return text, InlineKeyboardMarkup(buttons) if buttons else None
