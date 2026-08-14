"""
Scan Engine — the core strategy logic (trend hierarchy, zone ID, entry
check), refactored to return structured data instead of printing.
Both strategy_scan.py (CLI) and dashboard/app.py (Streamlit) use this
so the logic only lives in one place.
"""
from datetime import datetime
from pathlib import Path

import pytz
import pandas as pd

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.fvg_detector import detect_fvgs
from modules.trend import determine_trend
from modules.chart_render import render_chart
from modules.rejection import detect_rejections

NY_TZ = pytz.timezone("America/New_York")


def in_ny_window(ts, start_h=9, start_m=0, end_h=12, end_m=59):
    ts_ny = ts.tz_convert(NY_TZ) if ts.tzinfo else NY_TZ.localize(ts)
    t = ts_ny.time()
    return (t.hour, t.minute) >= (start_h, start_m) and (t.hour, t.minute) <= (end_h, end_m)


def is_ny_session_now() -> dict:
    now_utc = datetime.now(pytz.UTC)
    now_ny = now_utc.astimezone(NY_TZ)
    t = now_ny.time()
    active = (t.hour, t.minute) >= (9, 0) and (t.hour, t.minute) <= (12, 59)
    return {
        "active": active,
        "current_time_et": now_ny.strftime("%I:%M %p ET"),
    }


def run_scan(pair: str, render_chart_image: bool = True) -> dict:
    result = {
        "pair": pair,
        "error": None,
        "daily_trend": None,
        "h4_trend": None,
        "h1_trend": None,
        "valid_direction": None,
        "deciding_tf": None,
        "zone": None,
        "entry": None,
        "chart_path": None,
    }

    daily = get_candles(pair, "D", count=60)
    h4 = get_candles(pair, "H4", count=120)
    h1 = get_candles(pair, "H1", count=200)
    m15 = get_candles(pair, "M15", count=200)
    m5 = get_candles(pair, "M5", count=300)

    daily_trend = determine_trend(daily, "D")
    h4_trend = determine_trend(h4, "H4")
    h1_trend = determine_trend(h1, "H1")
    result["daily_trend"] = daily_trend
    result["h4_trend"] = h4_trend
    result["h1_trend"] = h1_trend

    if h4_trend["trend"] != "unclear":
        valid_direction = h4_trend["trend"]
        deciding_tf = "4H"
    elif h1_trend["trend"] != "unclear":
        valid_direction = h1_trend["trend"]
        deciding_tf = "1H (4H was unclear)"
    else:
        valid_direction = None
        deciding_tf = None

    result["valid_direction"] = valid_direction
    result["deciding_tf"] = deciding_tf

    if not valid_direction:
        return result

    h1_zones = detect_zones(h1, "H1")
    zone_type_needed = "demand" if valid_direction == "bullish" else "supply"
    matching_zones = [z for z in h1_zones if z["type"] == zone_type_needed and not z["mitigated"]]

    if not matching_zones:
        return result

    zone = matching_zones[-1]
    result["zone"] = zone

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
        pip = 0.0001
        if valid_direction == "bearish":
            sl = entry_price + 8 * pip
            tp = entry_price - 16 * pip
        else:
            sl = entry_price - 8 * pip
            tp = entry_price + 16 * pip
        result["entry"] = {
            "timeframe": tf_name,
            "time": t,
            "candle": {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]},
            "direction": "SHORT" if valid_direction == "bearish" else "LONG",
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
        }

    if render_chart_image:
        try:
            fvgs = detect_fvgs(m15)
            out_dir = Path("rendered_charts")
            out_path = out_dir / f"{pair}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            render_chart(m15, h1_zones, fvgs, out_path, title=f"{pair} M15 with 1H zones")
            result["chart_path"] = out_path
        except Exception:
            pass

    return result
