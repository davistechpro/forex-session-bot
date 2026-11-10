"""
Trend v2 -- SHADOW ONLY until the team confirms it (Nov 10).

Same rule as modules/trend.py (most recent push on the timeframe, unless a
later rejection at a zone overrides it), with two fixes for the wrong-trend
posts Gage and Steven flagged Nov 5-9 (GBP/JPY, EUR/JPY, EUR/USD, GBP/USD):

  FIX A "touch" -- a rejection only counts if the touching candle actually
         ENTERS the zone (low <= top and high >= bottom), as the written rule
         says. trend.py only checks one side, so a big red candle anywhere
         ABOVE an old supply zone counted as a "bearish rejection" and could
         flip a bullish Daily to bearish. It also must happen while the zone
         was still alive (at or before the candle that used it up).

  FIX B "push" -- the most recent push is taken from EVERY push, including
         new zones the zone list skips because they overlap an older live
         zone. trend.py could miss the newest push and keep the older,
         opposite one (more likely since the Oct 28 zone fix keeps zones
         alive longer).

determine_trend_v2(candles, tf, fixes="AB") -> same dict as determine_trend.
fixes="A" or "B" applies one fix only, so the shadow log can show which one
made the difference.
"""
import pandas as pd

from modules.rejection import VOLUME_LOOKBACK, _is_engulfing
from modules.zone_detector import detect_zones


def _rejections(candles: pd.DataFrame, zones: list, strict_touch: bool) -> list:
    candles = candles.reset_index(drop=True)
    if "volume" not in candles.columns or len(candles) < VOLUME_LOOKBACK + 2:
        return []
    candles = candles.copy()
    candles["avg_volume"] = candles["volume"].rolling(VOLUME_LOOKBACK, min_periods=5).mean()
    out = []
    for zone in zones:
        future = candles[candles["time"] > zone["confirmed_time"]].reset_index(drop=True)
        for i in range(len(future) - 1):
            touch, nxt = future.iloc[i], future.iloc[i + 1]
            if strict_touch:
                if zone.get("mitigated_time") is not None and touch["time"] > zone["mitigated_time"]:
                    break                                   # zone already used up
                touched = touch["low"] <= zone["top"] and touch["high"] >= zone["bottom"]
            else:                                           # trend.py behaviour
                touched = (touch["high"] >= zone["bottom"]) if zone["type"] == "supply" \
                    else (touch["low"] <= zone["top"])
            if not touched or not _is_engulfing(touch, nxt):
                continue
            if zone["type"] == "supply":
                away = nxt["close"] < nxt["open"] and nxt["close"] < touch["low"]
                direction = "bearish"
            else:
                away = nxt["close"] > nxt["open"] and nxt["close"] > touch["high"]
                direction = "bullish"
            if not away:
                continue
            avg_vol, vol = nxt.get("avg_volume"), nxt.get("volume")
            if pd.isna(avg_vol) or vol is None or vol <= avg_vol:
                continue
            out.append({"zone": zone, "direction": direction, "touch_time": touch["time"],
                        "rejection_time": nxt["time"], "volume": vol, "avg_volume": avg_vol})
            break
    return out


def determine_trend_v2(candles: pd.DataFrame, timeframe_label: str = "", fixes: str = "AB") -> dict:
    candles = candles.reset_index(drop=True)
    zones = detect_zones(candles, timeframe_label=timeframe_label)
    pushes = detect_zones(candles, timeframe_label=timeframe_label, dedupe=False) if "B" in fixes else zones
    if not pushes:
        return {"trend": "unclear", "signal_type": None, "event_time": None, "detail": None}
    push = max(pushes, key=lambda z: z["confirmed_time"])
    push_time = push["confirmed_time"]
    push_trend = "bullish" if push["type"] == "demand" else "bearish"
    rej = _rejections(candles, zones, strict_touch="A" in fixes)
    last = max(rej, key=lambda r: r["rejection_time"]) if rej else None
    if last and last["rejection_time"] >= push_time:
        return {"trend": last["direction"], "signal_type": "rejection",
                "event_time": last["rejection_time"], "detail": last}
    return {"trend": push_trend, "signal_type": "push", "event_time": push_time, "detail": push}


def explain(t: dict) -> str:
    """One line: why this timeframe got this trend."""
    if not t or not t.get("signal_type"):
        return "unclear (no zones)"
    et = lambda x: pd.Timestamp(x).tz_convert("America/New_York").strftime("%a %b %d %I:%M %p")
    d = t["detail"]
    if t["signal_type"] == "push":
        return f"{t['trend']}: push {d['type']} {d['bottom']}-{d['top']} at {et(d['confirmed_time'])}"
    z = d["zone"]
    return (f"{t['trend']}: rejection at {z['type']} {z['bottom']}-{z['top']} "
            f"(touch {et(d['touch_time'])}, rejected {et(d['rejection_time'])})")
