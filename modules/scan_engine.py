"""
Scan Engine — trend agreement, zone ID, and entry/stop cascade.

TREND: requires 2-of-3 agreement across Daily / 4H / 1H.

ENTRY + STOP CASCADE:
  - Enter at the most refined nested level (15m first, then 5m, then 1m).
  - Stop must clear the NEXT nested level: try 8 pips, then 10. If 10
    still does not clear it, drop the entry down to that level and repeat.
  - 1m is the floor (plain 8-pip stop). TP is always 2R.
"""
from datetime import datetime
from pathlib import Path

import pytz
import pandas as pd

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.trend import determine_trend
from modules.chart_render import render_chart
from modules.confirmation_entry import find_confirmation_entry

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
    return {"active": active, "current_time_et": now_ny.strftime("%I:%M %p ET")}


def _is_same_day_session_zone(zone, reference_time) -> bool:
    origin_ny = zone["origin_time"].tz_convert(NY_TZ) if zone["origin_time"].tzinfo else NY_TZ.localize(zone["origin_time"])
    ref_ny = reference_time.tz_convert(NY_TZ) if reference_time.tzinfo else NY_TZ.localize(reference_time)
    same_day = origin_ny.date() == ref_ny.date()
    t = origin_ny.time()
    in_window = (t.hour, t.minute) >= (6, 0) and (t.hour, t.minute) < (13, 0)
    return same_day and in_window


def _is_same_day_narrow_window_zone(zone, reference_time) -> bool:
    origin_ny = zone["origin_time"].tz_convert(NY_TZ) if zone["origin_time"].tzinfo else NY_TZ.localize(zone["origin_time"])
    ref_ny = reference_time.tz_convert(NY_TZ) if reference_time.tzinfo else NY_TZ.localize(reference_time)
    same_day = origin_ny.date() == ref_ny.date()
    t = origin_ny.time()
    in_window = (t.hour, t.minute) >= (6, 0) and (t.hour, t.minute) < (12, 0)
    return same_day and in_window


def _extreme_nested_zone(zones: list, container_zone: dict):
    nested = [
        z for z in zones
        if z["type"] == container_zone["type"]
        and not (z["top"] < container_zone["bottom"] or container_zone["top"] < z["bottom"])
    ]
    if not nested:
        return None
    if container_zone["type"] == "supply":
        return max(nested, key=lambda z: z["top"])
    else:
        return min(nested, key=lambda z: z["bottom"])


def _level_price(zone: dict, zone_type: str) -> float:
    """Supply is hit from below -> entry at its bottom. Demand from above -> top."""
    return zone["bottom"] if zone_type == "supply" else zone["top"]


def _plan_trade(container: dict, ladder: list, zone_type: str, pip: float) -> dict:
    """
    ladder = nested zones below the container, most-refined LAST,
             e.g. [15m, 5m, 1m]. Only levels that exist are included.

    Enter at ladder[0] (or the container edge if the ladder is empty).
    The stop must clear the NEXT ladder level: try 8 pips, then 10. If
    10 still does not clear it, drop the entry to that level and repeat.
    The last level gets a plain 8-pip stop. TP is always 2R.
    """
    sl_candidates = (8, 10)

    if not ladder:
        entry = _level_price(container, zone_type)
        sl_pips = 8
        level_name = container.get("timeframe", "?")
    else:
        idx = 0
        while True:
            level = ladder[idx]
            entry = _level_price(level, zone_type)
            level_name = level.get("timeframe", "?")
            nxt = ladder[idx + 1] if idx + 1 < len(ladder) else None

            if nxt is None:
                sl_pips = 8
                break

            nxt_price = _level_price(nxt, zone_type)
            chosen = None
            for cand in sl_candidates:
                if zone_type == "supply":
                    if entry + cand * pip >= nxt_price:
                        chosen = cand
                        break
                else:
                    if entry - cand * pip <= nxt_price:
                        chosen = cand
                        break

            if chosen is not None:
                sl_pips = chosen
                break
            idx += 1

    if zone_type == "supply":
        sl = entry + sl_pips * pip
        tp = entry - 2 * sl_pips * pip
    else:
        sl = entry - sl_pips * pip
        tp = entry + 2 * sl_pips * pip

    return {
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "sl_pips": sl_pips,
        "tp_pips": 2 * sl_pips,
        "entry_level": level_name,
    }


def _check_wick_tap_entry(m5_candles, m15_candles, zone, zone_type):
    for tf_name, tf_candles in [("M15", m15_candles), ("M5", m5_candles)]:
        for _, c in tf_candles.iterrows():
            t = c["time"]
            if not in_ny_window(t):
                continue
            if t <= zone["confirmed_time"]:
                continue
            wick_low, wick_high = c["low"], c["high"]
            if zone_type == "demand" and wick_low <= zone["top"] and wick_low >= zone["bottom"] - 0.0020:
                return {"timeframe": tf_name, "time": t, "candle": c, "entry_price": zone["bottom"]}
            if zone_type == "supply" and wick_high >= zone["bottom"] and wick_high <= zone["top"] + 0.0020:
                return {"timeframe": tf_name, "time": t, "candle": c, "entry_price": zone["top"]}
    return None


def run_scan(pair: str, render_chart_image: bool = True) -> dict:
    result = {
        "pair": pair, "error": None, "daily_trend": None, "h4_trend": None, "h1_trend": None,
        "valid_direction": None, "deciding_tf": None, "zone": None, "zone_source_tf": None,
        "entry": None, "entry_method": None, "chart_path": None, "chart_path_1h": None,
    }

    daily = get_candles(pair, "D", count=60)
    h4 = get_candles(pair, "H4", count=120)
    h1 = get_candles(pair, "H1", count=200)
    m15 = get_candles(pair, "M15", count=200)
    m5 = get_candles(pair, "M5", count=300)
    m1 = get_candles(pair, "M1", count=1500)

    reference_time = h1["time"].max() if len(h1) > 0 else pd.Timestamp.now(tz="UTC")

    daily_trend = determine_trend(daily, "D")
    h4_trend = determine_trend(h4, "H4")
    h1_trend = determine_trend(h1, "H1")
    result["daily_trend"] = daily_trend
    result["h4_trend"] = h4_trend
    result["h1_trend"] = h1_trend

    votes = [daily_trend["trend"], h4_trend["trend"], h1_trend["trend"]]
    bullish_votes = votes.count("bullish")
    bearish_votes = votes.count("bearish")

    if bullish_votes >= 2:
        valid_direction = "bullish"
        agreeing = [name for name, t in
                    [("Daily", daily_trend["trend"]), ("4H", h4_trend["trend"]), ("1H", h1_trend["trend"])]
                    if t == "bullish"]
        deciding_tf = f"{bullish_votes}-of-3 agree ({'+'.join(agreeing)})"
    elif bearish_votes >= 2:
        valid_direction = "bearish"
        agreeing = [name for name, t in
                    [("Daily", daily_trend["trend"]), ("4H", h4_trend["trend"]), ("1H", h1_trend["trend"])]
                    if t == "bearish"]
        deciding_tf = f"{bearish_votes}-of-3 agree ({'+'.join(agreeing)})"
    else:
        valid_direction = None
        deciding_tf = None

    result["valid_direction"] = valid_direction
    result["deciding_tf"] = deciding_tf

    if not valid_direction:
        return result

    zone_type_needed = "demand" if valid_direction == "bullish" else "supply"

    h4_zones = detect_zones(h4, "H4")
    h4_matches = [z for z in h4_zones if z["type"] == zone_type_needed and not z["mitigated"]]

    entry = None
    zone_used = None
    zone_source_tf = None
    entry_method = None

    if h4_matches:
        zone_4h = h4_matches[-1]
        confirmation = find_confirmation_entry(m1, zone_4h, zone_type_needed)
        if confirmation:
            entry = confirmation
            zone_used = zone_4h
            zone_source_tf = "4H"
            entry_method = "confirmation_sequence"

    if entry is None:
        h1_zones = detect_zones(h1, "H1")
        h1_all_matches = [z for z in h1_zones if z["type"] == zone_type_needed and not z["mitigated"]]

        same_day_session_matches = [z for z in h1_all_matches if _is_same_day_session_zone(z, reference_time)]
        off_window_matches = [z for z in h1_all_matches if not _is_same_day_session_zone(z, reference_time)]

        if same_day_session_matches:
            zone_1h = same_day_session_matches[-1]
            wick_entry = _check_wick_tap_entry(m5, m15, zone_1h, zone_type_needed)
            if wick_entry:
                entry = {
                    "entry_time": wick_entry["time"], "entry_candle": wick_entry["candle"].to_dict(),
                    "entry_price": wick_entry["entry_price"], "confirm_time": None, "breakout_time": None,
                }
                zone_used = zone_1h
                zone_source_tf = "1H"
                entry_method = "wick_tap"
            else:
                zone_used = zone_1h
                zone_source_tf = "1H"

        if entry is None and off_window_matches:
            zone_1h = off_window_matches[-1]
            confirmation = find_confirmation_entry(m1, zone_1h, zone_type_needed)
            if confirmation:
                entry = confirmation
                zone_used = zone_1h
                zone_source_tf = "1H"
                entry_method = "confirmation_sequence"
            elif zone_used is None:
                zone_used = zone_1h
                zone_source_tf = "1H"

    if zone_used is None and h4_matches:
        zone_used = h4_matches[-1]
        zone_source_tf = "4H"

    result["zone"] = zone_used
    result["zone_source_tf"] = zone_source_tf

    if entry:
        pip = 0.01 if "JPY" in pair.upper() else 0.0001

        candle_pools = {"H4": h4, "H1": h1, "15": m15, "5": m5, "1": m1}
        tf_order = ["H4", "H1", "15", "5", "1"]
        container_idx = tf_order.index(zone_source_tf if zone_source_tf in tf_order else "H1")
        ladder = []
        for tf_name in tf_order[container_idx + 1:]:
            pool = [z for z in detect_zones(candle_pools[tf_name], tf_name) if not z["mitigated"]]
            pick = _extreme_nested_zone(pool, zone_used)
            if pick:
                ladder.append(pick)

        plan = _plan_trade(zone_used, ladder, zone_type_needed, pip)

        c = entry["entry_candle"]
        result["entry"] = {
            "source_tf": zone_source_tf, "method": entry_method, "time": entry["entry_time"],
            "candle": {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]},
            "direction": "SHORT" if valid_direction == "bearish" else "LONG",
            "entry_price": plan["entry_price"], "stop_loss": plan["stop_loss"], "take_profit": plan["take_profit"],
            "sl_pips": plan["sl_pips"], "tp_pips": plan["tp_pips"], "entry_level": plan["entry_level"],
            "ladder": [(z.get("timeframe"), _level_price(z, zone_type_needed)) for z in ladder],
            "confirm_time": entry.get("confirm_time"), "breakout_time": entry.get("breakout_time"),
        }
        result["entry_method"] = entry_method

    if render_chart_image:
        out_dir = Path("rendered_charts")

        try:
            h1_zones_for_chart = [
                z for z in detect_zones(h1, "H1")
                if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]
            ]
            m15_all = [z for z in detect_zones(m15, "15")
                       if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
            m5_all = [z for z in detect_zones(m5, "5")
                      if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
            m1_all = [z for z in detect_zones(m1, "1")
                      if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]

            zones_for_chart = []
            for h1z in h1_zones_for_chart:
                zones_for_chart.append(h1z)
                for pool in (m15_all, m5_all, m1_all):
                    pick = _extreme_nested_zone(pool, h1z)
                    if pick:
                        zones_for_chart.append(pick)

            ref_ny = reference_time.tz_convert(NY_TZ) if reference_time.tzinfo else NY_TZ.localize(reference_time)
            session_start = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("06:00", "%H:%M").time()))
            session_end = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("13:00", "%H:%M").time()))

            m5_ny = m5.copy()
            m5_ny["time_ny"] = m5_ny["time"].dt.tz_convert(NY_TZ)
            m5_window = m5_ny[(m5_ny["time_ny"] >= session_start) & (m5_ny["time_ny"] <= session_end)].drop(columns=["time_ny"])

            out_path = out_dir / f"{pair}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            if len(m5_window) > 0:
                render_chart(m5_window, zones_for_chart, [], out_path,
                             title=f"{pair} -- 6am-1pm ET session only",
                             trade=result["entry"])
                result["chart_path"] = out_path
        except Exception as e:
            print(f"DEBUG: session chart render failed with: {e}")

        try:
            if zone_used:
                candle_pools = {"H4": h4, "H1": h1, "15": m15, "5": m5, "1": m1}
                zones_1h_chart = [zone_used]

                tf_order = ["H4", "H1", "15", "5", "1"]
                container_idx = tf_order.index(zone_source_tf if zone_source_tf in tf_order else "H1")
                for tf_name in tf_order[container_idx + 1:]:
                    pool_zones = [z for z in detect_zones(candle_pools[tf_name], tf_name) if not z["mitigated"]]
                    pick = _extreme_nested_zone(pool_zones, zone_used)
                    if pick:
                        zones_1h_chart.append(pick)

                window_start = zone_used["origin_time"] - pd.Timedelta(hours=8)
                window_end = (entry["entry_time"] if entry else reference_time) + pd.Timedelta(hours=4)
                h1_window = h1[(h1["time"] >= window_start) & (h1["time"] <= window_end)]

                out_path_1h = out_dir / f"{pair}_1H_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                if len(h1_window) > 0:
                    render_chart(h1_window, zones_1h_chart, [], out_path_1h,
                                 title=f"{pair} 1H candles -- {zone_source_tf} {zone_type_needed.upper()} setup",
                                 trade=result["entry"])
                    result["chart_path_1h"] = out_path_1h
        except Exception as e:
            print(f"DEBUG: 1H chart render failed with: {e}")

    return result
