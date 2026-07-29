"""
PHASE 3b — Supply/Demand Zone Detection (ported from Pine Script indicator)

Faithful port of the "Custom S&D Zones" TradingView indicator's core logic:

  PUSH DETECTION:
    A bearish push = current candle is red AND closes below the low of the
    candle 1, 2, or 3 bars back (with the intervening candles also red).
    A bullish push = mirror image, with green candles breaking above highs.

  ZONE ORIGIN:
    When a bearish push is confirmed, look back 1-3 bars for the most
    recent GREEN candle whose low the push broke -- that candle's high/low
    range becomes a SUPPLY zone (the last up-candle before a down move).
    Bullish push -> look back for a RED candle whose high was broken ->
    that becomes a DEMAND zone (the last down-candle before an up move).

  MITIGATION:
    A supply zone is mitigated (closed out) the first time a later green
    candle's high trades back up into the zone's bottom.
    A demand zone is mitigated the first time a later red candle's low
    trades back down into the zone's top.
    (This asymmetry -- supply checks its bottom, demand checks its top --
    matches the original indicator's logic exactly, intentional or not.)

This is pure price-action math, same as FVG detection -- no AI involved.
"""
import pandas as pd


def _is_red(row) -> bool:
    return row["close"] < row["open"]


def _is_green(row) -> bool:
    return row["close"] > row["open"]


def detect_zones(candles: pd.DataFrame, timeframe_label: str = "") -> list[dict]:
    """
    Scan a DataFrame of OHLC candles (columns: time, open, high, low, close)
    for supply/demand zones using the same push+origin logic as the
    TradingView indicator.

    Zones are returned in the order they were created. Mitigation is
    evaluated against every candle after the zone's confirmed_time.
    """
    candles = candles.reset_index(drop=True)
    n = len(candles)
    zones: list[dict] = []

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
                    already_active = any(
                        z["type"] == "supply"
                        and z["origin_time"] == origin["time"]
                        and not z["mitigated"]
                        for z in zones
                    )
                    if not already_active:
                        zones.append({
                            "type": "supply",
                            "timeframe": timeframe_label,
                            "origin_time": origin["time"],
                            "top": origin["high"],
                            "bottom": origin["low"],
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
                    already_active = any(
                        z["type"] == "demand"
                        and z["origin_time"] == origin["time"]
                        and not z["mitigated"]
                        for z in zones
                    )
                    if not already_active:
                        zones.append({
                            "type": "demand",
                            "timeframe": timeframe_label,
                            "origin_time": origin["time"],
                            "top": origin["high"],
                            "bottom": origin["low"],
                            "confirmed_time": row["time"],
                            "mitigated": False,
                            "mitigated_time": None,
                        })
                    break

    for zone in zones:
        future = candles[candles["time"] > zone["confirmed_time"]]
        for _, c in future.iterrows():
            if zone["type"] == "supply" and _is_green(c) and c["high"] >= zone["bottom"]:
                zone["mitigated"] = True
                zone["mitigated_time"] = c["time"]
                break
            if zone["type"] == "demand" and _is_red(c) and c["low"] <= zone["top"]:
                zone["mitigated"] = True
                zone["mitigated_time"] = c["time"]
                break

    return zones


if __name__ == "__main__":
    from modules.config_loader import load_settings
    from modules.data_feed import get_candles

    settings = load_settings()
    pair = settings["instrument"]["pair"]

    print(f"Pulling last 200 M15 candles for {pair}...")
    candles = get_candles(pair, "M15", count=200)

    print(f"Scanning {len(candles)} candles for supply/demand zones...\n")
    zones = detect_zones(candles, timeframe_label="M15")

    for z in zones:
        status = "ACTIVE" if not z["mitigated"] else f"mitigated @ {z['mitigated_time']}"
        print(
            f"{z['type'].upper():7} | origin {z['origin_time']} | "
            f"confirmed {z['confirmed_time']} | "
            f"{z['bottom']:.5f} - {z['top']:.5f} | {status}"
        )
    print(f"\nTotal zones found: {len(zones)}")
    print(f"Still active (unmitigated): {sum(1 for z in zones if not z['mitigated'])}")
