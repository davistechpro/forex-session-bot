"""
Checks every trade the bot posted on a given day against the
candle-close rule, using the bot's own alert log (signal_map.json) and
real candles from OANDA. Nothing is sent to Telegram.

For each trade it rebuilds the market as of 5 minutes after the alert
and reports:
  - when the 1H/4H zone's push candle CLOSED ("zone ready")
  - when the bot's trigger happened
  - PASS if the trigger is at/after zone ready, FAIL if before

  py check_day.py 2026-10-01
"""
import json
import re
import sys
from datetime import datetime

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()

import modules.data_feed as data_feed
import modules.scan_engine as se
from modules.zone_detector import detect_zones

NY = pytz.timezone("America/New_York")
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D": 1440}
BIG = {"M1": 5000, "M5": 3000, "M15": 1500, "H1": 800, "H4": 400, "D": 200}

day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(NY).strftime("%Y-%m-%d")
signals = json.load(open("signal_map.json"))
todays = [(k, v) for k, v in signals.items() if str(v.get("triggered_at", "")).startswith(day)]
todays.sort(key=lambda kv: str(kv[1]["triggered_at"]))
if not todays:
    print(f"No trades logged for {day}.")
    sys.exit(0)

_real = data_feed.get_candles
_cache = {}
_now = {"t": None}


def closed(pair, tf, count):
    key = (pair, tf)
    if key not in _cache:
        _cache[key] = _real(pair, tf, count=BIG[tf])
    df = _cache[key]
    return df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= _now["t"]].tail(count).reset_index(drop=True)


se.get_candles = lambda p, tf, count=200: closed(p, tf, count)
ny = lambda t: pd.Timestamp(t).tz_convert(NY).strftime("%I:%M %p")

print(f"\nChecking {len(todays)} trade(s) from {day} against the candle-close rule\n")
results = []
for tok, s in todays:
    pair = s["pair"]
    m = re.match(r"(SUPPLY|DEMAND)\s+([\d.]+)-([\d.]+)\s+\((\w+)\)", s.get("zone", ""))
    if not m:
        print(f"{pair}: can't read zone from log -- {s.get('zone')}")
        continue
    zb, zt, tf = float(m.group(2)), float(m.group(3)), m.group(4)
    trig = pd.Timestamp(s["triggered_at"])
    if trig.tzinfo is None:
        trig = trig.tz_localize("UTC")
    _now["t"] = trig + pd.Timedelta(minutes=5)
    pip = se.pip_size(pair)

    try:
        gran, label, cnt = ("H4", "H4", 120) if tf == "4H" else ("H1", "H1", 200)
        zone = None
        for z in detect_zones(closed(pair, gran, cnt), label):
            if abs(z["bottom"] - zb) < pip / 2 and abs(z["top"] - zt) < pip / 2:
                zone = z
        if zone is None:
            print(f"{pair} {s['direction']}: zone {zb}-{zt} ({tf}) not found in candles as of {ny(_now['t'])} ET")
            continue
        ready = se.zone_ready_time(zone)
        ok = trig >= ready
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {pair} {s['direction']:5}  {s['method']:21} "
              f"zone {tf} formed {ny(zone['origin_time'])}, push candle {ny(zone['confirmed_time'])}, "
              f"ready {ny(ready)}  |  trigger {ny(trig)}  entry {s['entry_price']} ({s['entry_level']})")
        if not ok:
            print(f"      -> trigger is {(ready - trig).total_seconds() / 60:.0f} min BEFORE the zone's candle closed")
    except Exception as e:
        print(f"{pair} {s['direction']}: check failed -- {e}")

print(f"\n{sum(results)} pass, {len(results) - sum(results)} fail")
