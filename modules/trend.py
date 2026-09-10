"""
Trend Determination — implements the strategy's trend rule, including
the team's rejection-signal clarification (Aug 2026).
"""
import pandas as pd
from modules.zone_detector import detect_zones
from modules.rejection import detect_rejections


def determine_trend(candles: pd.DataFrame, timeframe_label: str = "") -> dict:
    candles = candles.reset_index(drop=True)
    zones = detect_zones(candles, timeframe_label=timeframe_label)

    if not zones:
        return {"trend": "unclear", "signal_type": None, "event_time": None, "detail": None}

    most_recent_push = zones[-1]
    push_time = most_recent_push["confirmed_time"]
    push_trend = "bullish" if most_recent_push["type"] == "demand" else "bearish"

    rejections = detect_rejections(candles, zones)
    most_recent_rejection = max(rejections, key=lambda r: r["rejection_time"]) if rejections else None

    if most_recent_rejection and most_recent_rejection["rejection_time"] >= push_time:
        return {
            "trend": most_recent_rejection["direction"],
            "signal_type": "rejection",
            "event_time": most_recent_rejection["rejection_time"],
            "detail": most_recent_rejection,
        }
    else:
        return {
            "trend": push_trend,
            "signal_type": "push",
            "event_time": push_time,
            "detail": most_recent_push,
        }


if __name__ == "__main__":
    from modules.config_loader import load_settings
    from modules.data_feed import get_candles

    settings = load_settings()
    pair = settings["instrument"]["pair"]

    for tf in ["D", "H4", "H1"]:
        try:
            candles = get_candles(pair, tf, count=200)
        except Exception as e:
            print(f"{tf}: could not pull candles ({e})")
            continue
        result = determine_trend(candles, timeframe_label=tf)
        print(f"{tf}: {result['trend']}  (via {result['signal_type']} at {result['event_time']})")
