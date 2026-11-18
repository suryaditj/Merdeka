# -*- coding: utf-8 -*-
import io
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from config import WIB, format_price


def generate_smc_chart(candles, result):
    """
    Menggambar chart candlestick dark-mode dari data 100 candle OHLC real-time
    lengkap dengan kotak zona SMC (OB, FVG, SND, RBR/DBD), level SNR/SBR-RBS,
    serta garis Entry, SL, TP1-TP2, dan Pending Limit (Sell/Buy Limit).
    """
    display_candles = candles[-65:] if len(candles) > 65 else candles
    n = len(display_candles)

    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=130)
    bg_color = "#0d1117"
    panel_color = "#131722"
    grid_color = "#21262d"
    text_color = "#d1d4dc"

    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(panel_color)

    body_width = 0.62
    for idx, c in enumerate(display_candles):
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        is_bull = cl >= o
        color = "#089981" if is_bull else "#f23645"

        ax.plot([idx, idx], [l, h], color=color, linewidth=1.0, zorder=2)

        lower = min(o, cl)
        height = max(abs(cl - o), (h - l) * 0.03)
        rect = patches.Rectangle(
            (idx - body_width / 2, lower),
            body_width,
            height,
            facecolor=color,
            edgecolor=color,
            zorder=3
        )
        ax.add_patch(rect)

    def draw_zone(z_low, z_high, label, color, start_idx=max(0, n - 28)):
        if z_low is None or z_high is None:
            return
        low_v, high_v = min(z_low, z_high), max(z_low, z_high)
        rect = patches.Rectangle(
            (start_idx, low_v),
            (n - 1) - start_idx + 1.5,
            max(high_v - low_v, (high_v * 0.0001)),
            facecolor=color,
            edgecolor=color,
            alpha=0.22,
            linewidth=1.0,
            zorder=1
        )
        ax.add_patch(rect)
        ax.text(
            start_idx + 0.4,
            high_v,
            f" {label}",
            color=color,
            fontsize=8,
            fontweight="bold",
            va="bottom",
            ha="left",
            zorder=5
        )

    def draw_level(price_val, label, color, linestyle="--", start_idx=max(0, n - 22)):
        if price_val is None:
            return
        ax.hlines(
            y=price_val,
            xmin=start_idx,
            xmax=n + 1.2,
            colors=color,
            linestyles=linestyle,
            linewidth=1.15,
            zorder=4
        )
        ax.text(
            n + 1.4,
            price_val,
            f"{label} @ {format_price(price_val)}",
            color=color,
            fontsize=7.8,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="round,pad=0.18", facecolor=bg_color, edgecolor=color, alpha=0.85, lw=0.7),
            zorder=6
        )

    if result.get("ob_low") is not None and result.get("ob_high") is not None:
        ob_col = "#2962ff" if result["signals"]["OB"] == "BUY" else "#ff5252"
        draw_zone(result["ob_low"], result["ob_high"], result.get("ob_type", "OB"), ob_col, max(0, n - 30))

    if result.get("fvg_low") is not None and result.get("fvg_high") is not None:
        fvg_col = "#00bcd4" if result["signals"]["FVG"] == "BUY" else "#ab47bc"
        draw_zone(result["fvg_low"], result["fvg_high"], f"FVG ({result['signals']['FVG']})", fvg_col, max(0, n - 24))

    if result.get("snd_low") is not None and result.get("snd_high") is not None:
        snd_col = "#26a69a" if result["signals"]["SND"] == "BUY" else "#ef5350"
        draw_zone(result["snd_low"], result["snd_high"], f"SND ({result['signals']['SND']})", snd_col, max(0, n - 26))

    if result.get("rbr_dbd_low") is not None and result.get("rbr_dbd_high") is not None:
        rbr_col = "#4caf50" if result["signals"]["RBR_DBD"] == "BUY" else "#ff7043"
        draw_zone(result["rbr_dbd_low"], result["rbr_dbd_high"], result.get("rbr_dbd_type", "BASE"), rbr_col, max(0, n - 28))

    if result.get("snr_level") is not None:
        draw_level(result["snr_level"], "SNR", "#ffd54f", ":", max(0, n - 32))

    if result.get("sbr_rbs_level") is not None:
        draw_level(result["sbr_rbs_level"], result.get("sbr_rbs_type", "SBR/RBS"), "#4dd0e1", "-.", max(0, n - 26))

    direction = result.get("direction", "WAIT")
    if direction in ("BUY", "SELL"):
        entry_mid = (result["entry_low"] + result["entry_high"]) / 2.0
        draw_level(entry_mid, f"ENTRY {direction}", "#29b6f6", "-", max(0, n - 12))
        draw_level(result["sl"], "SL", "#ff5252", "--", max(0, n - 12))
        draw_level(result["tp1"], "TP1", "#66bb6a", "--", max(0, n - 12))
        draw_level(result["tp2"], "TP2", "#26a69a", "--", max(0, n - 12))
    else:
        sell_lim = result.get("sell_limit")
        buy_lim = result.get("buy_limit")
        if sell_lim:
            draw_zone(sell_lim["zone_low"], sell_lim["zone_high"], "SELL LIMIT ZONE", "#ff5252", max(0, n - 18))
            draw_level(sell_lim["entry"], "SELL LIMIT", "#ff5252", "--", max(0, n - 14))
        if buy_lim:
            draw_zone(buy_lim["zone_low"], buy_lim["zone_high"], "BUY LIMIT ZONE", "#00e676", max(0, n - 18))
            draw_level(buy_lim["entry"], "BUY LIMIT", "#00e676", "--", max(0, n - 14))

    step = max(1, n // 7)
    tick_indices = list(range(0, n, step))
    tick_labels = [
        datetime.fromtimestamp(display_candles[i]["time"], tz=WIB).strftime("%H:%M")
        for i in tick_indices
    ]
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, color=text_color, fontsize=8)

    ax.yaxis.tick_right()
    ax.tick_params(axis="y", colors=text_color, labelsize=8.5)
    ax.grid(True, color=grid_color, linestyle="-", linewidth=0.5, alpha=0.7)

    for spine in ("top", "left"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "right"):
        ax.spines[spine].set_color("#30363d")

    ax.set_xlim(-1, n + 14)

    sym = result["symbol"]
    tf = result["timeframe"]
    price_now = format_price(result["price"])
    status_dir = f"{direction} ({result['confirmations']}/7)"
    ax.set_title(
        f"{sym} • {tf}  |  Harga: {price_now}  |  Sinyal: {status_dir}",
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
    return buf
