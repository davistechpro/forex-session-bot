"""
Vote Listener -- Valid/Invalid on TRADES, with a required reason.

  1. Someone taps Valid or Invalid on a trade alert.
  2. The bot replies to the alert tagging them: "why?" (their keyboard
     opens straight into a reply).
  3. Their reply is the reason. Only then is the vote recorded in
     votes.csv and shown in the tally.

Until they reply, the alert shows "Awaiting reason: <name>". Replying
directly to the trade alert also counts. Re-voting replaces that
person's earlier vote (both rows stay in votes.csv as history).
"""
import csv
import html
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
    "voted_at_et", "voter", "verdict", "reason", "pair", "direction",
    "entry_price", "entry_level", "sl_pips", "method",
    "trend", "zone", "triggered_at", "signal_token",
]
SIGNAL_FIELDS = ["pair", "direction", "entry_price", "entry_level",
                 "sl_pips", "method", "trend", "zone", "triggered_at"]


# ---------------------------------------------------------------- storage

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


def _migrate_votes_csv():
    """Older votes.csv has no 'reason' column -- add it (blank) once."""
    if not VOTES_FILE.exists():
        return
    with VOTES_FILE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        header = rows[0].keys() if rows else []
    if rows and "reason" in header:
        return
    if not rows:
        return
    with VOTES_FILE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"votes.csv upgraded with a 'reason' column ({len(rows)} old rows kept, reason blank)")


def _append_vote(row: dict):
    new_file = not VOTES_FILE.exists()
    with VOTES_FILE.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def _voter_name(user: dict) -> str:
    return user.get("username") or user.get("first_name") or str(user.get("id", "?"))


# ------------------------------------------------------------------ tally

def _refresh_tally(token: str):
    sig = notifier.lookup_signal(token)
    chat_id, msg_id = sig.get("alert_chat_id"), sig.get("alert_message_id")
    if not chat_id or not msg_id:
        return
    tally = notifier.format_vote_tally(sig.get("votes", {}), sig.get("pending", {}))
    if tally == sig.get("last_tally"):
        return
    base = sig.get("base_text", "")
    notifier.edit_text(chat_id, msg_id, base + tally, token=token,
                       is_photo=bool(sig.get("alert_is_photo")))
    notifier.remember_tally(token, tally)


# -------------------------------------------------------------- handlers

def handle_callback(cb: dict):
    data = cb.get("data", "")
    if "|" not in data:
        return
    kind, token = data.split("|", 1)
    verdict = "valid" if kind == "v" else "invalid"

    user = cb.get("from", {})
    uid, voter = user.get("id"), _voter_name(user)
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")

    sig = notifier.lookup_signal(token)
    if not sig:
        notifier.answer_callback(cb["id"], "This alert is no longer tracked.")
        return

    # Keep the alert's own ids fresh (older alerts didn't store them).
    if msg and not sig.get("alert_message_id"):
        notifier.update_signal(token, alert_chat_id=chat_id, alert_message_id=msg["message_id"],
                               alert_is_photo=bool(msg.get("photo")),
                               base_text=sig.get("base_text") or msg.get("caption") or msg.get("text", ""))

    notifier.set_pending_vote(token, uid, voter, verdict)

    mention = f'<a href="tg://user?id={uid}">{html.escape(user.get("first_name") or voter)}</a>'
    prompt = notifier.send_reason_prompt(
        chat_id, msg.get("message_id"),
        f"{mention} -- you picked <b>{verdict.upper()}</b> on "
        f"{html.escape(str(sig.get('pair', '')))} {html.escape(str(sig.get('direction', '')))}.\n"
        f"Reply to this message with <b>why</b>. Your vote counts once you reply.",
    )
    if prompt:
        notifier.add_pending(notifier.pending_key(chat_id, prompt["message_id"]),
                             {"token": token, "user_id": uid, "name": voter, "verdict": verdict})

    notifier.answer_callback(cb["id"], f"{verdict.upper()} -- now reply with why")
    print(f"[{datetime.now(NY_TZ):%H:%M:%S}] {voter} tapped {verdict.upper()} on "
          f"{sig.get('pair', 'signal')} -- waiting for reason")
    _refresh_tally(token)


def _find_pending_for_reply(msg: dict):
    """Match a reply to a reason prompt, or to the trade alert itself."""
    reply_to = msg.get("reply_to_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    uid = (msg.get("from") or {}).get("id")
    if not reply_to:
        return None, None

    key = notifier.pending_key(chat_id, reply_to.get("message_id"))
    p = notifier.get_pending(key)
    if p and p.get("user_id") == uid:
        return key, p

    # Replied to the trade alert instead of the prompt.
    token = notifier.token_for_alert(chat_id, reply_to.get("message_id"))
    if token:
        sig = notifier.lookup_signal(token)
        pend = (sig.get("pending") or {}).get(str(uid))
        if pend:
            return None, {"token": token, "user_id": uid, "name": pend["name"], "verdict": pend["verdict"]}
    return None, None


def _clear_prompts(token: str, uid):
    """Drop any other open prompts for this voter on this trade."""
    for key, p in list(notifier._load(notifier.PENDING_FILE).items()):
        if p.get("token") == token and p.get("user_id") == uid:
            notifier.pop_pending(key)


def handle_message(msg: dict):
    reason = (msg.get("text") or "").strip()
    if not reason:
        return
    key, p = _find_pending_for_reply(msg)
    if not p:
        return

    token, uid = p["token"], p["user_id"]
    sig = notifier.record_vote(token, p["name"], p["verdict"], reason=reason, user_id=uid)
    _clear_prompts(token, uid)

    now_et = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    _append_vote({
        "voted_at_et": now_et, "voter": p["name"], "verdict": p["verdict"],
        "reason": reason, "signal_token": token,
        **{k: sig.get(k, "") for k in SIGNAL_FIELDS},
    })
    print(f"[{now_et}] {p['name']} -> {p['verdict'].upper()} on {sig.get('pair', 'signal')}: {reason}")
    _refresh_tally(token)


def main():
    if not notifier.is_configured():
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_TRADES_CHAT_ID missing from .env")
        raise SystemExit(1)

    _migrate_votes_csv()
    print("Vote listener running (reason required). Ctrl+C to stop.")
    print(f"Logging to {VOTES_FILE.resolve()}")

    offset = _load_offset()
    while True:
        try:
            updates = notifier.get_updates(offset=offset, timeout=25)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    if "callback_query" in u:
                        handle_callback(u["callback_query"])
                    elif "message" in u:
                        handle_message(u["message"])
                except Exception as e:
                    print(f"update {u.get('update_id')} failed: {e}")
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
