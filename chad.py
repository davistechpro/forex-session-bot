"""
Chad -- operations. Run it in its own window next to watch.py and
vote_listener.py:

    py chad.py

On trading days (Mon-Thu, no bank holidays) Chad posts to BOTH chats (setups + trades):
  8:45 AM   Morning check: today's red-news events for our 7 pairs and where
            oil is (plus a PROBLEM line only if a bot window isn't running).
  9:00-1:00 Watches the bot. Alerts if the watcher stops scanning, the vote
            listener stops polling, or anything throws an error.
            15 minutes before each red-news event: a heads-up.
  9, 10, 11 AM, 12 PM  Hourly update (Gage's spec, Oct 22): oil and how it
            may be moving our currencies, today's trades and where each one
            stands (waiting, open +/- pips, stop hit, target hit), and why
            the other pairs have no trade.
  Mon 8:50  Weekly oil report: how oil is affecting each of our pairs
            (also by hand: py chad.py --now oilreport).
  1:00 PM   Recap of the day, plus the scorecard (votes toward 300, bot
            accuracy, Chad's agreement with the team).

Replies to Chad's messages are saved as team feedback (chad_feedback.csv)
and he reads the latest ones before every review, answer and update.

Questions in the chat ("Chad, why didn't GJ post?") are answered by the
vote listener, since it's the process that reads the chat.

  py chad.py --now morning     run one job right now (morning / update / summary / check / oilreport)
  py chad.py --dry-run         print instead of posting
"""
import argparse
import html
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

from modules import heartbeat, news, notifier, scorecard, status  # noqa: E402
from modules.scan_engine import trading_day_block_reason  # noqa: E402

NY = pytz.timezone("America/New_York")
PAIRS = news.PAIRS
STATE = status.DIR / "chad_state.json"
SIGN = "\n\n-- Chad"
WATCH_STALE_MIN = 12      # watcher scans every 5 min
LISTENER_STALE_MIN = 5    # listener polls every ~10 s
REALERT_MIN = 30
# Gage, Oct 22: an update every hour from 9 AM, and a recap at 1 PM.
# 9 AM waits a few minutes so the first scan has finished.
UPDATE_SLOTS = [((9, 7), "9 AM update"), ((10, 0), "10 AM update"), ((11, 0), "11 AM update"),
                ((12, 0), "12 PM update"), ((13, 0), "1 PM recap")]

DRY = False


# ------------------------------------------------------------------ helpers

def now():
    return datetime.now(NY)


def is_trading_day(t=None) -> bool:
    t = t or now()
    return any(trading_day_block_reason(p, when=t) is None for p in PAIRS)


def load_state() -> dict:
    day = now().strftime("%Y-%m-%d")
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    if s.get("day") != day:
        s = {"day": day, "done": [], "alerts": {}, "errors_seen": 0, "news_warned": []}
    return s


def save_state(s: dict):
    status.DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


BOTH = ("setups", "trades")


def post(text: str, chats=BOTH, loud: bool = False):
    """Oct 28 call (Gage, Steven, Devin): Chad was too noisy. Routine posts go out
    silently (no phone buzz); only real alerts are loud. Hourly updates go to the
    setups chat only; the morning note and the 1 PM recap still go to both."""
    body = html.escape(text, quote=False) + SIGN
    if DRY:
        print(f"---- would post to {', '.join(chats)}{' (loud)' if loud else ''} ----\n"
              + body + "\n--------------------")
        return
    for chat in chats:
        if not notifier.send_message(body, chat=chat, silent=not loud):
            print(f"CHAD: couldn't post to the {chat} chat")
            status.error("chad", f"couldn't post to the {chat} chat")


def age_min(name: str):
    ts, data = status.last_beat(name)
    if ts is None:
        return None, data
    return (now() - ts).total_seconds() / 60, data


def health_lines():
    """[(ok, text)] for the watcher and the listener."""
    out = []
    a, d = age_min("watch")
    if a is None:
        out.append((False, "Watcher: no sign of it yet (watch.py not started on the new code?)"))
    elif a > WATCH_STALE_MIN:
        out.append((False, f"Watcher: last scan {a:.0f} min ago ({d.get('label', '')}). It looks stopped."))
    else:
        out.append((True, f"Watcher: running (last scan {d.get('label', '')})"))
    a, d = age_min("listener")
    if a is None:
        out.append((False, "Vote listener: no sign of it yet (vote_listener.py not started on the new code?)"))
    elif a > LISTENER_STALE_MIN:
        out.append((False, f"Vote listener: last poll {a:.0f} min ago. It looks stopped, so votes won't count."))
    else:
        out.append((True, "Vote listener: running"))
    return out


# ------------------------------------------------------------------ jobs

def job_morning(s):
    # Devin, Oct 22: no "bot is running" lines -- the healthchecks alerts cover that.
    # Only speak up here if something is actually wrong.
    lines = [f"Morning check, {now():%A %b %d}."]
    for ok, txt in health_lines():
        if not ok:
            lines.append("PROBLEM  " + txt)
    try:
        from modules import trade_tracker
        lines.append("\nOil: " + trade_tracker.oil_text())
    except Exception:
        pass
    evs, err = news.red_events()
    if err:
        lines.append(f"\nRed news: {err}. Check the calendar manually.")
    elif evs:
        lines.append("\nRed news today for our pairs:")
        lines += [news.line(e) for e in evs]
    else:
        lines.append("\nNo red news today for our pairs.")
    blocked = [(p, trading_day_block_reason(p)) for p in PAIRS]
    blocked = [f"{p.replace('_', '/')} ({why})" for p, why in blocked if why]
    if blocked:
        lines.append("\nNot trading today: " + ", ".join(blocked))
    post("\n".join(lines))
    s["news"] = [{"t": e["time"].isoformat(), "line": news.line(e)} for e in evs]


def _alert_once(s, key, text):
    last = s["alerts"].get(key)
    if last and (now() - datetime.fromisoformat(last)) < timedelta(minutes=REALERT_MIN):
        return
    s["alerts"][key] = now().isoformat(timespec="seconds")
    post(text, loud=True)


def job_check(s):
    for ok, txt in health_lines():
        key = txt.split(":")[0]
        if not ok:
            _alert_once(s, key, "Heads up: " + txt)
        elif key in s["alerts"]:
            s["alerts"].pop(key)
            post(f"Back to normal: {txt}", loud=True)
    errs = status.errors_today()
    new = errs[s.get("errors_seen", 0):]
    if new:
        s["errors_seen"] = len(errs)
        shown = new[-5:]
        lines = [f"{len(new)} new error(s) from the bot:"]
        lines += [f"{e['ts'][11:16]} {e['source']}: {e['msg'][:200]}" for e in shown]
        post("\n".join(lines), loud=True)
    # red-news heads-up 15 min before
    for ev in s.get("news", []):
        t = datetime.fromisoformat(ev["t"])
        mins = (t - now()).total_seconds() / 60
        if 0 < mins <= 15 and ev["t"] not in s["news_warned"]:
            s["news_warned"].append(ev["t"])
            post(f"Red news in {mins:.0f} min: {ev['line']}", loud=True)


def _posts_today():
    try:
        st = json.loads(Path("alert_state.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    if st.get("day") != now().strftime("%Y-%m-%d"):
        return []
    out = []
    for sid in st.get("setups_sent", []) + st.get("sent", []):
        parts = sid.split("|")
        if parts[0] in ("SETUP", "PENDING"):
            kind, pair, direction = parts[0], parts[1], parts[2]
        else:
            kind, pair, direction = "TRADE (active)", parts[0], parts[1]
        out.append(f"{kind} {pair.replace('_', '/')} {direction}")
    return out


def _chad_verdicts_today():
    import csv
    path = Path("agent_reviews.csv")
    if not path.exists():
        return {}
    day = now().strftime("%Y-%m-%d")
    counts = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("reviewed_at") or "").startswith(day):
                counts[r.get("verdict", "?")] = counts.get(r.get("verdict", "?"), 0) + 1
    return counts


def _update_data(s, kind: str) -> str:
    from modules import trade_tracker
    t = now()
    day = t.strftime("%Y-%m-%d")
    parts = [f"TIME: {t:%A %b %d, %I:%M %p} ET ({kind})"]
    from modules.scan_engine import TEST_FRIDAYS
    if t.strftime("%Y-%m-%d") in TEST_FRIDAYS:
        parts.append("NOTE: today is a one-off TEST FRIDAY the team approved. Trading as normal today; Fridays stay off after this.")
    parts.append("OIL: " + trade_tracker.oil_text())
    parts.append("PAIR MOVES: " + trade_tracker.pair_moves_text(PAIRS))
    parts.append("TRADES TODAY:\n" + trade_tracker.trades_text(trade_tracker.trades_report()))
    posts = _posts_today()
    parts.append("POSTED TODAY: " + ("; ".join(posts) if posts else "nothing"))
    pairs = status.pairs_today()
    parts.append("WHAT EACH PAIR DID TODAY (latest, then changes):")
    for p in PAIRS:
        if p in pairs:
            parts.append(f"{p}: latest {pairs[p]['time']}: {pairs[p]['outcome']}")
            parts += [f"   {h}" for h in pairs[p].get("history", [])[-6:]]
        else:
            why = trading_day_block_reason(p)
            parts.append(f"{p}: " + (f"not trading today ({why})" if why else "no scan recorded yet"))
    soon = []
    for ev in s.get("news", []):
        mins = (datetime.fromisoformat(ev["t"]) - t).total_seconds() / 60
        if 0 < mins <= 75:
            soon.append(ev["line"])
    parts.append("RED NEWS IN THE NEXT HOUR: " + ("; ".join(soon) if soon else "none"))
    v = _chad_verdicts_today()
    if v:
        parts.append("YOUR REVIEWS TODAY: " + ", ".join(f"{k} {n}" for k, n in sorted(v.items())))
    errs = status.errors_today()
    if errs:
        parts.append(f"ERRORS TODAY: {len(errs)}; last: {errs[-1]['source']}: {errs[-1]['msg'][:150]}")
    for ok, txt in health_lines():
        if not ok:
            parts.append("PROBLEM: " + txt)
    if "recap" in kind:
        parts.append("SCORECARD: " + scorecard.text(day).replace("\n", " "))
    return "\n".join(parts)


def job_update(s, kind: str = "hourly update"):
    """Gage's spec: oil, today's trades and their status, why the other pairs
    have no trade, red news coming up. 'recap' covers the whole day + scorecard."""
    data = _update_data(s, kind)
    try:
        from modules import agent_reviewer
        if not agent_reviewer.enabled():
            raise RuntimeError("agent not configured")
        text = agent_reviewer.compose(kind, data)
        if not text:
            raise RuntimeError("empty reply")
    except Exception as e:
        print(f"CHAD: couldn't write the {kind} ({e}) -- posting the raw data")
        status.error("chad", f"couldn't write the {kind}: {e}")
        text = f"{kind.capitalize()} (raw data, I couldn't write it up):\n" + data
    # hourly updates: setups chat only; the 1 PM recap: both chats
    post(text, chats=BOTH if kind == "recap" else ("setups",))


def job_summary(s):
    job_update(s, "recap")


def job_oil_report(s):
    """Gage, Oct 22: a high-level report of how oil is affecting the pairs we trade.
    Weekly (Monday 8:50 AM) or by hand: py chad.py --now oilreport"""
    from modules import agent_reviewer, trade_tracker
    data = f"TIME: {now():%A %b %d, %I:%M %p} ET\n" + trade_tracker.oil_week_data(PAIRS)
    try:
        text = agent_reviewer.compose("weekly oil report", data)
    except Exception as e:
        status.error("chad", f"couldn't write the oil report: {e}")
        text = "Weekly oil report (raw data, I couldn't write it up):\n" + data
    post(text)


JOBS = {"morning": job_morning, "check": job_check, "update": job_update, "summary": job_summary,
        "oilreport": job_oil_report}


# ------------------------------------------------------------------ loop

def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", choices=list(JOBS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    DRY = args.dry_run

    if args.now:
        s = load_state()
        if args.now == "check" and "news" not in s:
            s["news"] = []
        if args.now == "update":
            job_update(s, "TEST update, run by hand (not on the hourly schedule)")
        else:
            JOBS[args.now](s)
        save_state(s)
        return

    print("Chad (operations) running. Ctrl+C to stop.")
    while True:
        try:
            status.beat("chad")
            heartbeat.ping("HEALTHCHECK_CHAD_URL", min_interval=60)
            t = now()
            hm = (t.hour, t.minute)
            if is_trading_day(t):
                s = load_state()
                if hm >= (8, 45) and "morning" not in s["done"] and hm < (13, 0):
                    s["done"].append("morning")
                    job_morning(s)
                if (8, 50) <= hm <= (13, 2):
                    job_check(s)
                if t.weekday() == 0 and hm >= (8, 50) and "oilreport" not in s["done"] and hm < (13, 0):
                    s["done"].append("oilreport")
                    save_state(s)
                    job_oil_report(s)
                for slot, kind in UPDATE_SLOTS:
                    key = f"update {slot[0]}:{slot[1]:02d}"
                    late = (slot[0] * 60 + slot[1] + 40) // 60, (slot[1] + 40) % 60
                    if slot <= hm < late and key not in s["done"]:
                        s["done"].append(key)
                        save_state(s)
                        job_update(s, kind)
                save_state(s)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"chad error: {e}")
            status.error("chad", e)
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
