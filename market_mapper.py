# -*- coding: utf-8 -*-
import io
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from config import WIB, format_price
from smc_engine import get_candles, analyze_market


def build_mapping_chart_and_caption(symbol, timeframe):
    """
    Menarik data candle & hasil deteksi struktur dari smc_engine.py asli (tanpa mengubahnya),
    menggambar peta struktur SMC secara detail & presisi, serta menyusun caption singkat.
    """
    candles = get_candles(symbol, timeframe)
    result = analyze_market(symbol, timeframe)

    # Tampilkan 80 candle terakhir agar struktur swing, zona atas & bawah terlihat utuh
    display_candles = candles[-80:] if len(candles) > 80 else candles
    n = len(display_candles)

    fig, ax = plt.subplots(figsize=(13, 7.0), dpi=150)
    bg_color = "#0b0e14"
    panel_color = "#131722"
    grid_color = "#1e222d"
    text_color = "#d1d4dc"

    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(panel_color)

    # 1. Gambar Candlestick OHLC Presisi
    body_width = 0.62
    for idx, c in enumerate(display_candles):
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        is_bull = cl >= o
        color = "#089981" if is_bull else "#f23645"

        ax.plot([idx, idx], [l, h], color=color, linewidth=1.05, zorder=2)
        lower = min(o, cl)
        height = max(abs(cl - o), (h - l) * 0.025)
        rect = patches.Rectangle(
            (idx - body_width / 2, lower),
            body_width,
            height,
            facecolor=color,
            edgecolor=color,
            zorder=3
        )
        ax.add_patch(rect)

    # 2. Peta Likuiditas Swing High (BSL) & Swing Low (SSL)
    swing_slice = display_candles[:-5] if n > 15 else display_candles
    bsl_high = max(c["high"] for c in swing_slice)
    ssl_low = min(c["low"] for c in swing_slice)

    def draw_zone(z_low, z_high, label, color, start_idx):
        if z_low is None or z_high is None:
            return
        low_v, high_v = min(z_low, z_high), max(z_low, z_high)
        rect = patches.Rectangle(
            (start_idx, low_v),
            (n - 1) - start_idx + 1.5,
            max(high_v - low_v, high_v * 0.00012),
            facecolor=color,
            edgecolor=color,
            alpha=0.22,
            linewidth=1.1,
            zorder=1
        )
        ax.add_patch(rect)
        ax.text(
            start_idx + 0.5,
            high_v,
            f" {label} ({format_price(low_v)} - {format_price(high_v)})",
            color=color,
            fontsize=7.8,
            fontweight="bold",
            va="bottom",
            ha="left",
            zorder=6
        )

    def draw_level(price_val, label, color, linestyle="--", start_idx=0, show_tag=True):
        if price_val is None:
            return
        ax.hlines(
            y=price_val,
            xmin=start_idx,
            xmax=n + 1.0,
            colors=color,
            linestyles=linestyle,
            linewidth=1.15,
            zorder=4
        )
        if show_tag:
            ax.text(
                n + 1.3,
                price_val,
                f"{label} {format_price(price_val)}",
                color=color,
                fontsize=7.6,
                fontweight="bold",
                va="center",
                ha="left",
                bbox=dict(boxstyle="round,pad=0.18", facecolor=bg_color, edgecolor=color, alpha=0.9, lw=0.7),
                zorder=7
            )

    # Garis Likuiditas Atas & Bawah
    draw_level(bsl_high, "BSL / High", "#ff9800", ":", max(0, n - 45))
    draw_level(ssl_low, "SSL / Low", "#26c6da", ":", max(0, n - 45))

    # Garis Harga Berjalan (Current Price)
    cur_price = result["price"]
    draw_level(cur_price, "NOW", "#ffffff", "-", max(0, n - 10))

    # 3. Gambar Semua Zona SMC Aktif yang Terdeteksi
    active_zones = []

    if result.get("ob_low") is not None and result.get("ob_high") is not None:
        ob_col = "#2962ff" if result["signals"]["OB"] == "BUY" else "#ff5252"
        draw_zone(result["ob_low"], result["ob_high"], result.get("ob_type", "OB"), ob_col, max(0, n - 36))
        active_zones.append(f"• {result.get('ob_type', 'OB')}: {format_price(result['ob_low'])} - {format_price(result['ob_high'])}")

    if result.get("fvg_low") is not None and result.get("fvg_high") is not None:
        fvg_col = "#00bcd4" if result["signals"]["FVG"] == "BUY" else "#e040fb"
        draw_zone(result["fvg_low"], result["fvg_high"], f"FVG {result['signals']['FVG']}", fvg_col, max(0, n - 30))
        active_zones.append(f"• FVG ({result['signals']['FVG']}): {format_price(result['fvg_low'])} - {format_price(result['fvg_high'])}")

    if result.get("snd_low") is not None and result.get("snd_high") is not None:
        snd_col = "#00e676" if result["signals"]["SND"] == "BUY" else "#ff7043"
        draw_zone(result["snd_low"], result["snd_high"], f"SND {result['signals']['SND']}", snd_col, max(0, n - 32))
        active_zones.append(f"• SND ({result['signals']['SND']}): {format_price(result['snd_low'])} - {format_price(result['snd_high'])}")

    if result.get("rbr_dbd_low") is not None and result.get("rbr_dbd_high") is not None:
        rbr_col = "#66bb6a" if result["signals"]["RBR_DBD"] == "BUY" else "#ffa726"
        draw_zone(result["rbr_dbd_low"], result["rbr_dbd_high"], result.get("rbr_dbd_type", "BASE"), rbr_col, max(0, n - 34))
        active_zones.append(f"• {result.get('rbr_dbd_type', 'BASE')}: {format_price(result['rbr_dbd_low'])} - {format_price(result['rbr_dbd_high'])}")

    if result.get("snr_level") is not None:
        draw_level(result["snr_level"], "SNR", "#ffd54f", "-.", max(0, n - 38))
        active_zones.append(f"• SNR: {format_price(result['snr_level'])}")

    if result.get("sbr_rbs_level") is not None:
        draw_level(result["sbr_rbs_level"], result.get("sbr_rbs_type", "SBR/RBS"), "#4dd0e1", "-.", max(0, n - 32))
        active_zones.append(f"• {result.get('sbr_rbs_type', 'SBR/RBS')}: {format_price(result['sbr_rbs_level'])}")

    # 4. Peta Area Jemputan Pending Limit (Supply Atas & Demand Bawah)
    sell_lim = result.get("sell_limit")
    buy_lim = result.get("buy_limit")
    if sell_lim:
        draw_zone(sell_lim["zone_low"], sell_lim["zone_high"], "SUPPLY / SELL LIMIT", "#ff5252", max(0, n - 24))
        draw_level(sell_lim["entry"], "SELL LIM", "#ff5252", "--", max(0, n - 18))
    if buy_lim:
        draw_zone(buy_lim["zone_low"], buy_lim["zone_high"], "DEMAND / BUY LIMIT", "#00e676", max(0, n - 24))
        draw_level(buy_lim["entry"], "BUY LIM", "#00e676", "--", max(0, n - 18))

    # 5. Sumbu Waktu WIB & Harga Kanan
    step = max(1, n // 8)
    tick_indices = list(range(0, n, step))
    tick_labels = [
        datetime.fromtimestamp(display_candles[i]["time"], tz=WIB).strftime("%H:%M")
        for i in tick_indices
    ]
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, color=text_color, fontsize=8)

    ax.yaxis.tick_right()
    ax.tick_params(axis="y", colors=text_color, labelsize=8.5)
    ax.grid(True, color=grid_color, linestyle="-", linewidth=0.5, alpha=0.75)

    for spine in ("top", "left"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "right"):
        ax.spines[spine].set_color("#30363d")

    ax.set_xlim(-1, n + 16)

    bias_str = result["direction"] if result["direction"] != "WAIT" else f"WAIT (Bias {result.get('bias', '-')})"
    ax.set_title(
        f"🗺️ MAPPING SMC • {symbol} ({timeframe})  |  Harga: {format_price(cur_price)}  |  Struktur: {bias_str}",
        loc="left",
        color="#ffffff",
        fontsize=11,
        fontweight="bold",
        pad=10
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    # 6. Susun Keterangan Singkat di Bawah Foto (Caption <= 1024 karakter)
    zona_text = "\n".join(active_zones) if active_zones else "• Harga berada di tengah range (pantau zona limit atas/bawah)"
    caption = (
        f"🗺️ MAPPING MARKET — {symbol} ({timeframe})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Harga: {format_price(cur_price)} | BOS: {result['signals']['BOS']}\n"
        f"💧 Likuiditas: {result['sweep_desc']}\n\n"
        f"📌 ZONA AKTIF DI PETA:\n"
        f"{zona_text}\n\n"
        f"🎯 AREA JEMPUTAN TERDEKAT:\n"
        f"• 🔴 Supply (Sell Limit): {format_price(sell_lim['entry'])} (SL {format_price(sell_lim['sl'])})\n"
        f"• 🟢 Demand (Buy Limit): {format_price(buy_lim['entry'])} (SL {format_price(buy_lim['sl'])})"
    )

    return buf, caption[:1020]
