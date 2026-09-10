"""
Chart Renderer — draws OANDA candles with code-detected supply/demand
zones overlaid, using mplfinance.

1H and 15m zones are drawn as filled boxes (their full origin-candle
range), color-coded blue and red respectively.

5m and 1m zones are drawn as single horizontal LINES instead of boxes,
matching the TradingView indicator's treatment of lower timeframes: a
supply line sits at the origin candle's LOW (bottom of its wick), a
demand line sits at the origin candle's HIGH (top of its wick).

All zones extend the full chart width for readability -- mitigation
status is conveyed by transparency, not by cutting the box short.
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

    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return save_path
