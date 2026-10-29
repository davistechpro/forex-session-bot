"""
Vote Listener -- Valid/Invalid on SETUPS and TRADES, with a required reason.

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
import re
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

from modules import notifier
from modules import heartbeat
from modules import status
from modules import agent_reviewer

_last_beat = [0.0]

NY_TZ = pytz.timezone("America/New_York")
VOTES_FILE = Path("votes.csv")
OFFSET_FILE = Path("vote_offset.json")

FIELDS = [
    "voted_at_et", "voter", "verdict", "reason", "pair", "direction",
    "entry_price", "entry_level", "sl_pips", "method",
    "trend", "zone", "triggered_at", "signal_token", "post_type",
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
    """Add any missing columns to an older votes.csv (old rows were all trades)."""
    if not VOTES_FILE.exists():
        return
    with VOTES_FILE.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)
    missing = [f for f in FIELDS if f not in header]
    if not missing:
        return
    with VOTES_FILE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in FIELDS}
            if not row["post_type"]:
                row["post_type"] = "trade"
            w.writerow(row)
    print(f"votes.csv upgraded: added {', '.join(missing)} ({len(rows)} old rows kept)")


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
    notifier.log_chat(chat_id, voter, f"[tapped {verdict.upper()}]", msg.get("caption") or msg.get("text") or "")

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
        f"{mention} -- you picked <b>{verdict.upper()}</b> on the "
        f"{html.escape(str(sig.get('post_type') or 'trade'))} "
        f"{html.escape(str(sig.get('pair', '')))} {html.escape(str(sig.get('direction', '')))}.\n"
        f"Now type <b>why</b> in the chat. Your next message counts as your reason.",
    )
    if prompt:
        notifier.add_pending(notifier.pending_key(chat_id, prompt["message_id"]),
                             {"token": token, "user_id": uid, "name": voter, "verdict": verdict,
                              "tapped_at": time.time()})

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
        return _latest_open_prompt(chat_id, uid)

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
    # Replied to something else (e.g. Chad's review): still the voter's reason.
    return _latest_open_prompt(chat_id, uid)


REASON_WINDOW_MIN = 30   # a message counts as the reason if sent within 30 min of the tap


def _latest_open_prompt(chat_id, uid):
    """The voter's newest open vote in this chat, if they tapped within the window.
    Works because the bot's group privacy is OFF, so it sees normal messages."""
    best = None
    for key, p in notifier._load(notifier.PENDING_FILE).items():
        if p.get("user_id") != uid or not key.startswith(f"{chat_id}:"):
            continue
        if time.time() - p.get("tapped_at", 0) > REASON_WINDOW_MIN * 60:
            continue
        if best is None or p.get("tapped_at", 0) > best[1].get("tapped_at", 0):
            best = (key, p)
    return best if best else (None, None)


def _clear_prompts(token: str, uid):
    """Drop any other open prompts for this voter on this trade."""
    for key, p in list(notifier._load(notifier.PENDING_FILE).items()):
        if p.get("token") == token and p.get("user_id") == uid:
            notifier.pop_pending(key)


CHAD_NAME = re.compile(r"\bchad\b", re.I)


def _is_reply_to_chad(msg: dict) -> bool:
    r = msg.get("reply_to_message") or {}
    return bool((r.get("from") or {}).get("is_bot")) and (r.get("text") or "").rstrip().endswith("-- Chad")


CONFIRM_RE = re.compile(r"^\s*(chad[\s,:]*)?(confirm(ed)?|approved?|yes,? save( it)?|save it)\b", re.I)


def _confirm_rule(msg: dict, text: str) -> bool:
    """Devin, Oct 28 call: the team can grow Chad's rules from the chat. When Chad
    ends a self-review with 'Proposed rule: ...' and a teammate replies 'confirm',
    the rule is saved to agent/team_rules.md and Chad uses it from then on."""
    if not (_is_reply_to_chad(msg) and CONFIRM_RE.match(text)):
        return False
    rule = agent_reviewer.proposed_rule((msg.get("reply_to_message") or {}).get("text", ""))
    if not rule:
        return False
    who = _voter_name(msg.get("from") or {})
    agent_reviewer.save_team_rule(who, rule)
    print(f"[{datetime.now(NY_TZ):%H:%M:%S}] {who} confirmed a Chad rule: {rule[:80]}")
    notifier._call("sendMessage", {
        "chat_id": (msg.get("chat") or {}).get("id"),
        "text": f"Saved as a team rule (confirmed by {who}): {rule}\n\n-- Chad",
        "reply_to_message_id": msg.get("message_id"),
    })
    return True


def _ask_chad(msg: dict, text: str):
    if _confirm_rule(msg, text):
        return
    asker = _voter_name(msg.get("from") or {})
    chat_id = (msg.get("chat") or {}).get("id")
    print(f"[{datetime.now(NY_TZ):%H:%M:%S}] {asker} asked Chad: {text[:80]}")
    if _is_reply_to_chad(msg):   # a reply to Chad = feedback he keeps and learns from
        agent_reviewer.record_feedback(asker, text, (msg.get("reply_to_message") or {}).get("text", ""))
    agent_reviewer.answer_async(chat_id, msg.get("message_id"), text, asker)


MEDIA_DIR = Path("chat_media")


def _save_photo(msg: dict):
    """Download a photo the team posted (largest size) into chat_media/.
    Returns the saved path, or None."""
    try:
        import os
        import requests
        best = max(msg["photo"], key=lambda ph: ph.get("file_size", 0) or ph.get("width", 0))
        info = notifier._call("getFile", {"file_id": best["file_id"]})
        if not info or not info.get("file_path"):
            return None
        url = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{info['file_path']}"
        data = requests.get(url, timeout=30).content
        MEDIA_DIR.mkdir(exist_ok=True)
        out = MEDIA_DIR / f"{datetime.now(NY_TZ):%Y%m%d_%H%M%S}_{msg.get('message_id')}.jpg"
        out.write_bytes(data)
        return out
    except Exception as e:
        print(f"photo save failed: {e}")
        return None


def log_chat(msg: dict):
    """Team messages -> chat_log.csv (the bot's own posts are logged by notifier).
    Photos the team posts (chart screenshots) are saved to chat_media/."""
    r = msg.get("reply_to_message") or {}
    text = msg.get("text") or msg.get("caption") or ""
    if msg.get("photo"):
        saved = _save_photo(msg)
        text = f"[photo: {saved}] {text}".strip() if saved else f"[photo, not saved] {text}".strip()
    elif not text:
        text = "[non-text message]"
    notifier.log_chat((msg.get("chat") or {}).get("id"), _voter_name(msg.get("from") or {}),
                      text, r.get("text") or r.get("caption") or "")


def handle_message(msg: dict):
    log_chat(msg)
    reason = (msg.get("text") or "").strip()
    if not reason or reason.startswith("/"):
        return
    # 0. "confirm" under a Chad "Proposed rule" message -> saved as a team rule
    if _confirm_rule(msg, reason):
        return
    # 1. Talking to Chad by name -> Chad answers (never counted as a vote reason)
    if CHAD_NAME.search(reason):
        _ask_chad(msg, reason)
        return
    key, p = _find_pending_for_reply(msg)
    if not p:
        # 2. A reply to one of Chad's messages, with no vote waiting -> Chad answers
        if _is_reply_to_chad(msg):
            _ask_chad(msg, reason)
        return

    token, uid = p["token"], p["user_id"]
    sig = notifier.record_vote(token, p["name"], p["verdict"], reason=reason, user_id=uid)
    _clear_prompts(token, uid)

    now_et = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    _append_vote({
        "voted_at_et": now_et, "voter": p["name"], "verdict": p["verdict"],
        "reason": reason, "signal_token": token,
        "post_type": sig.get("post_type") or "trade",
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
            heartbeat.ping("HEALTHCHECK_LISTENER_URL", min_interval=60)
            if time.time() - _last_beat[0] > 30:
                status.beat("listener")
                _last_beat[0] = time.time()
            updates = notifier.get_updates(offset=offset, timeout=10)  # short polls: desktop network drops idle connections
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
