"""
Chart Renderer — OANDA candles with supply/demand zones and a
TradingView-style long/short position box.

1H and 15m zones draw as filled boxes (blue / red).
5m and 1m zones draw as horizontal LINES (orange / black) at the origin
candle's wick -- supply at its low, demand at its high.
Trades draw as a shaded risk box (entry->SL, red) and reward box
(entry->TP, green) with pip distances and R:R.
"""
from pathlib import Path
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt

LINE_TIMEFRAMES = {"5", "M5", "5m", "1", "M1", "1m"}


def _timeframe_label(zone) -> str:
    tf = zone.get("timeframe", "")
    if tf in ("H1", "1H", "60"):
        return "1H"
    if tf in ("15", "M15", "15m"):
        return "15m"
    if tf in ("5", "M5", "5m"):
        return "5m"
    if tf in ("1", "M1", "1m"):
        return "1m"
    if tf in ("H4", "4H", "240"):
        return "4H"
    return tf or "?"


def _timeframe_color(zone) -> str:
    tf = zone.get("timeframe", "")
    if tf in ("H1", "1H", "60"):
        return "blue"
    if tf in ("15", "M15", "15m"):
        return "red"
    if tf in ("5", "M5", "5m"):
        return "orange"
    if tf in ("1", "M1", "1m"):
        return "black"
    if tf in ("H4", "4H", "240"):
        return "purple"
    return "red"


def render_chart(
    candles: pd.DataFrame,
    zones: list,
    fvgs: list,
    save_path: Path,
    title: str = "",
    rejections: list = None,
    trade: dict = None,
):
    df = candles.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})

    fig, axlist = mpf.plot(
        df,
        type="candle",
        style="charles",
        returnfig=True,
        figsize=(16, 9),
        title=title,
        volume=False,
    )
    ax = axlist[0]

    time_to_x = {t: i for i, t in enumerate(df.index)}

    def _x_for(t):
        t = pd.to_datetime(t)
        if t in time_to_x:
            return time_to_x[t]
        nearest = min(df.index, key=lambda dt: abs((dt - t).total_seconds()))
        return time_to_x[nearest]

    x_max = len(df) - 1

    for zone in zones:
        try:
            x_start = _x_for(zone["origin_time"])
        except Exception:
            continue

        x_end = x_max + 2
        color = _timeframe_color(zone)
        tf = zone.get("timeframe", "")

        if tf in LINE_TIMEFRAMES:
            level = zone["bottom"] if zone["type"] == "supply" else zone["top"]
            linewidth = 2.5 if not zone.get("mitigated") else 1.5
            alpha = 0.9 if not zone.get("mitigated") else 0.4
            ax.hlines(level, x_start, x_end, color=color, linewidth=linewidth, alpha=alpha, zorder=4)
            label = f"{_timeframe_label(zone)} {zone['type'].upper()} {level:.5f}"
            ax.text(x_start, level, f"{label}  ", fontsize=8, color=color,
                    va="center", ha="right", fontweight="bold")
        else:
            alpha = 0.15 if zone.get("mitigated") else 0.3
            ax.fill_betweenx(
                [zone["bottom"], zone["top"]],
                x_start, x_end,
                color=color, alpha=alpha,
            )
            label = f"{_timeframe_label(zone)} {zone['type'].upper()} {zone['bottom']:.5f}-{zone['top']:.5f}"
            ax.text(x_start, zone["top"], label, fontsize=7, color=color, va="bottom")

            mid_x = (x_start + min(x_end, x_max)) / 2
            mid_y = (zone["bottom"] + zone["top"]) / 2
            ax.text(
                mid_x, mid_y, _timeframe_label(zone),
                fontsize=11, color=color, alpha=0.7,
                ha="center", va="center", fontweight="bold",
            )

    for fvg in (fvgs or []):
        try:
            x_start = _x_for(fvg["start_time"])
        except Exception:
            continue
        ax.fill_betweenx(
            [fvg["gap_low"], fvg["gap_high"]],
            x_start, x_max + 2,
            color="purple", alpha=0.15, hatch="//",
        )

    if rejections:
        for r in rejections:
            try:
                x = _x_for(r["rejection_time"])
            except Exception:
                continue
            row = candles[candles["time"] == r["rejection_time"]]
            if row.empty:
                continue
            row = row.iloc[0]
            marker_color = "darkred" if r["direction"] == "bearish" else "darkgreen"
            y = row["high"] * 1.0005 if r["direction"] == "bearish" else row["low"] * 0.9995
            ax.scatter([x], [y], marker="*", s=250, color=marker_color, zorder=5, edgecolors="black")
            ax.text(x, y, f"  REJECT ({int(r['volume'])} vol)", fontsize=7, color=marker_color, va="center")

    # --- Trade position box (TradingView long/short tool style) ---
    if trade:
        entry_price = trade.get("entry_price")
        sl = trade.get("stop_loss")
        tp = trade.get("take_profit")
        direction = trade.get("direction", "")

        if entry_price is not None and sl is not None and tp is not None:
            try:
                x_box_start = _x_for(trade["time"]) if trade.get("time") else 0
            except Exception:
                x_box_start = 0
            x_box_end = x_max + 2

            ax.fill_betweenx(
                sorted([entry_price, sl]),
                x_box_start, x_box_end,
                color="red", alpha=0.13, zorder=2,
            )
            ax.fill_betweenx(
                sorted([entry_price, tp]),
                x_box_start, x_box_end,
                color="green", alpha=0.13, zorder=2,
            )

            ax.hlines(sl, x_box_start, x_box_end, color="red",
                      linewidth=1.3, linestyle="--", alpha=0.9, zorder=6)
            ax.hlines(tp, x_box_start, x_box_end, color="green",
                      linewidth=1.3, linestyle="--", alpha=0.9, zorder=6)
            ax.hlines(entry_price, x_box_start, x_box_end, color="black",
                      linewidth=1.8, alpha=0.95, zorder=7)

            pip_size = 0.01 if entry_price > 20 else 0.0001
            risk_pips = abs(entry_price - sl) / pip_size
            reward_pips = abs(tp - entry_price) / pip_size
            rr = (reward_pips / risk_pips) if risk_pips else 0

            fmt = "{:.3f}" if pip_size == 0.01 else "{:.5f}"

            ax.text(x_box_end, entry_price,
                    f"  {direction} @ {fmt.format(entry_price)}",
                    fontsize=10, color="black", fontweight="bold",
                    va="center", ha="left", zorder=8)
            ax.text(x_box_end, sl,
                    f"  SL {fmt.format(sl)}  (-{risk_pips:.0f} pips)",
                    fontsize=9, color="red", fontweight="bold",
                    va="center", ha="left", zorder=8)
            ax.text(x_box_end, tp,
                    f"  TP {fmt.format(tp)}  (+{reward_pips:.0f} pips)",
                    fontsize=9, color="green", fontweight="bold",
                    va="center", ha="left", zorder=8)

            mid_reward = (entry_price + tp) / 2
            ax.text((x_box_start + x_box_end) / 2, mid_reward,
                    f"{rr:.1f}R",
                    fontsize=13, color="darkgreen", fontweight="bold",
                    alpha=0.55, va="center", ha="center", zorder=8)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return save_path
