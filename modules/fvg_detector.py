"""
PHASE 3 — Fair Value Gap Detection (pure code, no AI)

An FVG is a strict 3-candle pattern: candle 1's high/low leaves a gap that
candle 3 doesn't fill. This is fully mathematical and should run against
every candle close, not off a screenshot.

Exit gate: code catches every FVG a manual chart review finds, with no
false positives, across a multi-week backtest.
"""
import pandas as pd


def detect_fvgs(candles: pd.DataFrame) -> list[dict]:
    """
    Scan a DataFrame of OHLC candles (columns: time, open, high, low, close)
    for 3-candle fair value gaps.

    Returns a list of dicts, e.g.:
        {"start_time": ..., "gap_high": ..., "gap_low": ..., "direction": "bullish"}

    TODO (Phase 3):
      - Iterate candles in overlapping groups of 3
      - Bullish FVG: candle[0].high < candle[2].low  -> gap between them
      - Bearish FVG: candle[0].low  > candle[2].high -> gap between them
      - Filter out gaps smaller than settings["fvg"]["min_gap_pips"]
    """
    raise NotImplementedError("Phase 3: implement 3-candle FVG scan here")


if __name__ == "__main__":
    print("fvg_detector.py — implement detect_fvgs() and backtest against historical candles")
