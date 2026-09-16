"""
Confirmation-Sequence Entry Logic

Per the strategy, a 4H zone (always) or an off-window 1H zone requires
this sequence on the 1-minute chart before an entry is valid:

  1. A 1-minute candle BODY closes within the zone
  2. Price BREAKS OUT -- a later 1m candle closes beyond the zone
  3. Price RETURNS to tag the zone again -- that return is the trigger
"""
import pandas as pd


def find_confirmation_entry(m1_candles: pd.DataFrame, zone: dict, zone_type: str,
                            max_age_minutes: int = None):
    """
    max_age_minutes: if set, the ENTRY trigger must have occurred within
    this many minutes of the most recent candle. Without it the scan
    returns the first valid sequence anywhere in the M1 history, which
    surfaces stale overnight setups as if they were live.
    """
    m1_candles = m1_candles.reset_index(drop=True)
    after_zone = m1_candles[m1_candles["time"] > zone["confirmed_time"]].reset_index(drop=True)

    if len(after_zone) == 0:
        return None

    cutoff = None
    if max_age_minutes is not None and len(m1_candles) > 0:
        cutoff = m1_candles["time"].max() - pd.Timedelta(minutes=max_age_minutes)

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

    # Reject stale sequences -- the trigger has to be recent to be actionable.
    if cutoff is not None and entry_candle["time"] < cutoff:
        return None

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
