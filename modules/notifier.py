"""
Telegram Notifier -- two chats, vote buttons on trades.

  SETUPS chat (TELEGRAM_SETUPS_CHAT_ID): every trade idea -- a zone that
    qualifies but has not triggered yet. No voting.
  TRADES chat (TELEGRAM_TRADES_CHAT_ID, falls back to TELEGRAM_CHAT_ID):
    every trade the bot actually takes. Valid/Invalid buttons; a vote only
    counts once the voter replies with WHY.

HTML parse mode (not Markdown): pair names contain underscores.
"""
import hashlib
import html
import json
import os
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
SIGNAL_MAP_FILE = Path("signal_map.json")
PENDING_FILE = Path("pending_votes.json")

LVL_NAMES = {"H4": "4H", "H1": "1H", "15": "15m", "5": "5m", "1": "1m"}


# ------------------------------------------------------------ plumbing

def _chat_id(kind: str = "trades"):
    if kind == "setups":
        return os.getenv("TELEGRAM_SETUPS_CHAT_ID")
    return os.getenv("TELEGRAM_TRADES_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and _chat_id("trades"))


def setups_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and _chat_id("setups"))


def _call(method: str, payload: dict, files=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("NOTIFIER: TELEGRAM_BOT_TOKEN not set")
        return None
    try:
        url = API.format(token=token, method=method)
        timeout = payload.get("timeout", 0) + 15 if not files else 60
        if files:
            data = dict(payload)
            if "reply_markup" in data:
                data["reply_markup"] = json.dumps(data["reply_markup"])
            resp = requests.post(url, data=data, files=files, timeout=timeout)
        else:
            resp = requests.post(url, json=payload, timeout=timeout)
        body = resp.json()
        if not body.get("ok"):
            desc = body.get("description", "")
            if "message is not modified" not in desc:
                print(f"NOTIFIER: {method} failed: {desc}")
            return None
        return body.get("result")
    except Exception as e:
        print(f"NOTIFIER: {method} error: {e}")
        return None


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save(path: Path, data: dict):
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        print(f"NOTIFIER: could not write {path}: {e}")


def short_token(signal_id: str) -> str:
    return hashlib.sha1(signal_id.encode()).hexdigest()[:8]


def vote_keyboard(token: str) -> dict:
    """Re-sent on every edit -- Telegram strips the keyboard otherwise."""
    return {"inline_keyboard": [[
        {"text": "Valid", "callback_data": f"v|{token}"},
        {"text": "Invalid", "callback_data": f"x|{token}"},
    ]]}


# ------------------------------------------------------- signal records

def remember_signal(token: str, info: dict):
    m = _load(SIGNAL_MAP_FILE)
    existing = m.get(token, {})
    info.setdefault("votes", existing.get("votes", {}))
    info.setdefault("pending", existing.get("pending", {}))
    m[token] = {**existing, **info}
    _save(SIGNAL_MAP_FILE, m)


def lookup_signal(token: str) -> dict:
    return _load(SIGNAL_MAP_FILE).get(token, {})


def update_signal(token: str, **fields):
    m = _load(SIGNAL_MAP_FILE)
    m.setdefault(token, {"votes": {}, "pending": {}}).update(fields)
    _save(SIGNAL_MAP_FILE, m)
    return m[token]


def token_for_alert(chat_id, message_id):
    for tok, sig in _load(SIGNAL_MAP_FILE).items():
        if str(sig.get("alert_chat_id")) == str(chat_id) and sig.get("alert_message_id") == message_id:
            return tok
    return None


# ------------------------------------------------------- pending votes

def pending_key(chat_id, message_id) -> str:
    return f"{chat_id}:{message_id}"


def add_pending(key: str, info: dict):
    p = _load(PENDING_FILE)
    p[key] = info
    _save(PENDING_FILE, p)


def get_pending(key: str):
    return _load(PENDING_FILE).get(key)


def pop_pending(key: str):
    p = _load(PENDING_FILE)
    v = p.pop(key, None)
    _save(PENDING_FILE, p)
    return v


# --------------------------------------------------------------- sending

def _after_send(result, signal_id, chat_id, is_photo, text):
    if result and signal_id:
        update_signal(short_token(signal_id),
                      alert_chat_id=chat_id, alert_message_id=result.get("message_id"),
                      alert_is_photo=is_photo, base_text=text)
    return result


def send_message(text: str, signal_id: str = None, chat: str = "trades"):
    chat_id = _chat_id(chat)
    if not chat_id:
        print(f"NOTIFIER: no chat id set for '{chat}'")
        return None
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if signal_id:
        payload["reply_markup"] = vote_keyboard(short_token(signal_id))
    return _after_send(_call("sendMessage", payload), signal_id, chat_id, False, text)


def send_photo(photo_path, caption: str, signal_id: str = None, chat: str = "trades"):
    chat_id = _chat_id(chat)
    if not chat_id:
        print(f"NOTIFIER: no chat id set for '{chat}'")
        return None
    photo_path = Path(photo_path)
    if not photo_path.exists():
        print(f"NOTIFIER: chart not found at {photo_path} -- sending text only")
        return send_message(caption, signal_id=signal_id, chat=chat)
    cap = caption if len(caption) <= 1000 else caption[:990] + "\n..."
    payload = {"chat_id": chat_id, "caption": cap, "parse_mode": "HTML"}
    if signal_id:
        payload["reply_markup"] = vote_keyboard(short_token(signal_id))
    with photo_path.open("rb") as fh:
        result = _call("sendPhoto", payload, files={"photo": fh})
    return _after_send(result, signal_id, chat_id, True, cap)


def send_reason_prompt(chat_id, reply_to_id, text: str):
    """Asks the voter for their reason. force_reply puts their cursor in a reply."""
    return _call("sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "reply_to_message_id": reply_to_id,
        "reply_markup": {"force_reply": True, "selective": True,
                         "input_field_placeholder": "Why is it valid / invalid?"},
    })


def answer_callback(callback_id: str, text: str):
    _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def edit_text(chat_id, message_id, text: str, token: str = None, is_photo: bool = False):
    payload = {"chat_id": chat_id, "message_id": message_id, "parse_mode": "HTML"}
    if token:
        payload["reply_markup"] = vote_keyboard(token)
    if is_photo:
        payload["caption"] = text if len(text) <= 1024 else text[:1014] + "\n..."
        return _call("editMessageCaption", payload)
    payload["text"] = text
    return _call("editMessageText", payload)


def get_updates(offset: int = None, timeout: int = 25):
    payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return _call("getUpdates", payload) or []


# ------------------------------------------------------------ formatting

def _vote_entries(votes: dict):
    """Normalise votes; older records stored {name: verdict}."""
    out = []
    for k, v in votes.items():
        if isinstance(v, str):
            out.append({"name": k, "verdict": v, "reason": ""})
        else:
            out.append(v)
    return out


def format_vote_tally(votes: dict, pending: dict = None) -> str:
    entries = _vote_entries(votes or {})
    pending = pending or {}
    if not entries and not pending:
        return ""
    lines = ["", "", "<b>Votes</b>"]
    valid = [e["name"] for e in entries if e["verdict"] == "valid"]
    invalid = [e["name"] for e in entries if e["verdict"] == "invalid"]
    if valid:
        lines.append(f"Valid ({len(valid)}): {', '.join(valid)}")
    if invalid:
        lines.append(f"Invalid ({len(invalid)}): {', '.join(invalid)}")
    if pending:
        waiting = ", ".join(f"{p['name']} ({p['verdict']})" for p in pending.values())
        lines.append(f"<i>Awaiting reason: {waiting}</i>")
    return "\n".join(lines)


def _fmt(pair):
    d = 3 if "JPY" in pair.upper() else 5
    return f"{{:.{d}f}}"


def format_trade_alert(pair: str, result: dict) -> str:
    entry, zone = result["entry"], result["zone"]
    lvl = LVL_NAMES.get(entry.get("entry_level", ""), entry.get("entry_level", entry["source_tf"]))
    f = _fmt(pair)
    lines = [
        f"<b>TRADE -- {pair} {entry['direction']}</b>",
        "",
        f"<b>Entry:</b> {f.format(entry['entry_price'])}  <i>({lvl} zone)</i>",
        f"<b>SL:</b> {f.format(entry['stop_loss'])}  ({entry.get('sl_pips', 8)} pips)",
        f"<b>TP:</b> {f.format(entry['take_profit'])}  ({entry.get('tp_pips', 16)} pips, 2R)",
        "",
        f"Trend: {result['deciding_tf']}",
        f"Zone: {zone['type'].upper()} {f.format(zone['bottom'])}-{f.format(zone['top'])} ({result['zone_source_tf']})",
    ]
    conf = entry.get("confluence") or []
    if conf:
        lines.append("Confluence: " + ", ".join(LVL_NAMES.get(tf, tf) for tf in conf))
    lines.append(f"Method: {entry['method']}")
    lines.append(f"Triggered: {entry['time']}")
    lines.append("")
    lines.append("<i>Vote, then reply to the prompt with why.</i>")
    return "\n".join(lines)


def format_setup_alert(pair: str, result: dict) -> str:
    s, zone = result["setup"], result["zone"]
    f = _fmt(pair)
    direction = "LONG" if result["valid_direction"] == "bullish" else "SHORT"
    lines = [
        f"<b>SETUP -- {pair} {direction}</b>",
        "",
        f"Zone: {zone['type'].upper()} {f.format(zone['bottom'])}-{f.format(zone['top'])} ({result['zone_source_tf']})",
        f"Trend: {result['deciding_tf']}",
        f"Zone formed: {s.get('formed', '')}",
        f"<b>Waiting for:</b> {s['waiting_for']}",
    ]
    if s.get("planned_entry") is not None:
        lvl = LVL_NAMES.get(s.get("planned_level", ""), s.get("planned_level", ""))
        lines += [
            "",
            "<b>If it triggers:</b>",
            f"Entry {f.format(s['planned_entry'])}  <i>({lvl} zone)</i>",
            f"SL {f.format(s['planned_sl'])} ({s['planned_sl_pips']} pips)  /  "
            f"TP {f.format(s['planned_tp'])} ({s['planned_sl_pips'] * 2} pips)",
        ]
    else:
        lines += ["", "<i>Entry is set at the 1m zone once the confirmation completes.</i>"]
    if s.get("note"):
        lines += ["", f"<i>{html.escape(str(s['note']))}</i>"]
    lines += ["", "<i>Trade idea only -- not taken. If it triggers it posts in the trades chat.</i>"]
    return "\n".join(lines)


# ------------------------------------------------ vote records / compat

def record_vote(token: str, voter: str, verdict: str, reason: str = "", user_id=None) -> dict:
    """One vote per person (re-voting overwrites). Clears their pending entry."""
    m = _load(SIGNAL_MAP_FILE)
    sig = m.setdefault(token, {"votes": {}, "pending": {}})
    votes = sig.setdefault("votes", {})
    votes[voter] = {"name": voter, "verdict": verdict, "reason": reason, "user_id": user_id}
    sig.setdefault("pending", {}).pop(str(user_id), None)
    _save(SIGNAL_MAP_FILE, m)
    return sig


def set_pending_vote(token: str, user_id, voter: str, verdict: str) -> dict:
    m = _load(SIGNAL_MAP_FILE)
    sig = m.setdefault(token, {"votes": {}, "pending": {}})
    sig.setdefault("pending", {})[str(user_id)] = {"name": voter, "verdict": verdict}
    _save(SIGNAL_MAP_FILE, m)
    return sig


def remember_tally(token: str, tally: str):
    """Track the last rendered tally so we skip no-op edits."""
    update_signal(token, last_tally=tally)


def set_base_text(token: str, text: str):
    update_signal(token, base_text=text)


# older name, kept so nothing else breaks
edit_message_text = edit_text
