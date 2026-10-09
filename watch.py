"""
Watcher -- scans all pairs and posts to TWO Telegram chats:

  SETUPS chat  -> trade ideas: a 4H/1H zone qualifies but has not
                  triggered yet. Valid/Invalid buttons, reason required.
                  Posted 9:00 AM - 12:59 PM ET.
  TRADES chat  -> ACTIVE trades only: the trigger fired AND price has
                  actually reached the entry price. A trade whose trigger
                  fired but whose entry hasn't been hit yet posts to the
                  SETUPS chat as "pending" and moves to TRADES once filled.
                  (Team, Oct 8: the trades chat is active trades.)
                  Trigger = wick tap / 1m
                  confirmation done). Chart + Valid/Invalid buttons.
                  A vote only counts once the voter replies with why.
                  Posted 9:00 AM - 12:59 PM ET only.

  python watch.py                  # loop forever, 5-minute interval
  python watch.py --once           # single pass
  python watch.py --dry-run        # print instead of sending
  python watch.py --ignore-session # scan outside 6am-1pm ET too
  python watch.py --ignore-calendar  # scan on Fridays / holidays too (testing)
  python watch.py --no-setups      # trades chat only
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

import pandas as pd

from modules.data_feed import get_candles
from modules.scan_engine import run_scan, is_ny_session_now, trading_day_block_reason
from modules.setup_planner import build_setup
from modules import notifier

NY_TZ = pytz.timezone("America/New_York")
STATE_FILE = Path("alert_state.json")
DRY_STATE_FILE = Path("alert_state_dryrun.json")

PAIRS = ["EUR_USD", "EUR_JPY", "GBP_CAD", "USD_JPY", "USD_CAD", "GBP_USD", "GBP_JPY"]

SETUPS_START = (9, 0)     # team, Oct 8: nothing posts before 9:00 AM ET
SESSION_END = (12, 59)    # doc 1: NY session ends 12:59 PM ET


def _today_key() -> str:
    return datetime.now(NY_TZ).strftime("%Y-%m-%d")


def _fresh_state() -> dict:
    return {"day": _today_key(), "sent": [], "setups_sent": []}


def load_state(dry_run: bool = False) -> dict:
    path = DRY_STATE_FILE if dry_run else STATE_FILE
    if not path.exists():
        return _fresh_state()
    try:
        state = json.loads(path.read_text())
    except Exception:
        return _fresh_state()
    if state.get("day") != _today_key():
        return _fresh_state()
    state.setdefault("sent", [])
    state.setdefault("setups_sent", [])
    return state


def save_state(state: dict, dry_run: bool = False):
    path = DRY_STATE_FILE if dry_run else STATE_FILE
    try:
        path.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"WARN: could not write state file: {e}")


def _zone_key(pair: str, direction: str, zone: dict) -> str:
    return "|".join([
        pair, direction, str(zone["origin_time"]),
        f"{zone['bottom']:.5f}-{zone['top']:.5f}",
    ])


def signal_id(pair: str, result: dict) -> str:
    # Keyed on the ZONE only -- a re-trigger on the same zone is the same trade.
    return _zone_key(pair, result["entry"]["direction"], result["zone"])


def setup_id(pair: str, result: dict) -> str:
    direction = "LONG" if result["valid_direction"] == "bullish" else "SHORT"
    return "SETUP|" + _zone_key(pair, direction, result["zone"])


def pending_id(pair: str, result: dict) -> str:
    return "PENDING|" + signal_id(pair, result)


def entry_filled(pair: str, entry: dict):
    """
    Has price actually reached the entry since the trigger? Checks 1m
    candles from the trigger candle onward. LONG fills when a low touches
    the entry; SHORT when a high does. Returns the fill time or None.
    """
    m1 = get_candles(pair, "M1", count=500)
    if len(m1) == 0:
        return None
    trig = pd.Timestamp(entry["time"])
    after = m1[m1["time"] >= trig]
    px = entry["entry_price"]
    hit = after[after["low"] <= px] if entry["direction"] == "LONG" else after[after["high"] >= px]
    return None if hit.empty else hit["time"].iloc[0]


def post_pending(pair, result, state, dry_run, send_setups):
    """Trigger fired but entry not reached yet -> setups chat, once."""
    sid = pending_id(pair, result)
    if sid in state["setups_sent"]:
        print(f"  {pair}: pending entry already posted -- waiting for fill")
        return 0
    e = result["entry"]
    lvl = notifier.LVL_NAMES.get(e.get("entry_level", ""), e.get("entry_level", ""))
    trig_et = pd.Timestamp(e["time"]).tz_convert(NY_TZ).strftime("%I:%M %p ET")
    result["setup"] = {
        "kind": "pending_entry",
        "waiting_for": f"price to reach the entry ({lvl} zone). Triggered {trig_et} ({e.get('method', '')})",
        "formed": pd.Timestamp(result["zone"]["origin_time"]).tz_convert(NY_TZ).strftime("%a %b %d %I:%M %p ET"),
        "planned_entry": e["entry_price"],
        "planned_level": e.get("entry_level", ""),
        "planned_sl": e["stop_loss"],
        "planned_sl_pips": e.get("sl_pips", 8),
        "planned_tp": e["take_profit"],
        "note": "PENDING -- posts to the trades chat as ACTIVE once price hits the entry.",
    }
    text = notifier.format_setup_alert(pair, result)
    print(f"  {pair}: PENDING -> {e['direction']} entry {e['entry_price']} not hit yet")
    if dry_run:
        print("  --- dry run, not sending ---")
        print(text)
        state["setups_sent"].append(sid)
        return 1
    if not send_setups:
        return 0
    z = result["zone"]
    notifier.remember_signal(notifier.short_token(sid), {
        "post_type": "pending",
        "pair": pair,
        "direction": e["direction"],
        "entry_price": e["entry_price"],
        "entry_level": e.get("entry_level", ""),
        "sl_pips": e.get("sl_pips", ""),
        "method": e.get("method", ""),
        "trend": result.get("deciding_tf", ""),
        "zone": f"{z['type'].upper()} {z['bottom']}-{z['top']} ({result['zone_source_tf']})",
        "triggered_at": str(e.get("time", "")),
    })
    chart = _chart_for(pair)
    ok = (notifier.send_photo(chart, text, signal_id=sid, chat="setups") if chart
          else notifier.send_message(text, signal_id=sid, chat="setups"))
    if ok:
        state["setups_sent"].append(sid)
        save_state(state)
        print("  pending posted" + (" with chart" if chart else " (text only)"))
        return 1
    print("  pending FAILED to send -- will retry next pass")
    return 0


def window_now() -> dict:
    now = datetime.now(NY_TZ)
    hm = (now.hour, now.minute)
    return {
        "setups": SETUPS_START <= hm <= SESSION_END,
        "trades": is_ny_session_now()["active"],
        "label": now.strftime("%I:%M %p ET"),
    }


def _chart_for(pair: str):
    try:
        full = run_scan(pair, render_chart_image=True)
        return full.get("chart_path_1h") or full.get("chart_path")
    except Exception as ex:
        print(f"  {pair}: chart render failed ({ex}) -- sending text only")
        return None


def post_setup(pair, result, state, dry_run, send_setups):
    sid = setup_id(pair, result)
    if sid in state["setups_sent"]:
        print(f"  {pair}: setup already posted")
        return 0
    try:
        result["setup"] = build_setup(pair, result)
    except Exception as e:
        print(f"  {pair}: setup planning failed -- {e}")
        return 0
    if not result["setup"]:
        return 0

    text = notifier.format_setup_alert(pair, result)
    print(f"  {pair}: NEW SETUP -> {result['setup']['waiting_for']}")
    if dry_run:
        print("  --- dry run, not sending ---")
        print(text)
        state["setups_sent"].append(sid)
        return 1
    if not send_setups:
        return 0

    chart = _chart_for(pair)
    s, z = result["setup"], result["zone"]
    notifier.remember_signal(notifier.short_token(sid), {
        "post_type": "setup",
        "pair": pair,
        "direction": "LONG" if result["valid_direction"] == "bullish" else "SHORT",
        "entry_price": s.get("planned_entry") if s.get("planned_entry") is not None else "",
        "entry_level": s.get("planned_level", ""),
        "sl_pips": s.get("planned_sl_pips", ""),
        "method": f"setup ({s.get('kind', '')})",
        "trend": result.get("deciding_tf", ""),
        "zone": f"{z['type'].upper()} {z['bottom']}-{z['top']} ({result['zone_source_tf']})",
        "triggered_at": "",
    })
    ok = (notifier.send_photo(chart, text, signal_id=sid, chat="setups") if chart
          else notifier.send_message(text, signal_id=sid, chat="setups"))
    if ok:
        state["setups_sent"].append(sid)
        save_state(state)
        print("  setup posted" + (" with chart" if chart else " (text only)"))
        return 1
    print("  setup FAILED to send -- will retry next pass")
    return 0


def post_trade(pair, result, state, dry_run):
    sid = signal_id(pair, result)
    if sid in state["sent"]:
        print(f"  {pair}: trade already alerted -- skipping")
        return 0

    text = notifier.format_trade_alert(pair, result)
    print(f"  {pair}: NEW TRADE -> {result['entry']['direction']} @ {result['entry']['entry_price']}")
    if dry_run:
        print("  --- dry run, not sending ---")
        print(text)
        state["sent"].append(sid)
        return 1

    chart = _chart_for(pair)
    e, z = result["entry"], result["zone"]
    notifier.remember_signal(notifier.short_token(sid), {
        "post_type": "trade",
        "pair": pair,
        "direction": e["direction"],
        "entry_price": e["entry_price"],
        "entry_level": e.get("entry_level", ""),
        "sl_pips": e.get("sl_pips", ""),
        "method": e.get("method", ""),
        "trend": result.get("deciding_tf", ""),
        "zone": f"{z['type'].upper()} {z['bottom']}-{z['top']} ({result['zone_source_tf']})",
        "triggered_at": str(e.get("time", "")),
        "filled_at": str(e.get("filled_at", "")),
    })

    ok = (notifier.send_photo(chart, text, signal_id=sid, chat="trades") if chart
          else notifier.send_message(text, signal_id=sid, chat="trades"))
    if ok:
        state["sent"].append(sid)
        save_state(state)
        print("  trade alert sent" + (" with chart" if chart else " (text only)"))
        return 1
    print("  trade alert FAILED to send -- will retry next pass")
    return 0


def scan_pass(pairs, state, win, dry_run=False, ignore_calendar=False, send_setups=True) -> int:
    count = 0
    print(f"\n[{win['label']}] scanning {len(pairs)} pairs "
          f"(setups {'on' if win['setups'] else 'off'}, trades {'on' if win['trades'] else 'off'})...")

    for pair in pairs:
        if not ignore_calendar:
            why = trading_day_block_reason(pair)
            if why:
                print(f"  {pair}: no trading today -- {why}")
                continue

        try:
            result = run_scan(pair, render_chart_image=False)
        except Exception as e:
            print(f"  {pair}: scan failed -- {e}")
            continue

        if result.get("entry"):
            if not win["trades"]:
                print(f"  {pair}: entry found but outside 9:00 AM - 12:59 PM ET -- held")
                continue
            if signal_id(pair, result) in state["sent"]:
                print(f"  {pair}: trade already alerted -- skipping")
                continue
            try:
                filled = entry_filled(pair, result["entry"])
            except Exception as ex:
                print(f"  {pair}: fill check failed ({ex}) -- treating as pending")
                filled = None
            if filled is not None:
                result["entry"]["filled_at"] = filled
                count += post_trade(pair, result, state, dry_run)
            else:
                count += post_pending(pair, result, state, dry_run, send_setups)
            continue

        if result.get("zone") and win["setups"]:
            count += post_setup(pair, result, state, dry_run, send_setups)
            continue

        direction = result.get("valid_direction") or "no direction"
        skip = result.get("skip_reason")
        print(f"  {pair}: {direction} -- nothing qualifying" + (f" ({skip})" if skip else ""))

    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--ignore-session", action="store_true")
    ap.add_argument("--ignore-calendar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-setups", action="store_true")
    ap.add_argument("--pairs", type=str, default="")
    args = ap.parse_args()

    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()] or PAIRS

    if not args.dry_run and not notifier.is_configured():
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_TRADES_CHAT_ID missing from .env")
        print("Run with --dry-run to test without sending.")
        sys.exit(1)

    send_setups = not args.no_setups and (args.dry_run or notifier.setups_configured())
    if not args.no_setups and not send_setups:
        print("NOTE: TELEGRAM_SETUPS_CHAT_ID not set -- setups will not be posted.")

    print(f"Watcher starting. Pairs: {', '.join(pairs)}")
    print(f"Mode: {'single pass' if args.once else f'loop every {args.interval}s'}")
    print(f"Session filter: {'OFF' if args.ignore_session else 'ON (setups and trades 9am-12:59pm ET)'}")
    print(f"Calendar filter: {'OFF' if args.ignore_calendar else 'ON (no Fridays / bank holidays)'}")

    while True:
        win = window_now()
        if args.ignore_session:
            win["setups"] = win["trades"] = True
        if win["setups"] or win["trades"]:
            state = load_state(dry_run=args.dry_run)
            n = scan_pass(pairs, state, win, dry_run=args.dry_run,
                          ignore_calendar=args.ignore_calendar, send_setups=send_setups)
            save_state(state, dry_run=args.dry_run)
            if n:
                print(f"  -> {n} new post(s) this pass")
        else:
            print(f"[{win['label']}] outside 9am-1pm ET -- idle")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
