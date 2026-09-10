"""
Confirmation-Sequence Entry Logic

Per the strategy: a 4H zone (always) or a 1H zone occurring OUTSIDE the
6:00 AM-12:00 PM ET window requires this exact sequence on the 1-minute
chart before an entry is valid:

  1. Price hits the higher-timeframe zone
  2. A 1-minute candle's BODY closes within that zone
  3. Price then BREAKS OUT -- a later 1m candle closes beyond the zone
     in the anticipated trade direction
  4. Price RETURNS to tag the zone again -- THAT return is the entry
     trigger, at the zone's edge
"""
import pandas as pd


def find_confirmation_entry(m1_candles: pd.DataFrame, zone: dict, zone_type: str):
    m1_candles = m1_candles.reset_index(drop=True)
    after_zone = m1_candles[m1_candles["time"] > zone["confirmed_time"]].reset_index(drop=True)

    if len(after_zone) == 0:
        return None

    confirm_idx = None
    for idx, c in after_zone.iterrows():
        if zone["bottom"] <= c["close"] <= zone["top"]:
            confirm_idx = idx
            break

    if confirm_idx is None:
        return None

    confirm_candle = after_zone.iloc[confirm_idx]

    breakout_idx = None
    for idx in range(confirm_idx + 1, len(after_zone)):
        c = after_zone.iloc[idx]
        if zone_type == "demand" and c["close"] > zone["top"]:
            breakout_idx = idx
            break
        if zone_type == "supply" and c["close"] < zone["bottom"]:
            breakout_idx = idx
            break

    if breakout_idx is None:
        return None

    breakout_candle = after_zone.iloc[breakout_idx]

    entry_idx = None
    for idx in range(breakout_idx + 1, len(after_zone)):
        c = after_zone.iloc[idx]
        if zone_type == "demand" and c["low"] <= zone["top"]:
            entry_idx = idx
            break
        if zone_type == "supply" and c["high"] >= zone["bottom"]:
            entry_idx = idx
            break

    if entry_idx is None:
        return None

    entry_candle = after_zone.iloc[entry_idx]
    entry_price = zone["top"] if zone_type == "demand" else zone["bottom"]

    return {
        "confirm_time": confirm_candle["time"],
        "confirm_candle": confirm_candle.to_dict(),
        "breakout_time": breakout_candle["time"],
        "breakout_candle": breakout_candle.to_dict(),
        "entry_time": entry_candle["time"],
        "entry_candle": entry_candle.to_dict(),
        "entry_price": entry_price,
    }
