"""
Watcher — scans all pairs and alerts via Telegram when a NEW trade
signal appears. Sends the chart with the alert as its caption, with
Valid/Invalid vote buttons attached.

  python watch.py                  # loop forever, 5-minute interval
  python watch.py --once           # single pass (for cron)
  python watch.py --dry-run        # print instead of sending
  python watch.py --ignore-session # scan outside 9am-1pm ET too
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

from modules.scan_engine import run_scan, is_ny_session_now
from modules import notifier

NY_TZ = pytz.timezone("America/New_York")
STATE_FILE = Path("alert_state.json")
DRY_STATE_FILE = Path("alert_state_dryrun.json")

PAIRS = ["EUR_USD", "EUR_JPY", "GBP_CAD", "USD_JPY", "USD_CAD", "GBP_USD", "GBP_JPY"]


def _today_key() -> str:
    return datetime.now(NY_TZ).strftime("%Y-%m-%d")


def load_state(dry_run: bool = False) -> dict:
    path = DRY_STATE_FILE if dry_run else STATE_FILE
    if not path.exists():
        return {"day": _today_key(), "sent": []}
    try:
        state = json.loads(path.read_text())
    except Exception:
        return {"day": _today_key(), "sent": []}
    if state.get("day") != _today_key():
        return {"day": _today_key(), "sent": []}
    return state


def save_state(state: dict, dry_run: bool = False):
    path = DRY_STATE_FILE if dry_run else STATE_FILE
    try:
        path.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"WARN: could not write state file: {e}")


def signal_id(pair: str, result: dict) -> str:
    entry = result["entry"]
    zone = result["zone"]
    # Key on the ZONE only -- pair, direction and which zone fired. A
    # later re-trigger on the same zone (different refinement level or
    # entry price) is the SAME setup and must not alert twice.
    return "|".join([
        pair,
        entry["direction"],
        str(zone["origin_time"]),
        f"{zone['bottom']:.5f}-{zone['top']:.5f}",
    ])


def scan_pass(pairs, state, dry_run=False) -> int:
    sent_count = 0
    now_et = datetime.now(NY_TZ).strftime("%I:%M:%S %p ET")
    print(f"\n[{now_et}] scanning {len(pairs)} pairs...")

    for pair in pairs:
        try:
            result = run_scan(pair, render_chart_image=False)
        except Exception as e:
            print(f"  {pair}: scan failed -- {e}")
            continue

        if not result.get("entry"):
            direction = result.get("valid_direction") or "no direction"
            print(f"  {pair}: {direction} -- no entry")
            continue

        sid = signal_id(pair, result)
        if sid in state["sent"]:
            print(f"  {pair}: signal already alerted -- skipping")
            continue

        text = notifier.format_trade_alert(pair, result)
        print(f"  {pair}: NEW SIGNAL -> {result['entry']['direction']} @ {result['entry']['entry_price']}")

        if dry_run:
            print("  --- dry run, not sending ---")
            print(text)
            state["sent"].append(sid)
            sent_count += 1
            continue

        # Only render a chart for pairs that actually signalled, so the
        # sweep stays fast for the ones that do not.
        chart = None
        try:
            full = run_scan(pair, render_chart_image=True)
            chart = full.get("chart_path_1h") or full.get("chart_path")
        except Exception as ex:
            print(f"  {pair}: chart render failed ({ex}) -- sending text only")

        e, z = result["entry"], result["zone"]
        notifier.remember_signal(notifier.short_token(sid), {
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

        ok = (notifier.send_photo(chart, text, signal_id=sid) if chart
              else notifier.send_message(text, signal_id=sid))

        if ok:
            state["sent"].append(sid)
            save_state(state, dry_run=False)
            sent_count += 1
            print("  alert sent" + (" with chart" if chart else " (text only)"))
        else:
            print("  alert FAILED to send -- will retry next pass")

    return sent_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--ignore-session", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pairs", type=str, default="")
    args = ap.parse_args()

    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()] or PAIRS

    if not args.dry_run and not notifier.is_configured():
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from .env")
        print("Run with --dry-run to test without sending.")
        sys.exit(1)

    print(f"Watcher starting. Pairs: {', '.join(pairs)}")
    print(f"Mode: {'single pass' if args.once else f'loop every {args.interval}s'}")
    print(f"Session filter: {'OFF' if args.ignore_session else 'ON (9am-1pm ET only)'}")

    while True:
        session = is_ny_session_now()
        if session["active"] or args.ignore_session:
            state = load_state(dry_run=args.dry_run)
            n = scan_pass(pairs, state, dry_run=args.dry_run)
            save_state(state, dry_run=args.dry_run)
            if n:
                print(f"  -> {n} new alert(s) this pass")
        else:
            print(f"[{session['current_time_et']}] outside 9am-1pm ET session -- idle")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

