"""
Setup Planner -- builds the "trade idea" for the SETUPS chat.

A SETUP is a 4H/1H zone that qualifies under Jordan's Strategy v3
(direction agrees 2-of-3, right zone type, untouched, and either formed
today 6 AM - 1 PM ET or within the last 7 days) but has NOT triggered
yet: no wick tap (today's zones) or no finished 1-minute confirmation
(older zones).

Nothing in scan_engine.py changes. This reads run_scan()'s result and
reuses the same entry/stop logic (_plan_trade) so the planned entry is
exactly what the bot would call if the setup triggers.
"""
import pandas as pd

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.scan_engine import (
    pip_size, _to_ny, _is_same_day_session_zone, _nested_in, _plan_trade,
)


def build_setup(pair: str, result: dict):
    """Returns a setup dict, or None if there is no untriggered qualifying zone."""
    zone = result.get("zone")
    if not zone or result.get("entry") or not result.get("valid_direction"):
        return None

    zone_type = zone["type"]
    tf = result.get("zone_source_tf") or ""
    pip = pip_size(pair)
    formed = _to_ny(zone["origin_time"]).strftime("%a %b %d %I:%M %p ET")
    skip = result.get("skip_reason")

    if _is_same_day_session_zone(zone, pd.Timestamp.now(tz="UTC")):
        # Doc 5.1: today's zone -> wick tap on 15m/5m. Entry is planned
        # with the same nested-zone ladder + stop cascade the trade uses.
        nested = []
        for tf_name, gran, count in (("15", "M15", 200), ("5", "M5", 300), ("1", "M1", 1500)):
            try:
                zs = [z for z in detect_zones(get_candles(pair, gran, count=count), tf_name)
                      if not z["mitigated"]]
            except Exception as e:
                print(f"  {pair}: setup planning skipped {gran} ({e})")
                continue
            nested.extend(_nested_in(zs, zone))
        plan = _plan_trade(zone, nested, zone_type, pip)
        return {
            "kind": "wick_tap",
            "waiting_for": f"a 15m/5m wick into the {tf} zone (9 AM - 12:59 PM ET)",
            "formed": formed,
            "planned_entry": plan["entry_price"],
            "planned_level": plan["entry_level"],
            "planned_sl": plan["stop_loss"],
            "planned_sl_pips": plan["sl_pips"],
            "planned_tp": plan["take_profit"],
            "note": skip,
        }

    # Doc 5.2: earlier zone -> 1-minute confirmation, then entry at the
    # newest 1m zone formed after it. That 1m zone doesn't exist yet.
    return {
        "kind": "confirmation",
        "waiting_for": f"1m confirmation at the {tf} zone (body close in, break out, 1m zone forms)",
        "formed": formed,
        "planned_entry": None,
        "note": skip,
    }
