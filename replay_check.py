"""
Replay check -- proof that a change did what we meant and nothing else.

Replays a fixed list of reference days (reference_days.json) through the
session every 15 minutes with the CURRENT code and records what the bot saw at
each step: trend per timeframe, direction, zone, trigger, entry/stop. Candles
are downloaded once per day and frozen in replay_data/, so "before" and
"after" always run on identical prices.

The routine for every TRADE-LOGIC change (see CHANGELOG.md):

  1. py replay_check.py --save before        (on the current code)
  2. make the change
  3. py replay_check.py --compare before     (lists every step that changed)

  If only the intended days/pairs changed -> log it and ship it.
  If anything else moved -> stop and look before going live.

Trend v2 (Nov 10, shadow until confirmed):
  py replay_check.py --trend v2              replay with the trend fix
  py replay_check.py --compare NAME --trend v2   fix vs a saved baseline
  py replay_check.py --why GBP_JPY 2026-11-09 09:00
                                             why each timeframe got its trend, every version
  py replay_check.py --score                 every trend version vs the team's calls, one table
  --trend b1|b2|b3|b4                        zone-break rule versions (Nov 11), see trend_v2.py

Reference days can carry the team's read ("expect"); every run ends with a
TEAM CHECK: how many of those the current logic gets right.

Other:
  py replay_check.py                         run and print, save nothing
  py replay_check.py --add GBP_CAD 2026-10-27 "Gage: wrong 4H zone"
  py replay_check.py --list
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()

import modules.data_feed as data_feed  # noqa: E402
import modules.scan_engine as se  # noqa: E402
from modules.trend import determine_trend as _live_trend  # noqa: E402
from modules.trend_v2 import determine_trend_v2, explain, trend_for, BREAK_MODES  # noqa: E402

NY = pytz.timezone("America/New_York")
ROOT = Path(__file__).resolve().parent
DAYS_FILE = ROOT / "reference_days.json"
DATA_DIR = ROOT / "replay_data"
SNAP_DIR = ROOT / "replay_snapshots"
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D": 1440}
COUNT = {"M1": 5000, "M5": 3000, "M15": 1500, "H1": 800, "H4": 400, "D": 200}

DEFAULT_DAYS = [
    {"pair": "GBP_CAD", "day": "2026-10-27", "why": "Gage: bot used the old Friday 4H zone; zone fix Oct 28"},
    {"pair": "GBP_JPY", "day": "2026-10-27", "why": "planned entry 210.207 outside the 4H/1H zone (open question)"},
    {"pair": "USD_JPY", "day": "2026-10-27", "why": "8-pip stop landed exactly on the 5m zone (open question)"},
    {"pair": "USD_JPY", "day": "2026-10-21", "why": "watcher froze; missed 10:24 short / 12:30 long"},
    {"pair": "EUR_USD", "day": "2026-10-29", "why": "posted PENDING short 1.11458 at 10:20"},
    {"pair": "EUR_JPY", "day": "2026-10-29", "why": "posted 1H wick-tap SETUP at 10:20"},
]


# ------------------------------------------------------------------ reference days

def load_days():
    if not DAYS_FILE.exists():
        DAYS_FILE.write_text(json.dumps(DEFAULT_DAYS, indent=1), encoding="utf-8")
    return json.loads(DAYS_FILE.read_text(encoding="utf-8"))


def add_day(pair, day, why):
    days = load_days()
    if any(d["pair"] == pair and d["day"] == day for d in days):
        print(f"{pair} {day} is already a reference day")
        return
    days.append({"pair": pair, "day": day, "why": why})
    DAYS_FILE.write_text(json.dumps(days, indent=1), encoding="utf-8")
    print(f"Added {pair} {day}. Run --save again so the next compare includes it.")


# ------------------------------------------------------------------ frozen candles

def _download(pair, tf, to_utc):
    """`count` completed candles ending at `to_utc`, from OANDA."""
    from oandapyV20.endpoints.instruments import InstrumentsCandles
    req = InstrumentsCandles(instrument=pair, params={
        "granularity": tf, "price": "M", "count": COUNT[tf],
        "to": to_utc.strftime("%Y-%m-%dT%H:%M:%SZ")})
    data_feed._get_client().request(req)
    rows = [{"time": pd.to_datetime(c["time"]), "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
             "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]), "volume": int(c.get("volume", 0))}
            for c in req.response["candles"] if c["complete"]]
    return pd.DataFrame(rows)


def frozen(pair, day, tf):
    """Candles for one pair/day/timeframe, downloaded once and kept forever."""
    path = DATA_DIR / f"{pair}_{day}_{tf}.csv"
    if path.exists():
        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df
    end = NY.localize(datetime.strptime(day, "%Y-%m-%d").replace(hour=13, minute=0)).astimezone(pytz.utc)
    df = _download(pair, tf, end)
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(path, index=False)
    return df


# ------------------------------------------------------------------ replay

def _et(ts):
    return pd.Timestamp(ts).tz_convert(NY).strftime("%a %I:%M %p") if ts is not None else "-"


def replay(pair, day):
    """{ "09:00": "fingerprint", ... } for one pair/day, current code."""
    now_t = {"t": None}
    cache = {}

    def fake_get(p, tf, count=200):
        if (p, tf) not in cache:
            cache[(p, tf)] = frozen(p, day, tf)
        df = cache[(p, tf)]
        closed = df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= now_t["t"]]
        return closed.tail(count).reset_index(drop=True)

    real = se.get_candles
    se.get_candles = fake_get
    out = {}
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        start = NY.localize(d.replace(hour=9, minute=0))
        why = se.trading_day_block_reason(pair, when=start)
        if why:
            return {"day": f"BLOCKED: {why}"}
        t = start
        while t <= NY.localize(d.replace(hour=12, minute=45)):
            now_t["t"] = pd.Timestamp(t).tz_convert("UTC")
            try:
                r = se.run_scan(pair, render_chart_image=False)
                tr = "/".join(str((r.get(k) or {}).get("trend", "?")) for k in ("daily_trend", "h4_trend", "h1_trend"))
                z = r.get("zone")
                zone = f"{r.get('zone_source_tf')} {z['type']} {z['bottom']}-{z['top']}" if z else "no zone"
                e = r.get("entry")
                if e:
                    trig = (f"TRIGGER {e.get('method')} {_et(e.get('time'))} entry {e.get('entry_price')} "
                            f"stop {e.get('stop_loss')} tp {e.get('take_profit')}")
                else:
                    trig = r.get("skip_reason") or "no trigger"
                out[t.strftime("%H:%M")] = f"D/4H/1H {tr} -> {r.get('valid_direction') or 'none'} | {zone} | {trig}"
            except Exception as ex:
                out[t.strftime("%H:%M")] = f"ERROR {type(ex).__name__}: {str(ex)[:120]}"
            t += timedelta(minutes=15)
    finally:
        se.get_candles = real
    return out


def use_trend(mode: str):
    """'live' = modules/trend.py, 'v2'/'A'/'B' = push fixes, 'b1'-'b4' = zone-break rule."""
    se.determine_trend = _live_trend if mode == "live" else trend_for(mode)


MODES = ["live", "v2", "b1", "b2", "b3", "b4"]
MODE_NAMES = {"live": "live bot (zone formed)", "v2": "push fixes A+B",
              "b1": "break, any zone", "b2": "break, any zone + rejections",
              "b3": "break, most recent zone", "b4": "break, most recent zone + rejections"}


def _trends_at(pair, day, hhmm, fn):
    t = NY.localize(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M"))
    now_utc = pd.Timestamp(t).tz_convert("UTC")
    out = {}
    for tf, label, n in (("D", "D", 60), ("H4", "4H", 120), ("H1", "1H", 200)):
        df = frozen(pair, day, tf)
        df = df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= now_utc].tail(n).reset_index(drop=True)
        out[label] = fn(df, tf)
    return out


def score_all():
    """One table: every trend version vs the team's calls on the reference days."""
    days = [d for d in load_days() if d.get("expect")]
    if not days:
        raise SystemExit("No reference days with the team's read (\"expect\") yet.")
    totals = {m: [0, 0] for m in MODES}
    print("Team's calls vs each trend version (OK = matches the team)\n")
    for d in days:
        e = d["expect"]
        print(f"{d['pair']} {d['day']} {e['time']}   team: " +
              ", ".join(f"{tf} {v}" for tf, v in e["trends"].items()))
        for m in MODES:
            fn = _live_trend if m == "live" else trend_for(m)
            got = _trends_at(d["pair"], d["day"], e["time"], fn)
            marks = []
            for tf, want in e["trends"].items():
                g = got[tf]["trend"]
                totals[m][1] += 1
                totals[m][0] += g == want
                marks.append(f"{tf} {g}{'' if g == want else ' X'}")
            print(f"   {m:4} {', '.join(marks)}")
        print()
    print("SCORE")
    for m in MODES:
        h, n = totals[m]
        print(f"   {m:4} {h:2} of {n}   {MODE_NAMES[m]}")


def run_all():
    res = {}
    for d in load_days():
        key = f"{d['pair']} {d['day']}"
        print(f"replaying {key} ...", file=sys.stderr)
        res[key] = replay(d["pair"], d["day"])
    return res


TF_IDX = {"D": 0, "4H": 1, "1H": 2}


def team_check(res):
    """Score the replay against the team's read on the days that have one."""
    days = [d for d in load_days() if d.get("expect")]
    if not days:
        return
    hit = total = 0
    print("\nTEAM CHECK (does the bot's trend match what the team said?)")
    for d in days:
        key = f"{d['pair']} {d['day']}"
        e = d["expect"]
        step = (res.get(key) or {}).get(e["time"], "")
        if not step.startswith("D/4H/1H "):
            print(f"  {key} {e['time']}: no data ({step[:60]})")
            continue
        trends = step.split()[1].split("/")
        marks = []
        for tf, want in e["trends"].items():
            got = trends[TF_IDX[tf]]
            total += 1
            ok = got == want
            hit += ok
            marks.append(f"{tf} {got}{' OK' if ok else ' MISS (team: ' + want + ')'}")
        print(f"  {key} {e['time']}: " + ", ".join(marks))
    print(f"  -> {hit} of {total} timeframe calls match the team")


def why(pair, day, hhmm):
    """trend_why: for one moment, what decided each timeframe, live vs fix."""
    t = NY.localize(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M"))
    now_utc = pd.Timestamp(t).tz_convert("UTC")
    counts = {"D": 60, "H4": 120, "H1": 200}
    print(f"{pair} at {day} {hhmm} ET (closed candles only)\n")
    for tf, label in (("D", "Daily"), ("H4", "4H"), ("H1", "1H")):
        df = frozen(pair, day, tf)
        df = df[df["time"] + pd.Timedelta(minutes=TF_MIN[tf]) <= now_utc].tail(counts[tf]).reset_index(drop=True)
        last = df["close"].iloc[-1] if len(df) else None
        live = _live_trend(df, tf)
        print(f"{label:5}  price {last}")
        print(f"   live : {explain(live)}")
        for m in ("v2", "b1", "b2", "b3", "b4"):
            v = trend_for(m)(df, tf)
            flag = "" if v["trend"] == live["trend"] else "   <-- CHANGED"
            print(f"   {m:4} : {explain(v)}{flag}")
        print()


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", metavar="NAME")
    ap.add_argument("--compare", metavar="NAME")
    ap.add_argument("--add", nargs=3, metavar=("PAIR", "DAY", "WHY"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--trend", default="live", choices=["live", "v2", "A", "B", "b1", "b2", "b3", "b4"])
    ap.add_argument("--score", action="store_true", help="score every trend version against the team's calls")
    ap.add_argument("--why", nargs=3, metavar=("PAIR", "DAY", "HH:MM"))
    a = ap.parse_args()

    if a.why:
        why(a.why[0].upper(), a.why[1], a.why[2])
        return
    if a.score:
        score_all()
        return
    use_trend(a.trend)
    if a.trend != "live":
        print(f"(replaying with trend {a.trend})", file=sys.stderr)

    if a.add:
        add_day(a.add[0].upper(), a.add[1], a.add[2])
        return
    if a.list:
        for d in load_days():
            print(f"{d['pair']} {d['day']}  -- {d['why']}")
        return

    res = run_all()

    if a.save:
        SNAP_DIR.mkdir(exist_ok=True)
        path = SNAP_DIR / f"{a.save}.json"
        path.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"Saved {len(res)} reference days to {path}")
        team_check(res)
        return

    if a.compare:
        path = SNAP_DIR / f"{a.compare}.json"
        if not path.exists():
            raise SystemExit(f"STOP -- no snapshot named '{a.compare}'. Run --save {a.compare} on the old code first.")
        old = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for key, steps in res.items():
            before = old.get(key)
            if before is None:
                print(f"\n{key}: NEW reference day (not in '{a.compare}')")
                continue
            diffs = [(t, before.get(t), s) for t, s in steps.items() if before.get(t) != s]
            if not diffs:
                print(f"{key}: no change")
                continue
            changed += 1
            print(f"\n{key}: {len(diffs)} step(s) changed")
            for t, b, s in diffs:
                print(f"  {t}  BEFORE {b}\n         AFTER  {s}")
        print(f"\n{changed} of {len(res)} reference days changed.")
        team_check(res)
        return

    for key, steps in res.items():
        print(f"\n{key}")
        last = None
        for t, s in steps.items():
            if s != last:
                print(f"  {t}  {s}")
                last = s
    team_check(res)


if __name__ == "__main__":
    main()
