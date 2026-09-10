"""
Visualize Zones — render any pair/timeframe with zones and rejection
events drawn on it, so you can visually check what the code is (and
isn't) finding, side by side with your own read of the chart.

Run with:  python visualize_zones.py [PAIR] [TIMEFRAME]
Example:   python visualize_zones.py GBP_USD H4
"""
import sys
from datetime import datetime
from pathlib import Path

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.fvg_detector import detect_fvgs
from modules.rejection import detect_rejections
from modules.chart_render import render_chart


def visualize(pair: str, timeframe: str, count: int = 120):
    print(f"Pulling {count} {timeframe} candles for {pair}...")
    candles = get_candles(pair, timeframe, count=count)

    zones = detect_zones(candles, timeframe)
    fvgs = detect_fvgs(candles)
    rejections = detect_rejections(candles, zones)

    print(f"Zones found: {len(zones)} ({sum(1 for z in zones if not z['mitigated'])} still active)")
    print(f"Rejections found: {len(rejections)}")
    if rejections:
        most_recent = max(rejections, key=lambda r: r["rejection_time"])
        print(f"Most recent rejection: {most_recent['direction']} at {most_recent['rejection_time']}")

    out_dir = Path("rendered_charts")
    out_path = out_dir / f"{pair}_{timeframe}_zones_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    render_chart(
        candles, zones, fvgs, out_path,
        title=f"{pair} {timeframe} — zones + rejections",
        rejections=rejections,
    )
    print(f"\nSaved: {out_path}")
    print("Opening it now...")

    import subprocess
    subprocess.run(["start", "", str(out_path)], shell=True)


if __name__ == "__main__":
    pair = sys.argv[1] if len(sys.argv) > 1 else "GBP_USD"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "H4"
    visualize(pair, timeframe)
