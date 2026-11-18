# -*- coding: utf-8 -*-
# ============================================================
# MASTER SMC SCALPING BOT — MODULAR EDITION
# Entry Point Utama (Tidak perlu mengubah .github/workflows/bot.yml)
# ============================================================

import sys
import html
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import (
    BOT_TOKEN, GEMINI_API_KEY, GITHUB_TOKEN, GITHUB_REPOSITORY,
    MARKETS, TIMEFRAMES, log, format_price, today_wib_date, parse_date_ymd
)
from smc_engine import (
    analyze_market, create_report, analyze_confluence_pro, get_latest_price
)
from fundamental_ai import get_cached_calendar, get_cached_news
from user_manager import (
    USERS_DB, load_users_db, sync_users, is_admin, get_active_vip_ids,
    check_user_access, consume_trial_if_needed, set_user_risk_profile,
    get_user_risk_profile, add_price_alert, get_user_alerts,
    clear_user_alerts, build_locked_message_and_markup
)

from datetime import datetime, timedelta
from config import WIB

LAST_BROADCAST_SIGNALS = {}


# ============================================================
# INLINE KEYBOARD MENUS
# ============================================================

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🥇 XAUUSD", callback_data="market_XAUUSD"),
            InlineKeyboardButton("₿ BTCUSD", callback_data="market_BTCUSD")
        ],
        [
            InlineKeyboardButton("⚡ Confluence XAU (H4+H1+M15)", callback_data="conf_XAUUSD")
        ],
        [
            InlineKeyboardButton("⚡ Confluence BTC (H4+H1+M15)", callback_data="conf_BTCUSD")
        ],
        [
            InlineKeyboardButton("📅 Kalender Ekonomi US", callback_data="calendar"),
            InlineKeyboardButton("📰 Berita Market", callback_data="news")
        ],
        [
            InlineKeyboardButton("📖 Panduan & Perintah Bot", callback_data="help_commands"),
            InlineKeyboardButton("👤 Status Akun", callback_data="check_my_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def timeframe_menu(symbol):
    keyboard = []
    row = []
    for tf in TIMEFRAMES:
        row.append(InlineKeyboardButton(tf, callback_data=f"tf_{symbol}_{tf}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(f"⚡ Confluence Pro {symbol} (H4+H1+M15)", callback_data=f"conf_{symbol}")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def analysis_menu(symbol, timeframe):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ANALISA SEKARANG", callback_data=f"analyze_{symbol}_{timeframe}")],
        [InlineKeyboardButton(f"⚡ Confluence Pro {symbol}", callback_data=f"conf_{symbol}")],
        [
            InlineKeyboardButton("⬅️ Ganti TF", callback_data=f"market_{symbol}"),
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")
        ]
    ])


def confluence_menu(symbol):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Confluence", callback_data=f"conf_{symbol}")],
        [
            InlineKeyboardButton(f"📊 Pilih TF {symbol}", callback_data=f"market_{symbol}"),
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")
        ]
    ])


def info_menu(refresh_action):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Data", callback_data=refresh_action),
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")
        ]
    ])


def help_menu(is_user_admin=False):
    buttons = [
        [
            InlineKeyboardButton("🥇 Analisa XAUUSD", callback_data="market_XAUUSD"),
            InlineKeyboardButton("₿ Analisa BTCUSD", callback_data="market_BTCUSD")
        ],
        [InlineKeyboardButton("🏠 Kembali ke Menu Utama", callback_data="menu")]
    ]
    return InlineKeyboardMarkup(buttons)


def build_help_text(user_id):
    text = (
        "📖 <b>PANDUAN &amp; DAFTAR PERINTAH BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1️⃣ PERINTAH MEMBER &amp; TRADING:</b>\n\n"
        "• <code>/start</code> — Membuka menu utama bot.\n"
        "• <code>/menu</code> — Menampilkan tombol pilihan market &amp; berita.\n"
        "• <code>/status</code> — Cek sisa masa aktif VIP &amp; profil risiko kamu.\n"
        "• <code>/panduan</code> — Menampilkan halaman panduan ini.\n\n"
        "💼 <b>Kalkulator Lot Otomatis:</b>\n"
        "• <code>/setrisk 500 1</code>\n"
        "  <i>Mengatur modal $500 dengan batas risiko 1% per entry. Setiap laporan analisa akan otomatis menghitung ukuran Lot yang aman.</i>\n\n"
        "🔔 <b>Alert Harga Pribadi (Watchlist):</b>\n"
        "• <code>/alert XAUUSD 2650.50</code>\n"
        "  <i>Memasang pengingat otomatis saat harga XAUUSD menyentuh 2650.50 (bisa untuk XAUUSD / BTCUSD).</i>\n"
        "• <code>/myalert</code> — Melihat daftar alert harga yang sedang aktif.\n"
        "• <code>/delalert</code> — Menghapus semua alert harga milikmu.\n\n"
        "⚡ <b>Fitur Otomatis VIP:</b>\n"
        "• <b>Confluence Pro:</b> Menggabungkan analisa H4 + H1 + M15 dalam sekali klik.\n"
        "• <b>Auto-Scanner Sinyal A+:</b> Bot memindai H1 &amp; M15 tiap 15 menit dan otomatis mengirim notifikasi ke Member VIP jika muncul setup minimal 5/7 konfirmasi."
    )

    if is_admin(user_id):
        text += (
            "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            "👑 <b>PERINTAH KHUSUS ADMIN:</b>\n"
            "• <code>/admin</code> — Panel statistik member.\n"
            "• <code>/add ID HARI NAMA</code> — Tambah member VIP.\n"
            "• <code>/perpanjang ID HARI</code> — Tambah masa aktif.\n"
            "• <code>/hapus ID</code> — Hapus member.\n"
            "• <code>/cekuser ID</code> — Cek detail user.\n"
            "• <code>/listuser</code> — Daftar semua member.\n"
            "• <code>/settrial JUMLAH</code> — Atur jatah trial gratis.\n"
            "• <code>/setcontact USERNAME</code> — Atur kontak tombol aktivasi."
        )
    return text


# ============================================================
# USER COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, role, status_info = check_user_access(user.id)

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

    admin_hint = "\n👑 Ketik /admin untuk kelola member." if role == "ADMIN" else ""
    await update.message.reply_html(
        "🤖 <b>MASTER SMC SCALPING BOT PRO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Kamu: <code>{user.id}</code>\n"
        f"📌 Status: <b>{html.escape(status_info)}</b>\n\n"
        "• <b>7 Metode Murni SMC:</b> OB Valid, FVG, BOS, SNR, SBR/RBS, SND, RBR/DBD\n"
        "• <b>Fitur Pro:</b> Multi-TF Confluence, Radar Killzone &amp; Sweep, Kalkulator Lot, Alert Harga, &amp; AI Fundamental"
        f"{admin_hint}",
        reply_markup=main_menu()
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, _, _ = check_user_access(user.id)
    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return
    await update.message.reply_text("Pilih menu:", reply_markup=main_menu())


async def panduan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(
        build_help_text(user.id),
        reply_markup=help_menu(is_admin(user.id))
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _, _, user_status = check_user_access(user.id)
    risk = get_user_risk_profile(user.id)
    risk_str = f"Modal ${risk['balance']:,.0f} | Risk {risk['risk_pct']:.1f}%" if risk else "Belum diatur (/setrisk)"

    ai_status = f"Aktif (...{GEMINI_API_KEY[-4:]})" if GEMINI_API_KEY else "Tidak Terdeteksi"
    gh_status = "Aktif (Auto-Commit)" if (GITHUB_TOKEN and GITHUB_REPOSITORY) else "Lokal Saja"

    await update.message.reply_html(
        "🟢 <b>STATUS BOT &amp; AKUN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID Kamu: <code>{user.id}</code>\n"
        f"👤 Akses: <b>{html.escape(user_status)}</b>\n"
        f"💼 Profil Lot: <b>{risk_str}</b>\n"
        f"🔔 Alert Aktif: <b>{len(get_user_alerts(user.id))}/5</b>\n"
        f"🤖 Gemini AI: <b>{ai_status}</b>\n"
        f"☁️ Sync GitHub: <b>{gh_status}</b>"
    )


async def cmd_set_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, _, _ = check_user_access(user.id)
    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/setrisk 500 1</code>\n"
            "<i>(Artinya modal $500 dengan risiko 1% per transaksi)</i>"
        )
        return

    try:
        balance = float(args[0].replace(",", ""))
        risk_pct = float(args[1].replace("%", ""))
        if balance <= 0 or risk_pct <= 0 or risk_pct > 50:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("⚠️ Masukkan angka modal (> 0) dan persen risiko (0.1 - 50) yang valid.")
        return

    _, sync_msg = await asyncio.to_thread(set_user_risk_profile, user.id, balance, risk_pct)
    risk_usd = balance * (risk_pct / 100.0)

    await update.message.reply_html(
        "💼 <b>PROFIL KALKULATOR LOT DISIMPAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Modal Akun: <b>${balance:,.2f}</b>\n"
        f"• Risiko per Entry: <b>{risk_pct:.1f}% (${risk_usd:,.2f})</b>\n"
        f"• Status: <i>{html.escape(sync_msg)}</i>\n\n"
        "Sekarang setiap laporan analisa SMC akan otomatis menampilkan ukuran Lot rekomendasi."
    )


async def cmd_set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, _, _ = check_user_access(user.id)
    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_html(
            "⚠️ Format salah.\nContoh: <code>/alert XAUUSD 2650.50</code> atau <code>/alert BTCUSD 92000</code>"
        )
        return

    symbol = args[0].strip().upper()
    if symbol not in MARKETS:
        await update.message.reply_text("⚠️ Market harus XAUUSD atau BTCUSD.")
        return

    try:
        target = float(args[1].replace(",", ""))
        if target <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("⚠️ Harga target harus berupa angka positif.")
        return

    try:
        current_price = await asyncio.to_thread(get_latest_price, symbol)
    except Exception as err:
        await update.message.reply_text(f"❌ Gagal mengambil harga saat ini: {err}")
        return

    ok, msg = await asyncio.to_thread(add_price_alert, user.id, symbol, target, current_price)
    if not ok:
        await update.message.reply_text(f"⚠️ {msg}")
        return

    await update.message.reply_html(
        "🔔 <b>ALERT HARGA DIPASANG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Market: <b>{symbol}</b>\n"
        f"• Harga Sekarang: <b>{format_price(current_price)}</b>\n"
        f"• Target Alert: <b>{format_price(target)}</b>\n\n"
        "Bot akan mengecek harga tiap 1 menit dan mengirim notifikasi otomatis saat target tersentuh."
    )


async def cmd_my_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    alerts = get_user_alerts(user.id)
    if not alerts:
        await update.message.reply_html(
            "📭 Kamu belum memiliki alert harga aktif.\nContoh pasang: <code>/alert XAUUSD 2650</code>"
        )
        return

    lines = ["🔔 <b>DAFTAR ALERT HARGA AKTIF KAMU</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for i, a in enumerate(alerts, 1):
        arah = "Naik ke ≥" if a.get("condition") == "ABOVE" else "Turun ke ≤"
        lines.append(f"{i}. <b>{a['symbol']}</b> — {arah} <code>{format_price(a['target'])}</code> ({a.get('created_at', '-')})")
    lines.append("\nKetik <code>/delalert</code> untuk menghapus semua alert.")
    await update.message.reply_html("\n".join(lines))


async def cmd_del_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    removed = await asyncio.to_thread(clear_user_alerts, user.id)
    await update.message.reply_text(f"🗑️ {removed} alert harga berhasil dihapus.")


# ============================================================
# ADMIN COMMANDS
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

    _, sync_msg = await asyncio.to_thread(sync_users)
    await update.message.reply_html(
        "👑 <b>ADMIN BERHASIL DIKLAIM!</b>\n"
        f"ID Admin: <code>{user.id}</code>\n"
        f"Status Simpan: <i>{html.escape(sync_msg)}</i>\n\n"
        "Ketik /admin untuk melihat panel kelola member.",
        reply_markup=main_menu()
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    today = today_wib_date()
    aktif = sum(
        1 for m in USERS_DB["members"].values()
        if parse_date_ymd(m.get("expired", "")) and today <= parse_date_ymd(m.get("expired", ""))
    )
    expired = len(USERS_DB["members"]) - aktif
    contact_disp = f"@{USERS_DB['admin_username']}" if USERS_DB["admin_username"] else "(Belum diatur)"

    await update.message.reply_html(
        "👑 <b>PANEL ADMIN BOT SMC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Member VIP Aktif: <b>{aktif}</b> | Expired: <b>{expired}</b>\n"
        f"• Jatah Trial User Baru: <b>{USERS_DB['trial_limit']}x</b>\n"
        f"• Kontak Admin: <b>{html.escape(contact_disp)}</b>\n\n"
        "📋 <b>PERINTAH KELOLA MEMBER:</b>\n"
        "• <code>/add ID HARI NAMA</code>\n"
        "• <code>/perpanjang ID HARI</code>\n"
        "• <code>/cekuser ID</code>\n"
        "• <code>/hapus ID</code>\n"
        "• <code>/listuser</code>\n"
        "• <code>/settrial JUMLAH</code>\n"
        "• <code>/setcontact USERNAME</code>"
    )


async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await update.message.reply_html("⚠️ Contoh: <code>/add 987654321 30 Budi</code>")
        return

    target_id = args[0].strip()
    days = int(args[1])
    nama = " ".join(args[2:]).strip() if len(args) >= 3 else "Member VIP"
    if days <= 0:
        await update.message.reply_text("⚠️ Jumlah hari harus > 0.")
        return

    exp_str = (today_wib_date() + timedelta(days=days)).strftime("%Y-%m-%d")
    USERS_DB["members"][target_id] = {
        "nama": nama,
        "expired": exp_str,
        "updated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    }
    _, sync_msg = await asyncio.to_thread(sync_users)

    await update.message.reply_html(
        "✅ <b>MEMBER DITAMBAHKAN</b>\n"
        f"🆔 ID: <code>{target_id}</code> | 👤 <b>{html.escape(nama)}</b>\n"
        f"📅 Aktif s/d: <b>{exp_str}</b> ({days} hari)\n"
        f"☁️ <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_extend_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await update.message.reply_html("⚠️ Contoh: <code>/perpanjang 987654321 30</code>")
        return

    target_id = args[0].strip()
    add_days = int(args[1])
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
    USERS_DB["members"][target_id] = {
        "nama": nama,
        "expired": new_exp_str,
        "updated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    }
    _, sync_msg = await asyncio.to_thread(sync_users)

    await update.message.reply_html(
        "🔄 <b>MASA AKTIF DIPERPANJANG</b>\n"
        f"🆔 ID: <code>{target_id}</code> | 👤 <b>{html.escape(nama)}</b>\n"
        f"📅 Expired Baru: <b>{new_exp_str}</b> (Sisa {(new_exp - today).days} hari)\n"
        f"☁️ <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html("⚠️ Contoh: <code>/hapus 987654321</code>")
        return

    target_id = args[0].strip()
    removed = USERS_DB["members"].pop(target_id, None)
    if not removed:
        await update.message.reply_html(f"ℹ️ ID <code>{target_id}</code> tidak ditemukan.")
        return

    _, sync_msg = await asyncio.to_thread(sync_users)
    await update.message.reply_html(
        f"🗑️ Member <code>{target_id}</code> (<b>{html.escape(removed.get('nama', 'Member'))}</b>) dihapus.\n"
        f"☁️ <i>{html.escape(sync_msg)}</i>"
    )


async def cmd_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html("⚠️ Contoh: <code>/cekuser 987654321</code>")
        return

    target_id = args[0].strip()
    member = USERS_DB["members"].get(target_id)
    today = today_wib_date()

    if isinstance(member, dict):
        exp_str = str(member.get("expired", "-"))
        exp_date = parse_date_ymd(exp_str)
        st = f"🟢 AKTIF (Sisa {(exp_date - today).days} hr)" if (exp_date and today <= exp_date) else "🔴 EXPIRED"
        await update.message.reply_html(
            f"🆔 <code>{target_id}</code> • <b>{html.escape(member.get('nama', 'Member'))}</b>\n"
            f"Status: <b>{st}</b> | Exp: <b>{exp_str}</b>"
        )
    else:
        used = int(USERS_DB["trials"].get(target_id, 0))
        await update.message.reply_html(
            f"🆔 <code>{target_id}</code> • Non-VIP (Trial terpakai: {used}/{USERS_DB['trial_limit']})"
        )


async def cmd_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return

    members = USERS_DB["members"]
    if not members:
        await update.message.reply_text("📭 Belum ada member VIP.")
        return

    today = today_wib_date()
    rows = []
    for uid_str, info in members.items():
        nama = html.escape(str(info.get("nama", "Member")))
        exp_str = str(info.get("expired", "-"))
        exp_date = parse_date_ymd(exp_str)
        if exp_date and today <= exp_date:
            sisa = (exp_date - today).days
            rows.append((0, sisa, f"🟢 <code>{uid_str}</code> • <b>{nama}</b> (s/d {exp_str} • {sisa} hr)"))
        else:
            rows.append((1, 0, f"🔴 <code>{uid_str}</code> • <b>{nama}</b> (HABIS {exp_str})"))

    rows.sort(key=lambda x: (x[0], -x[1]))
    msg = f"👥 <b>DAFTAR MEMBER ({len(rows)})</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(r[2] for r in rows)
    await update.message.reply_html(msg[:3900])


async def cmd_set_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return
    args = context.args or []
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_html("⚠️ Contoh: <code>/settrial 2</code>")
        return
    USERS_DB["trial_limit"] = int(args[0])
    _, sync_msg = await asyncio.to_thread(sync_users)
    await update.message.reply_html(f"⚙️ Trial diatur ke <b>{USERS_DB['trial_limit']}x</b>.\n☁️ <i>{html.escape(sync_msg)}</i>")


async def cmd_set_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return
    args = context.args or []
    if len(args) < 1:
        await update.message.reply_html("⚠️ Contoh: <code>/setcontact suryaditj</code>")
        return
    USERS_DB["admin_username"] = args[0].strip().lstrip("@")
    _, sync_msg = await asyncio.to_thread(sync_users)
    await update.message.reply_html(f"💬 Kontak admin diatur ke <b>@{html.escape(USERS_DB['admin_username'])}</b>.\n☁️ <i>{html.escape(sync_msg)}</i>")


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()
    data = query.data

    if data == "help_commands":
        await query.message.reply_html(
            build_help_text(user.id),
            reply_markup=help_menu(is_admin(user.id))
        )
        return

    if data == "check_my_status":
        allowed, _, status_info = check_user_access(user.id)
        if allowed:
            await query.message.reply_html(
                f"✅ <b>AKSES AKTIF</b>\n🆔 ID Kamu: <code>{user.id}</code>\n📌 Status: <b>{html.escape(status_info)}</b>",
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
        await query.message.reply_text("Pilih market:", reply_markup=main_menu())
        return

    if data.startswith("market_"):
        symbol = data.replace("market_", "")
        if symbol not in MARKETS:
            return
        await query.message.reply_text(
            f"Market: {symbol}\nPilih timeframe atau jalankan Confluence Pro:",
            reply_markup=timeframe_menu(symbol)
        )
        return

    if data.startswith("tf_"):
        _, symbol, timeframe = data.split("_")
        if symbol not in MARKETS or timeframe not in TIMEFRAMES:
            return
        await query.message.reply_text(
            f"Market: {symbol} | Timeframe: {timeframe}\nTekan tombol untuk analisis:",
            reply_markup=analysis_menu(symbol, timeframe)
        )
        return

    if data.startswith("conf_"):
        allowed, role, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        symbol = data.replace("conf_", "")
        if symbol not in MARKETS:
            return

        processing = await query.message.reply_text(
            f"⚡ Menjalankan Confluence Pro {symbol} (H4 + H1 + M15)..."
        )
        try:
            risk_profile = get_user_risk_profile(user.id)
            report_text = await asyncio.to_thread(analyze_confluence_pro, symbol, risk_profile)

            if role == "TRIAL":
                await asyncio.to_thread(consume_trial_if_needed, user.id)
                _, _, new_status = check_user_access(user.id)
                report_text += f"\n\n🎁 {new_status}"

            await processing.edit_text(report_text, reply_markup=confluence_menu(symbol))
        except Exception as err:
            await processing.edit_text(f"❌ Confluence gagal:\n{str(err)[:2000]}")
        return

    if data.startswith("analyze_"):
        allowed, role, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        _, symbol, timeframe = data.split("_")
        if symbol not in MARKETS or timeframe not in TIMEFRAMES:
            return

        processing = await query.message.reply_text(
            f"⏳ Menganalisis {symbol} {timeframe} (100 candle tertutup)..."
        )
        try:
            result = await asyncio.to_thread(analyze_market, symbol, timeframe)
            risk_profile = get_user_risk_profile(user.id)
            report_text = create_report(result, risk_profile=risk_profile)

            if role == "TRIAL":
                await asyncio.to_thread(consume_trial_if_needed, user.id)
                _, _, new_status = check_user_access(user.id)
                report_text += f"\n\n🎁 {new_status}"

            await processing.edit_text(report_text, reply_markup=analysis_menu(symbol, timeframe))
        except Exception as err:
            await processing.edit_text(f"❌ ANALISIS GAGAL:\n{str(err)[:2000]}")
        return

    if data in ("calendar", "calendar_refresh"):
        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        force = (data == "calendar_refresh")
        msg = await query.message.reply_text("⏳ Mengambil jadwal ekonomi US...")
        try:
            formatted = await asyncio.to_thread(get_cached_calendar, force)
            await msg.edit_text(formatted, parse_mode="HTML", reply_markup=info_menu("calendar_refresh"))
        except Exception as err:
            await msg.edit_text(f"❌ Kalender gagal:\n{str(err)[:2000]}", reply_markup=info_menu("calendar_refresh"))
        return

    if data in ("news", "news_refresh"):
        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        force = (data == "news_refresh")
        msg = await query.message.reply_text("⏳ Membaca berita market US...")
        try:
            formatted = await asyncio.to_thread(get_cached_news, force)
            await msg.edit_text(formatted, parse_mode="HTML", reply_markup=info_menu("news_refresh"))
        except Exception as err:
            await msg.edit_text(f"❌ Berita gagal:\n{str(err)[:2000]}", reply_markup=info_menu("news_refresh"))
        return


# ============================================================
# BACKGROUND WORKERS (WATCHLIST ALERT + AUTO-SCANNER SINYAL A+)
# ============================================================

async def background_scanner_and_alerts(application):
    await asyncio.sleep(20)
    tick = 0

    while True:
        try:
            # 1. Cek Price Alert Pribadi setiap 1 menit
            if USERS_DB["price_alerts"]:
                latest_prices = {}
                for sym in MARKETS:
                    if any(a.get("symbol") == sym for a in USERS_DB["price_alerts"]):
                        try:
                            latest_prices[sym] = await asyncio.to_thread(get_latest_price, sym)
                        except Exception:
                            pass

                triggered_ids = set()
                for alert in list(USERS_DB["price_alerts"]):
                    sym = alert.get("symbol")
                    cur = latest_prices.get(sym)
                    if cur is None:
                        continue
                    target = float(alert.get("target", 0))
                    cond = alert.get("condition")
                    hit = (cond == "ABOVE" and cur >= target) or (cond == "BELOW" and cur <= target)

                    if hit:
                        triggered_ids.add(alert.get("id"))
                        uid = int(alert.get("user_id"))
                        try:
                            await application.bot.send_message(
                                chat_id=uid,
                                text=(
                                    f"🔔 <b>ALERT HARGA TERSENTUH — {sym}</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"• Target Alert: <b>{format_price(target)}</b>\n"
                                    f"• Harga Saat Ini: <b>{format_price(cur)}</b>\n\n"
                                    f"Tekan tombol di bawah untuk langsung analisa zona ini:"
                                ),
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton(f"⚡ Confluence Pro {sym}", callback_data=f"conf_{sym}")]
                                ])
                            )
                        except Exception:
                            pass

                if triggered_ids:
                    USERS_DB["price_alerts"] = [
                        a for a in USERS_DB["price_alerts"] if a.get("id") not in triggered_ids
                    ]
                    await asyncio.to_thread(sync_users)

            # 2. Auto-Scanner Sinyal A+ (Minimal 5/7 Konfirmasi) setiap 15 menit
            if tick % 15 == 0:
                vip_ids = get_active_vip_ids()
                if vip_ids:
                    for sym in MARKETS:
                        for tf in ("H1", "M15"):
                            try:
                                res = await asyncio.to_thread(analyze_market, sym, tf)
                                if res["direction"] in ("BUY", "SELL") and res["confirmations"] >= 5:
                                    sig_key = f"{sym}_{tf}_{res['direction']}_{round(res['entry_low'], 1)}"
                                    if LAST_BROADCAST_SIGNALS.get(f"{sym}_{tf}") != sig_key:
                                        LAST_BROADCAST_SIGNALS[f"{sym}_{tf}"] = sig_key
                                        alert_msg = create_report(
                                            res,
                                            title_prefix=f"🚨 AUTO-SCANNER SINYAL A+ ({sym} {tf})"
                                        )
                                        for uid in vip_ids:
                                            try:
                                                await application.bot.send_message(
                                                    chat_id=uid,
                                                    text=alert_msg,
                                                    reply_markup=analysis_menu(sym, tf)
                                                )
                                            except Exception:
                                                pass
                            except Exception as scan_err:
                                log.warning("Scanner %s %s error: %s", sym, tf, scan_err)

        except Exception as loop_err:
            log.error("Background loop error: %s", loop_err)

        tick += 1
        await asyncio.sleep(60)


# ============================================================
# STARTUP & MAIN ENTRY
# ============================================================

async def error_handler(update, context):
    log.error("Telegram error: %s", context.error, exc_info=context.error)


async def post_init(application):
    log.info("======================================")
    log.info("MASTER SMC SCALPING BOT PRO STARTED")
    log.info("Modules: config, smc_engine, fundamental_ai, user_manager, main")
    log.info("Admin IDs: %s | VIP Members: %s", USERS_DB["admin_ids"], len(USERS_DB["members"]))
    log.info("======================================")
    asyncio.create_task(background_scanner_and_alerts(application))


def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN tidak ditemukan!")
        sys.exit(1)

    load_users_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # User & Trading Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("panduan", panduan_command))
    application.add_handler(CommandHandler("help", panduan_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("setrisk", cmd_set_risk))
    application.add_handler(CommandHandler("alert", cmd_set_alert))
    application.add_handler(CommandHandler("myalert", cmd_my_alerts))
    application.add_handler(CommandHandler("delalert", cmd_del_alerts))

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

    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot dihentikan.")
    except Exception:
        log.exception("FATAL ERROR")
        raise
