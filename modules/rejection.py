"""
Rejection Detection — per team clarification (Aug 2026):

A REJECTION at a zone is a distinct, additional trend-flip signal beyond
the original "price closes beyond the zone" push rule. It requires:

  1. Price touches the zone (wick or body enters the zone's range)
  2. The candle immediately after that touch is an ENGULFING candle
     (its body fully contains the touching candle's body) moving AWAY
     from the zone
  3. That engulfing candle has ABOVE-AVERAGE volume (vs. a rolling
     average of recent candles)

This applies within a single timeframe's own read only -- it does not
override the 4H/1H cross-timeframe hierarchy.
"""
import pandas as pd

VOLUME_LOOKBACK = 20


def _is_engulfing(prior_candle, candle) -> bool:
    prior_low, prior_high = min(prior_candle["open"], prior_candle["close"]), max(prior_candle["open"], prior_candle["close"])
    body_low, body_high = min(candle["open"], candle["close"]), max(candle["open"], candle["close"])
    return body_low <= prior_low and body_high >= prior_high


def detect_rejections(candles: pd.DataFrame, zones: list[dict]) -> list[dict]:
    candles = candles.reset_index(drop=True)
    if "volume" not in candles.columns or len(candles) < VOLUME_LOOKBACK + 2:
        return []

    candles["avg_volume"] = candles["volume"].rolling(VOLUME_LOOKBACK, min_periods=5).mean()

    rejections = []

    for zone in zones:
        future = candles[candles["time"] > zone["confirmed_time"]].reset_index(drop=True)
        for i in range(len(future) - 1):
            touch_candle = future.iloc[i]
            next_candle = future.iloc[i + 1]

            touched = False
            if zone["type"] == "supply" and touch_candle["high"] >= zone["bottom"]:
                touched = True
            if zone["type"] == "demand" and touch_candle["low"] <= zone["top"]:
                touched = True
            if not touched:
                continue

            if not _is_engulfing(touch_candle, next_candle):
                continue

            if zone["type"] == "supply":
                moving_away = next_candle["close"] < next_candle["open"] and next_candle["close"] < touch_candle["low"]
                direction = "bearish"
            else:
                moving_away = next_candle["close"] > next_candle["open"] and next_candle["close"] > touch_candle["high"]
                direction = "bullish"

            if not moving_away:
                continue

            avg_vol = next_candle.get("avg_volume")
            vol = next_candle.get("volume")
            if pd.isna(avg_vol) or vol is None or vol <= avg_vol:
                continue

            rejections.append({
                "zone": zone,
                "direction": direction,
                "touch_time": touch_candle["time"],
                "rejection_time": next_candle["time"],
                "volume": vol,
                "avg_volume": avg_vol,
            })
            break

    return rejections
