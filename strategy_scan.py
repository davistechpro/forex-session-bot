"""
Strategy Scan — the real test: does pure code (no AI vision) correctly
call out a valid trade per the strategy, using real OANDA data?
"""
import sys
from datetime import datetime
from pathlib import Path

import pytz
import pandas as pd

from modules.config_loader import load_settings
from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.fvg_detector import detect_fvgs
from modules.trend import determine_trend
from modules.chart_render import render_chart

settings = load_settings()
NY_TZ = pytz.timezone("America/New_York")


def in_ny_window(ts, start_h=9, start_m=0, end_h=12, end_m=59):
    ts_ny = ts.tz_convert(NY_TZ) if ts.tzinfo else NY_TZ.localize(ts)
    t = ts_ny.time()
    return (t.hour, t.minute) >= (start_h, start_m) and (t.hour, t.minute) <= (end_h, end_m)


def run_scan(pair: str):
    print(f"\n{'='*70}\nSTRATEGY SCAN — {pair}\n{'='*70}\n")

    print("Pulling candles...")
    daily = get_candles(pair, "D", count=60)
    h4 = get_candles(pair, "H4", count=120)
    h1 = get_candles(pair, "H1", count=200)
    m15 = get_candles(pair, "M15", count=200)
    m5 = get_candles(pair, "M5", count=300)

    print("\n--- STEP 1: TREND ---")
    daily_trend = determine_trend(daily, "D")
    h4_trend = determine_trend(h4, "H4")
    h1_trend = determine_trend(h1, "H1")

    print(f"Daily: {daily_trend['trend']}  (via {daily_trend['signal_type']})")
    print(f"4H:    {h4_trend['trend']}  (via {h4_trend['signal_type']} at {h4_trend['event_time']})")
    print(f"1H:    {h1_trend['trend']}  (via {h1_trend['signal_type']} at {h1_trend['event_time']})")

    if h4_trend["trend"] != "unclear":
        valid_direction = h4_trend["trend"]
        deciding_tf = "4H"
    elif h1_trend["trend"] != "unclear":
        valid_direction = h1_trend["trend"]
        deciding_tf = "1H (4H was unclear)"
    else:
        valid_direction = None
        deciding_tf = None

    if valid_direction:
        print(f"\n>>> Valid direction: {valid_direction.upper()} (per {deciding_tf})")
    else:
        print("\n>>> No valid direction — both 4H and 1H unclear. NO TRADE.")
        return

    print("\n--- STEP 2: ZONE ---")
    h1_zones = detect_zones(h1, "H1")
    zone_type_needed = "demand" if valid_direction == "bullish" else "supply"
    matching_zones = [z for z in h1_zones if z["type"] == zone_type_needed and not z["mitigated"]]

    if not matching_zones:
        print(f"No active {zone_type_needed} zone found on 1H. NO TRADE.")
        return

    zone = matching_zones[-1]
    print(f"Zone found: {zone['type'].upper()} {zone['bottom']:.5f}-{zone['top']:.5f}")
    print(f"Origin: {zone['origin_time']}  Confirmed: {zone['confirmed_time']}")

    print("\n--- STEP 3/4: ENTRY CHECK (wick tap, 9am-12:59pm ET, no confirmation needed) ---")
    entry_found = None
    for tf_name, tf_candles in [("M15", m15), ("M5", m5)]:
        for _, c in tf_candles.iterrows():
            t = c["time"]
            if not in_ny_window(t):
                continue
            if t <= zone["confirmed_time"]:
                continue
            wick_low, wick_high = c["low"], c["high"]
            if zone_type_needed == "demand" and wick_low <= zone["top"] and wick_low >= zone["bottom"] - 0.0020:
                entry_found = (tf_name, t, c)
                break
            if zone_type_needed == "supply" and wick_high >= zone["bottom"] and wick_high <= zone["top"] + 0.0020:
                entry_found = (tf_name, t, c)
                break
        if entry_found:
            break

    if entry_found:
        tf_name, t, c = entry_found
        entry_price = zone["top"] if zone_type_needed == "supply" else zone["bottom"]
        print(f"ENTRY FOUND on {tf_name} at {t}")
        print(f"Candle: O{c['open']:.5f} H{c['high']:.5f} L{c['low']:.5f} C{c['close']:.5f}")
        pip = 0.0001
        if valid_direction == "bearish":
            sl = entry_price + 8 * pip
            tp = entry_price - 16 * pip
        else:
            sl = entry_price - 8 * pip
            tp = entry_price + 16 * pip
        print(f"\n>>> TRADE: {'SHORT' if valid_direction=='bearish' else 'LONG'} @ {entry_price:.5f}")
        print(f">>> SL: {sl:.5f}  TP: {tp:.5f}")
    else:
        print("No wick tap into zone found during trading window. NO TRADE.")

    print("\n--- RENDERING CHART ---")
    fvgs = detect_fvgs(m15)
    out_dir = Path("rendered_charts")
    out_path = out_dir / f"{pair}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    render_chart(m15, h1_zones, fvgs, out_path, title=f"{pair} M15 with 1H zones")
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    pair = sys.argv[1] if len(sys.argv) > 1 else settings["instrument"]["pair"]
    run_scan(pair)
