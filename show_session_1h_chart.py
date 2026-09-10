"""
Show Session 1H Chart — today's 6am-1pm ET session, rendered with actual
1-HOUR candles, using the same zone logic as the main scan: 1H zones as
containers (filtered to today's session window, active only), each with
its single most extreme nested 15m/5m/1m zone drawn inside it.

Run with:  python show_session_1h_chart.py [PAIR]
"""
import sys
from datetime import datetime
from pathlib import Path

import pytz

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.chart_render import render_chart
from modules.scan_engine import _is_same_day_narrow_window_zone, _extreme_nested_zone

NY_TZ = pytz.timezone("America/New_York")


def show_chart(pair: str):
    print(f"Pulling candles for {pair}...")
    h1 = get_candles(pair, "H1", count=200)
    m15 = get_candles(pair, "M15", count=200)
    m5 = get_candles(pair, "M5", count=300)
    m1 = get_candles(pair, "M1", count=1500)

    reference_time = h1["time"].max()

    h1_zones = [z for z in detect_zones(h1, "H1")
                if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
    print(f"1H zones in today's session window (active only): {len(h1_zones)}")

    m15_all = [z for z in detect_zones(m15, "15")
               if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
    m5_all = [z for z in detect_zones(m5, "5")
              if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
    m1_all = [z for z in detect_zones(m1, "1")
              if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]

    zones_for_chart = []
    for h1z in h1_zones:
        zones_for_chart.append(h1z)
        for pool in (m15_all, m5_all, m1_all):
            pick = _extreme_nested_zone(pool, h1z)
            if pick:
                zones_for_chart.append(pick)

    print(f"Total zones on chart (1H + nested): {len(zones_for_chart)}")

    ref_ny = reference_time.tz_convert(NY_TZ)
    session_start = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("06:00", "%H:%M").time()))
    session_end = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("13:00", "%H:%M").time()))

    h1_ny = h1.copy()
    h1_ny["time_ny"] = h1_ny["time"].dt.tz_convert(NY_TZ)
    session_candles = h1_ny[(h1_ny["time_ny"] >= session_start) & (h1_ny["time_ny"] <= session_end)].drop(columns=["time_ny"])

    print(f"1H candles in session window: {len(session_candles)}")

    out_dir = Path("rendered_charts")
    out_path = out_dir / f"{pair}_session1H_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

    if len(session_candles) > 0:
        render_chart(session_candles, zones_for_chart, [], out_path,
                     title=f"{pair} -- 1H candles, 6am-1pm ET session")
        print(f"Saved: {out_path}")
    else:
        print("No 1H candles found in today's 6am-1pm ET window -- nothing to render yet.")


if __name__ == "__main__":
    pair = sys.argv[1] if len(sys.argv) > 1 else "GBP_USD"
    show_chart(pair)
