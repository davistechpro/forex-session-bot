"""
PHASE 3 — Fair Value Gap Detection (pure code, no AI)

An FVG is a strict 3-candle pattern:
  - Bullish FVG: candle[0].high < candle[2].low  (gap gets left behind as price
    rips upward; candle[1] is the big displacement candle in between)
  - Bearish FVG: candle[0].low  > candle[2].high (same idea, to the downside)

This is fully mathematical — every candle close, no interpretation needed.

Exit gate: code catches every FVG a manual chart review finds, with no
false positives, across a multi-week backtest.
"""
import pandas as pd

from modules.config_loader import load_settings

settings = load_settings()
MIN_GAP_PIPS = settings["fvg"]["min_gap_pips"]

# EUR/USD: 1 pip = 0.0001. If you trade pairs with different pip sizing
# (e.g. JPY pairs at 0.01), this needs to become pair-aware later.
PIP_SIZE = 0.0001


def detect_fvgs(candles: pd.DataFrame) -> list[dict]:
    """
    Scan a DataFrame of OHLC candles (columns: time, open, high, low, close)
    for 3-candle fair value gaps.

    Returns a list of dicts:
        {
            "start_time": <time of candle[0]>,
            "end_time": <time of candle[2]>,
            "direction": "bullish" | "bearish",
            "gap_high": float,
            "gap_low": float,
            "gap_pips": float,
        }
    """
    if len(candles) < 3:
        return []

    fvgs = []
    candles = candles.reset_index(drop=True)

    for i in range(len(candles) - 2):
        c0 = candles.iloc[i]
        c2 = candles.iloc[i + 2]

        # Bullish FVG: candle[0]'s high is below candle[2]'s low — gap between them
        if c0["high"] < c2["low"]:
            gap_pips = (c2["low"] - c0["high"]) / PIP_SIZE
            if gap_pips >= MIN_GAP_PIPS:
                fvgs.append({
                    "start_time": c0["time"],
                    "end_time": c2["time"],
                    "direction": "bullish",
                    "gap_high": c2["low"],
                    "gap_low": c0["high"],
                    "gap_pips": round(gap_pips, 1),
                })

        # Bearish FVG: candle[0]'s low is above candle[2]'s high — gap between them
        if c0["low"] > c2["high"]:
            gap_pips = (c0["low"] - c2["high"]) / PIP_SIZE
            if gap_pips >= MIN_GAP_PIPS:
                fvgs.append({
                    "start_time": c0["time"],
                    "end_time": c2["time"],
                    "direction": "bearish",
                    "gap_high": c0["low"],
                    "gap_low": c2["high"],
                    "gap_pips": round(gap_pips, 1),
                })

    return fvgs


def is_fvg_filled(fvg: dict, candles_after: pd.DataFrame) -> bool:
    """
    Check whether a given FVG has since been filled (price traded back
    through the gap). Useful later for filtering to only "fresh" /
    unmitigated FVGs when deciding what to hand the AI judgment layer.
    """
    for _, c in candles_after.iterrows():
        if fvg["direction"] == "bullish" and c["low"] <= fvg["gap_low"]:
            return True
        if fvg["direction"] == "bearish" and c["high"] >= fvg["gap_high"]:
            return True
    return False


if __name__ == "__main__":
    # Manual backtest — run: python -m modules.fvg_detector
    # Pulls real candles via Phase 2 and prints every FVG found, so you can
    # cross-check the list against what you'd mark by eye on the chart.
    from modules.data_feed import get_candles

    pair = settings["instrument"]["pair"]
    print(f"Pulling last 200 M15 candles for {pair}...")
    candles = get_candles(pair, "M15", count=200)

    print(f"Scanning {len(candles)} candles for FVGs (min gap: {MIN_GAP_PIPS} pips)...\n")
    fvgs = detect_fvgs(candles)

    if not fvgs:
        print("No FVGs found in this window.")
    else:
        for fvg in fvgs:
            print(
                f"{fvg['direction'].upper():8} | "
                f"{fvg['start_time']} -> {fvg['end_time']} | "
                f"{fvg['gap_low']:.5f} - {fvg['gap_high']:.5f} "
                f"({fvg['gap_pips']} pips)"
            )
        print(f"\nTotal FVGs found: {len(fvgs)}")
