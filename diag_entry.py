"""
Shows WHY the bot picked the entry level it did for one trade.

Rebuilds the market exactly as the bot saw it at a given time (only
candles that had closed), finds the 1H/4H zone, lists every small
15m / 5m / 1m zone it counted as "inside" it, and walks the entry +
stop cascade step by step.

  py diag_entry.py EUR_JPY 2026-09-30 10:05 178.206 178.439
                   pair    day        time(ET) zone-bottom zone-top
"""
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

pair, day, hhmm, zb, zt = sys.argv[1].upper(), sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
at = NY.localize(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M"))
at_utc = pd.Timestamp(at).tz_convert("UTC")

_real = data_feed.get_candles
_cache = {}


def closed(tf, count):
    if tf not in _cache:
        _cache[tf] = _real(pair, tf, count=BIG[tf])
    df = _cache[tf]
    return df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= at_utc].tail(count).reset_index(drop=True)


se.get_candles = lambda p, tf, count=200: closed(tf, count)
pip = se.pip_size(pair)
d = 3 if "JPY" in pair else 5
f = lambda x: f"{x:.{d}f}"
ny = lambda t: pd.Timestamp(t).tz_convert(NY).strftime("%m-%d %I:%M %p")

print(f"\n{pair} as the bot saw it at {at:%Y-%m-%d %I:%M %p} ET\n")

r = se.run_scan(pair, render_chart_image=False)
print(f"Direction: {r['valid_direction']}  ({r['deciding_tf']})")
e = r.get("entry")
if e:
    print(f"Bot's call: {e['direction']} entry {f(e['entry_price'])} on {e['entry_level']} zone, "
          f"SL {e['sl_pips']} -- {e['method']}, trigger {ny(e['time'])} ET")
else:
    print("Bot's call at this time: no entry" + (f" ({r['skip_reason']})" if r.get("skip_reason") else ""))

# container zone (the one you passed in)
container = None
for tf, label in (("H1", "H1"), ("H4", "H4")):
    for z in detect_zones(closed(tf, 200 if tf == "H1" else 120), label):
        if abs(z["bottom"] - zb) < pip / 2 and abs(z["top"] - zt) < pip / 2:
            container = z
if not container:
    print(f"\nCould not find a zone {zb}-{zt} -- check the numbers.")
    sys.exit(1)
ztype = container["type"]
print(f"\nBig zone: {container['timeframe']} {ztype.upper()} {f(container['bottom'])}-{f(container['top'])}  "
      f"formed {ny(container['origin_time'])}  ready {ny(se.zone_ready_time(container))}")

# every small zone the bot counted as inside it
rows = []
for tf, label, cnt in (("M15", "15", 200), ("M5", "5", 300), ("M1", "1", 1500)):
    for z in detect_zones(closed(tf, cnt), label):
        if z["mitigated"] or z["type"] != ztype:
            continue
        if z["top"] < container["bottom"] or container["top"] < z["bottom"]:
            continue
        inside = z["bottom"] >= container["bottom"] - 1e-9 and z["top"] <= container["top"] + 1e-9
        rows.append((z, inside))

if not rows:
    print("\nNo untouched 15m/5m/1m zones inside it -- entry would be the big zone's edge.")
    sys.exit(0)

key = (lambda z: -z["top"]) if ztype == "demand" else (lambda z: z["bottom"])
rows.sort(key=lambda x: key(x[0]))
edge = "top" if ztype == "demand" else "bottom"
print(f"\nSmall zones counted, in the order price reaches them ({edge} edge = entry price):")
for i, (z, inside) in enumerate(rows, 1):
    tfn = {"15": "15m", "5": "5m", "1": "1m"}[z["timeframe"]]
    where = "fully inside" if inside else "STICKS OUT of the big zone"
    print(f"  {i}. {tfn:3}  {f(z['bottom'])}-{f(z['top'])}  entry {f(z[edge])}  "
          f"formed {ny(z['origin_time'])}  [{where}]")

plan = se._plan_trade(container, [z for z, _ in rows], ztype, pip)
lvl = {"15": "15m", "5": "5m", "1": "1m", "H1": "1H", "H4": "4H"}.get(plan["entry_level"], plan["entry_level"])
print(f"\nStop cascade result: entry {f(plan['entry_price'])} on the {lvl} zone, SL {plan['sl_pips']} pips")
print("  (8 pips must reach past the next deeper zone; if not, 10; if not, the entry moves down a step)")
