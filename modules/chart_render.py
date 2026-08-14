"""
Chart Renderer — draws real OANDA candles with code-detected supply/demand
zones and FVGs overlaid, using mplfinance. This REPLACES TradingView
screenshots entirely -- no browser automation, no login, no session
transfer. The zones drawn here are guaranteed accurate because they're
code output, not a vision model's guess at pixels.
"""
from pathlib import Path
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt


def render_chart(
    candles: pd.DataFrame,
    zones: list[dict],
    fvgs: list[dict],
    save_path: Path,
    title: str = "",
    rejections: list[dict] = None,
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
        if zone.get("mitigated") and zone.get("mitigated_time"):
            try:
                x_end = _x_for(zone["mitigated_time"])
            except Exception:
                pass

        color = "red" if zone["type"] == "supply" else "blue"
        alpha = 0.15 if zone.get("mitigated") else 0.3
        ax.fill_betweenx(
            [zone["bottom"], zone["top"]],
            x_start, x_end,
            color=color, alpha=alpha,
        )
        label = f"{zone['type'].upper()} {zone['bottom']:.5f}-{zone['top']:.5f}"
        ax.text(x_start, zone["top"], label, fontsize=7, color=color, va="bottom")

    for fvg in fvgs:
        try:
            x_start = _x_for(fvg["start_time"])
        except Exception:
            continue
        x_end = x_max + 2
        color = "purple"
        ax.fill_betweenx(
            [fvg["gap_low"], fvg["gap_high"]],
            x_start, x_end,
            color=color, alpha=0.15, hatch="//",
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
