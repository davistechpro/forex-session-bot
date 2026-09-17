"""
Vote Listener — records Valid/Invalid taps on trade alerts.

Everyone in the group can vote; the tally updates live and the buttons
stay active. Re-voting overwrites that person's earlier verdict.
"""
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

from modules import notifier

NY_TZ = pytz.timezone("America/New_York")
VOTES_FILE = Path("votes.csv")
OFFSET_FILE = Path("vote_offset.json")

FIELDS = [
    "voted_at_et", "voter", "verdict", "pair", "direction",
    "entry_price", "entry_level", "sl_pips", "method",
    "trend", "zone", "triggered_at", "signal_token",
]


def _load_offset():
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text()).get("offset")
        except Exception:
            return None
    return None


def _save_offset(offset: int):
    try:
        OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    except Exception as e:
        print(f"WARN: could not save offset: {e}")


def _append_vote(row: dict):
    new_file = not VOTES_FILE.exists()
    with VOTES_FILE.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def handle_callback(cb: dict):
    data = cb.get("data", "")
    if "|" not in data:
        return

    kind, token = data.split("|", 1)
    verdict = "valid" if kind == "v" else "invalid"

    user = cb.get("from", {})
    voter = user.get("username") or user.get("first_name") or str(user.get("id", "?"))

    sig = notifier.record_vote(token, voter, verdict)
    now_et = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")

    _append_vote({
        "voted_at_et": now_et,
        "voter": voter,
        "verdict": verdict,
        "signal_token": token,
        **{k: sig.get(k, "") for k in
           ["pair", "direction", "entry_price", "entry_level",
            "sl_pips", "method", "trend", "zone", "triggered_at"]},
    })

    votes = sig.get("votes", {})
    nv = sum(1 for v in votes.values() if v == "valid")
    ni = sum(1 for v in votes.values() if v == "invalid")
    print(f"[{now_et}] {voter} -> {verdict.upper()} on {sig.get('pair','signal')} | tally: {nv} valid / {ni} invalid")

    notifier.answer_callback(cb["id"], f"Your vote: {verdict}")

    msg = cb.get("message") or {}
    if msg:
        prev = sig.get("last_tally")
        tally = notifier.format_vote_tally(votes)
        if tally != prev:
            base = sig.get("base_text") or msg.get("caption") or msg.get("text", "")
            notifier.edit_message_text(
                msg["chat"]["id"], msg["message_id"],
                base + tally,
                token=token,
                is_photo=bool(msg.get("photo")),
            )
            notifier.remember_tally(token, tally)


def main():
    if not notifier.is_configured():
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from .env")
        raise SystemExit(1)

    print("Vote listener running. Ctrl+C to stop.")
    print(f"Logging to {VOTES_FILE.resolve()}")

    offset = _load_offset()
    while True:
        try:
            updates = notifier.get_updates(offset=offset, timeout=25)
            for u in updates:
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    handle_callback(u["callback_query"])
            if updates:
                _save_offset(offset)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"listener error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

