"""
Why didn't a pair post? Replays one pair through today's session (or a past
day) with the CURRENT logic, every 15 minutes, and prints what the bot saw:
trend, direction, the zones it had, which were used up (mitigated) and when,
and whether it found a trigger. Sends nothing.

  py why_no_post.py USD_JPY
  py why_no_post.py USD_JPY 2026-10-21
"""
import sys
from datetime import datetime, timedelta

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

pair = (sys.argv[1] if len(sys.argv) > 1 else "USD_JPY").upper()
day = sys.argv[2] if len(sys.argv) > 2 else datetime.now(NY).strftime("%Y-%m-%d")
d = datetime.strptime(day, "%Y-%m-%d")
start = NY.localize(d.replace(hour=9, minute=0))
end = min(NY.localize(d.replace(hour=12, minute=55)), datetime.now(NY))

_real_get = data_feed.get_candles
cache, now_t = {}, {"t": None}


def fake_get(p, tf, count=200):
    key = (p, tf)
    if key not in cache:
        cache[key] = _real_get(p, tf, count=BIG[tf])
    df = cache[key]
    closed = df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= now_t["t"]]
    return closed.tail(count).reset_index(drop=True)


se.get_candles = fake_get


def et(ts):
    return pd.Timestamp(ts).tz_convert(NY).strftime("%a %I:%M %p") if ts is not None else "-"


print(f"{pair} on {day}, 9:00 AM - {end:%I:%M %p} ET, every 15 min (current logic)\n")
why = se.trading_day_block_reason(pair, when=start)
if why:
    print(f"BLOCKED for the day: {why}")
    raise SystemExit

last_line = None
t = start
while t <= end:
    now_t["t"] = pd.Timestamp(t).tz_convert("UTC")
    r = se.run_scan(pair, render_chart_image=False)
    trends = "/".join(str((r.get(k) or {}).get("trend", "?"))[:4] for k in ("daily_trend", "h4_trend", "h1_trend"))
    z = r.get("zone")
    zone = (f"{r['zone_source_tf']} {z['type']} {z['bottom']}-{z['top']} ready {et(se.zone_ready_time(z))}"
            if z else "no usable zone")
    e = r.get("entry")
    out = (f"TRIGGER {e['method']} at {et(e['time'])}, entry {e['entry_price']}" if e
           else (r.get("skip_reason") or "no trigger yet"))
    line = f"D/4H/1H {trends} -> {r.get('valid_direction') or 'NO DIRECTION'} | {zone} | {out}"
    if line != last_line:
        print(f"{t:%I:%M %p}  {line}")
        last_line = line
    t += timedelta(minutes=15)

# Every 4H / 1H zone from the last 2 days, including ones the bot threw away
now_t["t"] = pd.Timestamp(end).tz_convert("UTC")
print("\nZones from the last 2 days (what the bot could pick from):")
cut = pd.Timestamp(end).tz_convert("UTC") - pd.Timedelta(days=2)
for tf in ("H4", "H1"):
    zs = [z for z in detect_zones(fake_get(pair, tf, 200), tf) if z["origin_time"] >= cut]
    for z in zs:
        status = (f"USED UP {et(z['mitigated_time'])}" if z["mitigated"] else "active")
        ready = se.zone_ready_time({**z, "timeframe": tf})
        early = ""
        if z["mitigated"] and z["mitigated_time"] is not None and z["mitigated_time"] < ready:
            early = "  <-- used up BEFORE it was ready"
        print(f"  {tf} {z['type']:6} {z['bottom']}-{z['top']}  origin {et(z['origin_time'])}  "
              f"ready {et(ready)}  {status}{early}")
