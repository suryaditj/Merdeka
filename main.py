# -*- coding: utf-8 -*-
# ============================================================
# SMC HYBRID ANALISIS BOT — CLEAN MODULAR EDITION
# ============================================================

import sys
import html
import asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import (
    BOT_TOKEN, GEMINI_API_KEY, GITHUB_TOKEN, GITHUB_REPOSITORY,
    MARKETS, MACRO_MARKETS, WIB, BULAN_ID, log, format_price,
    today_wib_date, parse_date_ymd
)
from smc_engine import (
    analyze_hybrid_3tf, format_hybrid_signal_report, get_latest_price, get_candles
)
from fundamental_ai import (
    get_cached_calendar, get_macro_market_report
)
from session_tools import (
    build_session_report, build_lot_calculator_guide, calculate_lot_for_pair,
    load_signals_db, evaluate_open_signals, build_winrate_report
)
from user_manager import (
    USERS_DB, load_users_db, sync_users, is_admin, is_free_mode, toggle_free_mode,
    check_user_access, consume_trial_if_needed, set_user_risk_profile,
    get_user_risk_profile, add_price_alert, get_user_alerts,
    clear_user_alerts, build_locked_message_and_markup
)


# ============================================================
# INLINE KEYBOARD MENUS
# ============================================================

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🪙 XAUUSD", callback_data="market_XAUUSD"),
            InlineKeyboardButton("₿ BTCUSD", callback_data="market_BTCUSD")
        ],
        [
            InlineKeyboardButton("🛢️ Cek USOIL", callback_data="macro_USOIL"),
            InlineKeyboardButton("💵 Cek DXY", callback_data="macro_DXY")
        ],
        [
            InlineKeyboardButton("📰 Berita Ekonomi US", callback_data="calendar"),
            InlineKeyboardButton("⏰ Sesi Market (WIB)", callback_data="session_wib")
        ],
        [
            InlineKeyboardButton("🧮 Kalkulator Lot", callback_data="lot_calc"),
            InlineKeyboardButton("📈 Rekap Winrate", callback_data="winrate")
        ],
        [
            InlineKeyboardButton("📖 Panduan & Perintah", callback_data="help_commands"),
            InlineKeyboardButton("👤 Status Akun", callback_data="check_my_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_main_menu_text():
    now = datetime.now(WIB)
    bulan = BULAN_ID.get(now.month, now.strftime("%b"))
    waktu_str = f"{now.day:02d} {bulan} {now.year} • {now.strftime('%H:%M')} WIB"
    return (
        "🤖 <b>SMC HYBRID ANALISIS BOT</b>\n"
        f"🕒 Waktu Server: <b>{waktu_str}</b>\n\n"
        "Silakan pilih market untuk analisa sinyal atau cek kondisi makro pasar:"
    )


def method_menu(symbol):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scalping (H1 • M30 • M15)", callback_data=f"run_{symbol}_SCALPING")],
        [InlineKeyboardButton("🏛️ Intra Day (D1 • H4 • H1)", callback_data=f"run_{symbol}_INTRADAY")],
        [InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="menu")]
    ])


def signal_result_menu(symbol, mode):
    other_symbol = "BTCUSD" if symbol == "XAUUSD" else "XAUUSD"
    other_icon = "₿" if other_symbol == "BTCUSD" else "🪙"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Analisa", callback_data=f"run_{symbol}_{mode}"),
            InlineKeyboardButton("🔁 Ganti Metode", callback_data=f"market_{symbol}")
        ],
        [
            InlineKeyboardButton(f"{other_icon} Ganti {other_symbol}", callback_data=f"market_{other_symbol}"),
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


def admin_panel_menu():
    mode_label = "🔓 Mode Akses: GRATIS SEMUA (Klik untuk Kunci VIP)" if is_free_mode() else "🔒 Mode Akses: KHUSUS VIP (Klik untuk Buka Gratis)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mode_label, callback_data="admin_toggle_free")],
        [InlineKeyboardButton("🔄 Refresh Panel Admin", callback_data="admin_refresh")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]
    ])


def build_admin_panel_text():
    today = today_wib_date()
    aktif = sum(
        1 for m in USERS_DB["members"].values()
        if parse_date_ymd(m.get("expired", "")) and today <= parse_date_ymd(m.get("expired", ""))
    )
    expired = len(USERS_DB["members"]) - aktif
    contact_disp = f"@{USERS_DB['admin_username']}" if USERS_DB["admin_username"] else "(Belum diatur)"
    mode_str = "🟢 GRATIS SEMUA (Semua fitur terbuka bebas)" if is_free_mode() else f"🔒 KHUSUS VIP (Trial gratis: {USERS_DB['trial_limit']}x)"

    return (
        "👑 <b>PANEL ADMIN BOT SMC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Status Akses Bot: <b>{mode_str}</b>\n"
        f"• Member VIP Aktif: <b>{aktif}</b> | Expired: <b>{expired}</b>\n"
        f"• Kontak Admin: <b>{html.escape(contact_disp)}</b>\n\n"
        "📋 <b>PERINTAH KELOLA MEMBER:</b>\n"
        "• <code>/add ID HARI NAMA</code> — Tambah member VIP\n"
        "• <code>/perpanjang ID HARI</code> — Tambah masa aktif\n"
        "• <code>/cekuser ID</code> — Cek detail user\n"
        "• <code>/hapus ID</code> — Hapus member\n"
        "• <code>/listuser</code> — Daftar semua member\n"
        "• <code>/settrial JUMLAH</code> — Atur jatah trial\n"
        "• <code>/setcontact USERNAME</code> — Atur kontak tombol aktivasi"
    )


def help_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪙 Analisa XAUUSD", callback_data="market_XAUUSD"),
            InlineKeyboardButton("₿ Analisa BTCUSD", callback_data="market_BTCUSD")
        ],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]
    ])


def build_help_text(user_id):
    text = (
        "📖 <b>PANDUAN &amp; DAFTAR PERINTAH BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>1️⃣ METODE ANALISA SINYAL:</b>\n"
        "• <b>Scalping (H1 • M30 • M15):</b> Analisa gabungan 3 timeframe untuk eksekusi cepat.\n"
        "• <b>Intra Day (D1 • H4 • H1):</b> Analisa gabungan 3 timeframe untuk target harian lebih lebar.\n\n"
        "<b>2️⃣ PERINTAH UTAMA:</b>\n"
        "• <code>/start</code> atau <code>/menu</code> — Membuka menu utama bot.\n"
        "• <code>/status</code> — Cek status akses akun &amp; profil risiko.\n"
        "• <code>/panduan</code> — Membuka halaman panduan ini.\n\n"
        "<b>3️⃣ KALKULATOR LOT &amp; RISIKO:</b>\n"
        "• <code>/setrisk 500 2</code> — Simpan profil modal $500 &amp; risiko 2% (muncul otomatis di setiap sinyal).\n"
        "• <code>/lot XAUUSD 200 2 5</code> — Hitung lot cepat (Market, Modal, Risiko%, Jarak SL).\n\n"
        "<b>4️⃣ ALERT HARGA PRIBADI:</b>\n"
        "• <code>/alert XAUUSD 2650.50</code> — Pasang pengingat harga.\n"
        "• <code>/myalert</code> — Lihat daftar alert aktif.\n"
        "• <code>/delalert</code> — Hapus semua alert."
    )

    if is_admin(user_id):
        text += (
            "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            "👑 <b>PERINTAH KHUSUS ADMIN:</b>\n"
            "• <code>/admin</code> — Panel kontrol mode Gratis/VIP &amp; statistik.\n"
            "• <code>/add ID HARI NAMA</code> — Tambah member VIP.\n"
            "• <code>/perpanjang ID HARI</code> — Tambah masa aktif.\n"
            "• <code>/hapus ID</code> — Hapus member.\n"
            "• <code>/cekuser ID</code> — Cek detail user.\n"
            "• <code>/listuser</code> — Daftar semua member.\n"
            "• <code>/settrial JUMLAH</code> — Atur jatah trial.\n"
            "• <code>/setcontact USERNAME</code> — Atur username kontak admin."
        )
    return text


# ============================================================
# USER COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, role, _ = check_user_access(user.id)

    if not USERS_DB["admin_ids"]:
        await update.message.reply_html(
            "🤖 <b>SMC HYBRID ANALISIS BOT</b>\n\n"
            "⚠️ <b>Admin belum diklaim.</b>\n"
            f"ID Telegram kamu: <code>{user.id}</code>\n\n"
            "Ketik <code>/claimadmin</code> sekarang untuk mengunci akunmu sebagai <b>Admin Utama</b>."
        )
        return

    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return

    admin_hint = "\n\n👑 <i>Ketik /admin untuk buka panel kontrol &amp; mode Gratis/VIP.</i>" if role == "ADMIN" else ""
    await update.message.reply_html(
        build_main_menu_text() + admin_hint,
        reply_markup=main_menu()
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    allowed, _, _ = check_user_access(user.id)
    if not allowed:
        text, markup = build_locked_message_and_markup(user)
        await update.message.reply_html(text, reply_markup=markup)
        return
    await update.message.reply_html(build_main_menu_text(), reply_markup=main_menu())


async def panduan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(build_help_text(user.id), reply_markup=help_menu())


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
        f"🧮 Profil Lot: <b>{risk_str}</b>\n"
        f"⏰ Alert Harga Aktif: <b>{len(get_user_alerts(user.id))}/5</b>\n"
        f"🤖 Gemini AI: <b>{ai_status}</b>\n"
        f"☁️ Sync GitHub: <b>{gh_status}</b>",
        reply_markup=main_menu()
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
            "⚠️ Format salah.\nContoh: <code>/setrisk 500 2</code>\n"
            "<i>(Artinya modal $500 dengan risiko 2% per transaksi)</i>"
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
        "🧮 <b>PROFIL KALKULATOR LOT DISIMPAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Modal Akun: <b>${balance:,.2f}</b>\n"
        f"• Risiko per Entry: <b>{risk_pct:.1f}% (${risk_usd:,.2f})</b>\n"
        f"• Status: <i>{html.escape(sync_msg)}</i>\n\n"
        "Sekarang setiap hasil analisa XAUUSD &amp; BTCUSD akan otomatis menampilkan ukuran Lot rekomendasi."
    )


async def cmd_calc_lot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 4:
        await update.message.reply_html(
            "⚠️ Format salah.\n"
            "Ketik: <code>/lot MARKET MODAL RISIKO% JARAK_SL</code>\n"
            "Contoh: <code>/lot XAUUSD 200 2 5</code>"
        )
        return

    sym = args[0].strip().upper()
    if sym not in MARKETS:
        await update.message.reply_text("⚠️ Market harus XAUUSD atau BTCUSD.")
        return

    try:
        bal = float(args[1].replace(",", ""))
        rp = float(args[2].replace("%", ""))
        sl_pts = float(args[3].replace(",", ""))
        if bal <= 0 or rp <= 0 or sl_pts <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("⚠️ Pastikan modal, risiko%, dan jarak SL berupa angka positif.")
        return

    lot, actual_loss, risk_usd, over_risk = calculate_lot_for_pair(sym, bal, rp, sl_pts)
    warn_txt = "\n⚠️ <i>Catatan: Lot minimum 0.01 melebihi batas risiko idealmu.</i>" if over_risk else ""

    await update.message.reply_html(
        f"🧮 <b>HASIL HITUNG LOT — {sym}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Modal: <b>${bal:,.2f}</b> | Batas Risiko: <b>{rp:.1f}% (${risk_usd:,.2f})</b>\n"
        f"• Jarak Stop Loss: <b>{sl_pts} poin</b>\n"
        f"• Ukuran Lot Aman: <b>{lot:.2f} Lot</b> (Estimasi SL: -${actual_loss:,.2f})"
        f"{warn_txt}"
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
        f"• Target Alert: <b>{format_price(target)}</b>"
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
        "Ketik /admin untuk membuka panel kontrol.",
        reply_markup=main_menu()
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Perintah ini khusus Admin.")
        return
    await update.message.reply_html(build_admin_panel_text(), reply_markup=admin_panel_menu())


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
# CALLBACK HANDLER (SATSET & HASIL ANALISA PERMANEN)
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()
    data = query.data

    if data == "admin_toggle_free":
        if not is_admin(user.id):
            return
        await asyncio.to_thread(toggle_free_mode)
        await query.message.edit_text(build_admin_panel_text(), parse_mode="HTML", reply_markup=admin_panel_menu())
        return

    if data == "admin_refresh":
        if not is_admin(user.id):
            return
        await query.message.edit_text(build_admin_panel_text(), parse_mode="HTML", reply_markup=admin_panel_menu())
        return

    if data == "help_commands":
        await query.message.reply_html(build_help_text(user.id), reply_markup=help_menu())
        return

    if data == "check_my_status":
        allowed, _, status_info = check_user_access(user.id)
        if allowed:
            risk = get_user_risk_profile(user.id)
            risk_str = f"Modal ${risk['balance']:,.0f} | Risk {risk['risk_pct']:.1f}%" if risk else "Belum diatur (/setrisk)"
            await query.message.reply_html(
                f"👤 <b>STATUS AKUN KAMU</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ID Kamu: <code>{user.id}</code>\n"
                f"📌 Status: <b>{html.escape(status_info)}</b>\n"
                f"🧮 Profil Lot: <b>{risk_str}</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]])
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
        await query.message.reply_html(build_main_menu_text(), reply_markup=main_menu())
        return

    if data.startswith("market_"):
        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        symbol = data.replace("market_", "")
        if symbol not in MARKETS:
            return
        sym_icon = "🪙" if symbol == "XAUUSD" else "₿"
        await query.message.reply_html(
            f"📊 <b>MARKET TERPILIH: {sym_icon} {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<b>PILIHAN METODE ANALISA:</b>",
            reply_markup=method_menu(symbol)
        )
        return

    if data.startswith("run_"):
        allowed, role, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        _, symbol, mode = data.split("_")
        if symbol not in MARKETS or mode not in ("SCALPING", "INTRADAY"):
            return

        tf_desc = "H1 • M30 • M15" if mode == "SCALPING" else "D1 • H4 • H1"
        processing = await query.message.reply_html(
            f"⚡ <i>Menganalisa {symbol} ({tf_desc})...</i>"
        )

        try:
            res = await asyncio.to_thread(analyze_hybrid_3tf, symbol, mode)
            risk_prof = get_user_risk_profile(user.id)
            report_text = format_hybrid_signal_report(res, risk_profile=risk_prof)

            if role == "TRIAL":
                await asyncio.to_thread(consume_trial_if_needed, user.id)
                _, _, new_status = check_user_access(user.id)
                report_text += f"\n\n🎁 <i>{html.escape(new_status)}</i>"

            await processing.edit_text(
                report_text,
                parse_mode="HTML",
                reply_markup=signal_result_menu(symbol, mode)
            )
        except Exception as err:
            await processing.edit_text(
                f"❌ <b>ANALISA GAGAL:</b>\n{html.escape(str(err)[:1500])}",
                parse_mode="HTML",
                reply_markup=signal_result_menu(symbol, mode)
            )
        return

    if data.startswith("macro_"):
        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        parts = data.split("_")
        symbol = parts[1]
        is_refresh = (len(parts) > 2 and parts[2] == "refresh")
        if symbol not in MACRO_MARKETS:
            return

        target_msg = query.message if is_refresh else await query.message.reply_html(f"⏳ <i>Memuat data {symbol}...</i>")
        try:
            report = await asyncio.to_thread(get_macro_market_report, symbol, is_refresh)
            await target_msg.edit_text(
                report,
                parse_mode="HTML",
                reply_markup=info_menu(f"macro_{symbol}_refresh")
            )
        except Exception as err:
            await target_msg.edit_text(
                f"❌ <b>Gagal mengambil data {symbol}:</b>\n{html.escape(str(err)[:1500])}",
                parse_mode="HTML",
                reply_markup=info_menu(f"macro_{symbol}_refresh")
            )
        return

    if data in ("calendar", "calendar_refresh"):
        allowed, _, _ = check_user_access(user.id)
        if not allowed:
            text, markup = build_locked_message_and_markup(user)
            await query.message.reply_html(text, reply_markup=markup)
            return

        is_refresh = (data == "calendar_refresh")
        target_msg = query.message if is_refresh else await query.message.reply_html("⏳ <i>Memuat jadwal berita ekonomi US (WIB)...</i>")
        try:
            formatted = await asyncio.to_thread(get_cached_calendar, is_refresh)
            await target_msg.edit_text(formatted, parse_mode="HTML", reply_markup=info_menu("calendar_refresh"))
        except Exception as err:
            await target_msg.edit_text(
                f"❌ <b>Gagal memuat berita ekonomi:</b>\n{html.escape(str(err)[:1500])}",
                parse_mode="HTML",
                reply_markup=info_menu("calendar_refresh")
            )
        return

    if data in ("session_wib", "session_wib_refresh"):
        is_refresh = (data == "session_wib_refresh")
        if is_refresh:
            await query.message.edit_text(
                build_session_report(),
                parse_mode="HTML",
                reply_markup=info_menu("session_wib_refresh")
            )
        else:
            await query.message.reply_html(
                build_session_report(),
                reply_markup=info_menu("session_wib_refresh")
            )
        return

    if data == "lot_calc":
        risk_prof = get_user_risk_profile(user.id)
        await query.message.reply_html(
            build_lot_calculator_guide(risk_prof),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]])
        )
        return

    if data in ("winrate", "winrate_refresh"):
        is_refresh = (data == "winrate_refresh")
        target_msg = query.message if is_refresh else await query.message.reply_html("⏳ <i>Mengevaluasi riwayat pergerakan harga sejak jam sinyal...</i>")
        await asyncio.to_thread(evaluate_open_signals, get_candles)
        await target_msg.edit_text(
            build_winrate_report(),
            parse_mode="HTML",
            reply_markup=info_menu("winrate_refresh")
        )
        return


# ============================================================
# BACKGROUND TASK (PRICE ALERT & WINRATE EVALUATOR)
# ============================================================

async def background_monitor(application):
    await asyncio.sleep(20)
    tick = 0
    while True:
        try:
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
                                    f"• Harga Saat Ini: <b>{format_price(cur)}</b>"
                                ),
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton(f"⚡ Analisa {sym} Sekarang", callback_data=f"market_{sym}")]
                                ])
                            )
                        except Exception:
                            pass

                if triggered_ids:
                    USERS_DB["price_alerts"] = [
                        a for a in USERS_DB["price_alerts"] if a.get("id") not in triggered_ids
                    ]
                    await asyncio.to_thread(sync_users)

            if tick % 5 == 0:
                await asyncio.to_thread(evaluate_open_signals, get_candles)

        except Exception as err:
            log.warning("Background monitor warning: %s", err)

        tick += 1
        await asyncio.sleep(60)


# ============================================================
# STARTUP & MAIN ENTRY
# ============================================================

async def error_handler(update, context):
    log.error("Telegram error: %s", context.error, exc_info=context.error)


async def post_init(application):
    log.info("======================================")
    log.info("SMC HYBRID ANALISIS BOT STARTED (WIB)")
    log.info("Free Mode: %s | Admin IDs: %s", is_free_mode(), USERS_DB["admin_ids"])
    log.info("======================================")
    asyncio.create_task(background_monitor(application))


def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN tidak ditemukan!")
        sys.exit(1)

    load_users_db()
    load_signals_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("panduan", panduan_command))
    application.add_handler(CommandHandler("help", panduan_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("setrisk", cmd_set_risk))
    application.add_handler(CommandHandler("lot", cmd_calc_lot))
    application.add_handler(CommandHandler("alert", cmd_set_alert))
    application.add_handler(CommandHandler("myalert", cmd_my_alerts))
    application.add_handler(CommandHandler("delalert", cmd_del_alerts))

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
