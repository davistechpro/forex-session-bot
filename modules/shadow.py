"""
Shadow trend -- run the trend v2 fix side by side with the live bot (Nov 10).

On every scan the watcher records the exact candles the live scan used, then
re-runs the scan with modules/trend_v2.py on those same candles. Nothing is
fetched twice and nothing is posted to the trade chats: the live bot trades
exactly as before.

Each comparison is logged to status/shadow_YYYY-MM-DD.csv. When the shadow
would do something different (other direction, zone or trigger), Chad posts
one quiet line in the Chad chat, once per change per pair:

  SHADOW GBP/JPY: live BEARISH short (D bear / 4H bear / 1H bull)
                  fix  BULLISH -- no short (D BULL / 4H bear / 1H bull)
                  Daily: bullish, push demand 211.20-211.95 Wed Nov 04 ...

Turn off with SHADOW_TREND=off in .env.
`py shadow_report.py [DATE]` prints the day's differences next to the team's votes.
"""
import csv
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytz

import modules.scan_engine as se
from modules import notifier, status
from modules.trend_v2 import determine_trend_v2, explain

NY = pytz.timezone("America/New_York")
FIELDS = ["at_et", "pair",
          "live_d", "live_h4", "live_h1", "live_dir", "live_zone", "live_entry",
          "fix_d", "fix_h4", "fix_h1", "fix_dir", "fix_zone", "fix_entry",
          "a_only", "b_only", "different", "why_live", "why_fix"]
_frames = {}
_queue = []
_last_note = {}


def enabled() -> bool:
    return os.getenv("SHADOW_TREND", "on").strip().lower() not in ("off", "0", "false", "no")


@contextmanager
def recording():
    """Wrap one live run_scan: pass every candle request through untouched and
    keep a copy so the shadow can reuse the very same data."""
    global _frames
    _frames = {}
    real = se.get_candles

    def rec(pair, tf, count=200):
        df = real(pair, tf, count=count)
        _frames[(pair, tf)] = df
        return df

    se.get_candles = rec
    try:
        yield
    finally:
        se.get_candles = real


def _tr(r, k):
    return str((r.get(k) or {}).get("trend", "?"))


def _zone(r):
    z = r.get("zone")
    return f"{r.get('zone_source_tf')} {z['type']} {z['bottom']}-{z['top']}" if z else ""


def _entry(r):
    e = r.get("entry")
    return f"{e.get('method')} entry {e.get('entry_price')}" if e else (r.get("skip_reason") or "")


def queue(pair: str, live: dict):
    """Called right after the live scan: keep this pair's candles and result.
    The shadow work runs in flush(), after every live post of the pass has
    gone out, so it can never delay a real alert."""
    if enabled() and live and _frames:
        _queue.append((pair, live, dict(_frames)))


def flush():
    global _frames
    while _queue:
        pair, live, frames = _queue.pop(0)
        _frames = frames
        compare(pair, live)
    _frames = {}


def _direction(frames, pair, fixes):
    """2-of-3 direction from trend v2 alone (cheap: no zones/entries)."""
    t = [determine_trend_v2(frames[(pair, tf)].tail(n).reset_index(drop=True), tf, fixes)["trend"]
         for tf, n in (("D", 60), ("H4", 120), ("H1", 200))]
    return "bullish" if t.count("bullish") >= 2 else "bearish" if t.count("bearish") >= 2 else None


def _scan_with(pair, fixes):
    real_get, real_trend = se.get_candles, se.determine_trend
    se.get_candles = lambda p, tf, count=200: _frames[(p, tf)].tail(count).reset_index(drop=True)
    se.determine_trend = lambda c, tf="": determine_trend_v2(c, tf, fixes)
    try:
        return se.run_scan(pair, render_chart_image=False)
    finally:
        se.get_candles, se.determine_trend = real_get, real_trend


def compare(pair: str, live: dict):
    """Never raises: a shadow problem must not touch the live bot."""
    if not enabled() or not live or not _frames:
        return
    try:
        fix = _scan_with(pair, "AB")
        dirs = {f: _direction(_frames, pair, f) for f in ("A", "B")}
        different = (live.get("valid_direction") != fix.get("valid_direction")
                     or _zone(live) != _zone(fix) or _entry(live) != _entry(fix))
        why_live = " | ".join(f"{n}: {explain(live.get(k))}" for n, k in
                              (("D", "daily_trend"), ("4H", "h4_trend"), ("1H", "h1_trend")))
        why_fix = " | ".join(f"{n}: {explain(fix.get(k))}" for n, k in
                             (("D", "daily_trend"), ("4H", "h4_trend"), ("1H", "h1_trend")))
        row = {
            "at_et": datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S"), "pair": pair,
            "live_d": _tr(live, "daily_trend"), "live_h4": _tr(live, "h4_trend"), "live_h1": _tr(live, "h1_trend"),
            "live_dir": live.get("valid_direction") or "none", "live_zone": _zone(live), "live_entry": _entry(live),
            "fix_d": _tr(fix, "daily_trend"), "fix_h4": _tr(fix, "h4_trend"), "fix_h1": _tr(fix, "h1_trend"),
            "fix_dir": fix.get("valid_direction") or "none", "fix_zone": _zone(fix), "fix_entry": _entry(fix),
            "a_only": dirs["A"] or "none", "b_only": dirs["B"] or "none",
            "different": "YES" if different else "", "why_live": why_live, "why_fix": why_fix,
        }
        path = status.DIR / f"shadow_{datetime.now(NY):%Y-%m-%d}.csv"
        status.DIR.mkdir(exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        if different:
            _note(pair, live, fix)
    except Exception as e:
        print(f"  {pair}: shadow skipped ({type(e).__name__}: {e})")


def _note(pair, live, fix):
    sig = (live.get("valid_direction"), fix.get("valid_direction"), _zone(fix), _entry(fix))
    if _last_note.get(pair) == sig or not notifier.chad_chat_configured():
        return
    _last_note[pair] = sig

    def line(tag, r):
        d = r.get("valid_direction")
        side = {"bullish": "long", "bearish": "short"}.get(d, "no trade")
        t = "/".join(_tr(r, k)[:4] for k in ("daily_trend", "h4_trend", "h1_trend"))
        z = _zone(r) or "no zone"
        return f"{tag} {(d or 'none').upper()} {side} (D/4H/1H {t}) {z}; {_entry(r) or 'no trigger'}"

    changed = [n for n, k in (("Daily", "daily_trend"), ("4H", "h4_trend"), ("1H", "h1_trend"))
               if _tr(live, k) != _tr(fix, k)]
    why = "; ".join(f"{n}: {explain(fix.get(k))}" for n, k in
                    (("Daily", "daily_trend"), ("4H", "h4_trend"), ("1H", "h1_trend")) if n in changed)
    text = (f"SHADOW {pair.replace('_', '/')} {datetime.now(NY):%I:%M %p}\n"
            f"{line('live:', live)}\n{line('fix: ', fix)}"
            + (f"\nWhy: {why}" if why else ""))
    notifier.send_message(text, chat="chad", silent=True)
