"""
Replay a past session through the CURRENT scan logic, as if the watcher
had run live every 15 minutes. Shows which trades would have been called
and when -- nothing is sent to Telegram.

  py replay_day.py                 # today
  py replay_day.py 2026-09-30      # a specific day
  py replay_day.py 2026-09-30 EUR_USD,GBP_USD   # specific pairs
"""
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()

import modules.data_feed as data_feed
import modules.scan_engine as se

NY = pytz.timezone("America/New_York")
PAIRS = ["EUR_USD", "EUR_JPY", "GBP_CAD", "USD_JPY", "USD_CAD", "GBP_USD", "GBP_JPY"]
if len(sys.argv) > 2:
    PAIRS = [p.strip().upper() for p in sys.argv[2].split(",")]
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D": 1440}
BIG = {"M1": 5000, "M5": 3000, "M15": 1500, "H1": 800, "H4": 400, "D": 200}

day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(NY).strftime("%Y-%m-%d")
d = datetime.strptime(day, "%Y-%m-%d")
start = NY.localize(d.replace(hour=9, minute=0))
end = NY.localize(d.replace(hour=12, minute=55))

_real_get = data_feed.get_candles
cache = {}
now_t = {"t": None}


def fake_get(pair, tf, count=200):
    key = (pair, tf)
    if key not in cache:
        cache[key] = _real_get(pair, tf, count=BIG[tf])
    df = cache[key]
    # only candles that had CLOSED by the simulated time
    closed = df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= now_t["t"]]
    return closed.tail(count).reset_index(drop=True)


se.get_candles = fake_get

print(f"Replaying {day} 9:00 AM - 12:55 PM ET, every 15 min, current logic\n")
seen = set()
for pair in PAIRS:
    why = se.trading_day_block_reason(pair, when=start)
    if why:
        print(f"{pair}: blocked ({why})")
        continue
    t = start
    while t <= end:
        now_t["t"] = pd.Timestamp(t).tz_convert("UTC")
        try:
            r = se.run_scan(pair, render_chart_image=False)
        except Exception as e:
            print(f"{pair} {t:%H:%M}: scan failed -- {e}")
            break
        e = r.get("entry")
        if e:
            z = r["zone"]
            key = (pair, e["direction"], str(z["origin_time"]))
            if key not in seen:
                seen.add(key)
                trig = pd.Timestamp(e["time"]).tz_convert(NY)
                ready = se.zone_ready_time(z).tz_convert(NY)
                print(f"{pair} {e['direction']:5}  alert {t:%I:%M %p}  trigger {trig:%I:%M %p}  "
                      f"zone {r['zone_source_tf']} ready {ready:%I:%M %p}  "
                      f"entry {e['entry_price']} ({e['entry_level']}) SL {e['sl_pips']}  {e['method']}")
        t += timedelta(minutes=15)

print(f"\n{len(seen)} trade(s) would have been called.")
