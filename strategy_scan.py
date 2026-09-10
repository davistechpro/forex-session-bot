"""
Strategy Scan (CLI) — thin wrapper around scan_engine.py that prints
results to the terminal. The dashboard uses the same scan_engine, so
both stay in sync automatically.

Run with:  python strategy_scan.py [PAIR]
"""
import sys

from modules.config_loader import load_settings
from modules.scan_engine import run_scan

settings = load_settings()


def print_scan(pair: str):
    print(f"\n{'='*70}\nSTRATEGY SCAN — {pair}\n{'='*70}\n")
    print("Pulling candles and running scan...")

    result = run_scan(pair, render_chart_image=True)

    print("\n--- STEP 1: TREND ---")
    dt = result["daily_trend"]
    h4 = result["h4_trend"]
    h1 = result["h1_trend"]
    print(f"Daily: {dt['trend']}  (via {dt['signal_type']})")
    print(f"4H:    {h4['trend']}  (via {h4['signal_type']} at {h4['event_time']})")
    print(f"1H:    {h1['trend']}  (via {h1['signal_type']} at {h1['event_time']})")

    if result["valid_direction"]:
        print(f"\n>>> Valid direction: {result['valid_direction'].upper()} (per {result['deciding_tf']})")
    else:
        print("\n>>> No valid direction - no 2-of-3 agreement across Daily/4H/1H. NO TRADE.")
        return

    print("\n--- STEP 2: ZONE ---")
    zone = result["zone"]
    if not zone:
        zone_type_needed = "demand" if result["valid_direction"] == "bullish" else "supply"
        print(f"No active {zone_type_needed} zone found. NO TRADE.")
        return

    print(f"Zone found ({result['zone_source_tf']}): {zone['type'].upper()} {zone['bottom']:.5f}-{zone['top']:.5f}")
    print(f"Origin: {zone['origin_time']}  Confirmed: {zone['confirmed_time']}")

    print("\n--- STEP 3/4: ENTRY CHECK ---")
    print("(1H zone in 6am-12pm ET window -> wick tap | 4H zone or off-window 1H -> confirmation sequence)")
    entry = result["entry"]
    if entry:
        c = entry["candle"]
        print(f"\nENTRY METHOD: {entry['method']}  (source: {entry['source_tf']})")
        if entry.get("confirm_time"):
            print(f"  Confirmed at:  {entry['confirm_time']}")
        if entry.get("breakout_time"):
            print(f"  Broke out at:  {entry['breakout_time']}")
        print(f"  Entry at:      {entry['time']}")
        print(f"Candle: O{c['open']:.5f} H{c['high']:.5f} L{c['low']:.5f} C{c['close']:.5f}")
        lvl = {"H4": "4H", "H1": "1H", "15": "15m", "5": "5m", "1": "1m"}.get(entry.get("entry_level", ""), entry.get("entry_level", entry["source_tf"]))
        print(f"\n>>> TRADE: {entry['direction']} @ {entry['entry_price']:.5f}  (entry off the {lvl} level)")
        print(f">>> SL: {entry['stop_loss']:.5f} ({entry.get('sl_pips', 8)} pips)  TP: {entry['take_profit']:.5f} ({entry.get('tp_pips', 16)} pips, 2R)")
        if entry.get("ladder"):
            names = {"H1": "1H", "15": "15m", "5": "5m", "1": "1m"}
            print("    nested ladder: " + "  ->  ".join(f"{names.get(tf, tf)} {px:.5f}" for tf, px in entry["ladder"]))
    else:
        print("\nNo valid entry sequence found yet. NO TRADE.")

    if result["chart_path"]:
        print(f"\n--- CHART SAVED (6am-1pm ET session view) ---")
        print(f"Saved: {result['chart_path']}")
    else:
        print(f"\n--- No session-view chart (no active 1H zone in today's 6am-12pm ET window) ---")

    if result["chart_path_1h"]:
        print(f"\n--- CHART SAVED (1H candles, actual trade setup) ---")
        print(f"Saved: {result['chart_path_1h']}")


if __name__ == "__main__":
    pair = sys.argv[1] if len(sys.argv) > 1 else settings["instrument"]["pair"]
    print_scan(pair)

