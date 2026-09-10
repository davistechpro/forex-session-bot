"""
Supply/Demand Zone Detection — matches the logic proven correct in the
TradingView indicator (v49), including overlap-dedup, plus a body-close
invalidation rule.

MITIGATION -- a zone clears when EITHER:
  1. WICK RETEST: price wicks back into the zone from the opposite side.
  2. BODY-CLOSE BREAK: a candle BODY closes THROUGH the zone against it --
     above a supply zone's top, or below a demand zone's bottom. Price
     closing AWAY from a zone (below a supply, above a demand) is the
     zone WORKING, not failing, and must not clear it.
"""
import pandas as pd


def _is_red(row) -> bool:
    return row["close"] < row["open"]


def _is_green(row) -> bool:
    return row["close"] > row["open"]


def _overlaps_active_zone(zones: list, zone_type: str, top: float, bottom: float) -> bool:
    for z in zones:
        if z["type"] != zone_type or z["mitigated"]:
            continue
        z_top, z_bottom = z["top"], z["bottom"]
        if not (top < z_bottom or z_top < bottom):
            return True
    return False


def detect_zones(candles: pd.DataFrame, timeframe_label: str = "") -> list:
    candles = candles.reset_index(drop=True)
    n = len(candles)
    zones = []

    if n < 2:
        return zones

    for i in range(1, n):
        row = candles.iloc[i]
        is_red = _is_red(row)
        is_green = _is_green(row)

        bear_push = False
        if row["close"] < candles.iloc[i - 1]["low"]:
            if is_red:
                bear_push = True
        if i >= 2 and is_red and _is_red(candles.iloc[i - 1]):
            if row["close"] < candles.iloc[i - 2]["low"]:
                bear_push = True
        if i >= 3 and is_red and _is_red(candles.iloc[i - 1]) and _is_red(candles.iloc[i - 2]):
            if row["close"] < candles.iloc[i - 3]["low"]:
                bear_push = True

        bull_push = False
        if row["close"] > candles.iloc[i - 1]["high"]:
            if is_green:
                bull_push = True
        if i >= 2 and is_green and _is_green(candles.iloc[i - 1]):
            if row["close"] > candles.iloc[i - 2]["high"]:
                bull_push = True
        if i >= 3 and is_green and _is_green(candles.iloc[i - 1]) and _is_green(candles.iloc[i - 2]):
            if row["close"] > candles.iloc[i - 3]["high"]:
                bull_push = True

        if bear_push:
            for k in (1, 2, 3):
                if k > i:
                    break
                origin = candles.iloc[i - k]
                if _is_green(origin) and row["close"] < origin["low"]:
                    top, bottom = origin["high"], origin["low"]
                    if not _overlaps_active_zone(zones, "supply", top, bottom):
                        zones.append({
                            "type": "supply",
                            "timeframe": timeframe_label,
                            "origin_time": origin["time"],
                            "top": top,
                            "bottom": bottom,
                            "confirmed_time": row["time"],
                            "mitigated": False,
                            "mitigated_time": None,
                        })
                    break

        if bull_push:
            for k in (1, 2, 3):
                if k > i:
                    break
                origin = candles.iloc[i - k]
                if _is_red(origin) and row["close"] > origin["high"]:
                    top, bottom = origin["high"], origin["low"]
                    if not _overlaps_active_zone(zones, "demand", top, bottom):
                        zones.append({
                            "type": "demand",
                            "timeframe": timeframe_label,
                            "origin_time": origin["time"],
                            "top": top,
                            "bottom": bottom,
                            "confirmed_time": row["time"],
                            "mitigated": False,
                            "mitigated_time": None,
                        })
                    break

        for zone in zones:
            if zone["mitigated"]:
                continue
            if row["time"] <= zone["confirmed_time"]:
                continue

            if zone["type"] == "supply":
                wick_retest = is_green and row["high"] >= zone["bottom"]
                body_break = row["close"] > zone["top"]
                if wick_retest or body_break:
                    zone["mitigated"] = True
                    zone["mitigated_time"] = row["time"]
            elif zone["type"] == "demand":
                wick_retest = is_red and row["low"] <= zone["top"]
                body_break = row["close"] < zone["bottom"]
                if wick_retest or body_break:
                    zone["mitigated"] = True
                    zone["mitigated_time"] = row["time"]

    return zones
