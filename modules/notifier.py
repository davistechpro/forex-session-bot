"""Telegram Notifier — trade alerts with inline vote buttons."""
import hashlib
import json
import os
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
SIGNAL_MAP_FILE = Path("signal_map.json")


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def _call(method: str, payload: dict):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("NOTIFIER: TELEGRAM_BOT_TOKEN not set")
        return None
    try:
        resp = requests.post(API.format(token=token, method=method),
                             json=payload, timeout=payload.get("timeout", 0) + 15)
        data = resp.json()
        if not data.get("ok"):
            print(f"NOTIFIER: {method} failed: {data.get('description')}")
            return None
        return data.get("result")
    except Exception as e:
        print(f"NOTIFIER: {method} error: {e}")
        return None


def short_token(signal_id: str) -> str:
    return hashlib.sha1(signal_id.encode()).hexdigest()[:8]


def vote_keyboard(token: str) -> dict:
    """Must be re-sent on every edit -- Telegram strips the keyboard otherwise."""
    return {"inline_keyboard": [[
        {"text": "Valid", "callback_data": f"v|{token}"},
        {"text": "Invalid", "callback_data": f"x|{token}"},
    ]]}


def _load_map() -> dict:
    if SIGNAL_MAP_FILE.exists():
        try:
            return json.loads(SIGNAL_MAP_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_map(m: dict):
    try:
        SIGNAL_MAP_FILE.write_text(json.dumps(m, indent=2, default=str))
    except Exception as e:
        print(f"NOTIFIER: could not persist signal map: {e}")


def remember_signal(token: str, info: dict):
    m = _load_map()
    info.setdefault("votes", {})
    info.setdefault("base_text", "")
    m[token] = info
    _save_map(m)


def lookup_signal(token: str) -> dict:
    return _load_map().get(token, {})


def record_vote(token: str, voter: str, verdict: str) -> dict:
    m = _load_map()
    sig = m.setdefault(token, {"votes": {}})
    sig.setdefault("votes", {})[voter] = verdict
    _save_map(m)
    return sig


def set_base_text(token: str, text: str):
    m = _load_map()
    if token in m:
        m[token]["base_text"] = text
        _save_map(m)


def send_message(text: str, signal_id: str = None) -> bool:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("NOTIFIER: TELEGRAM_CHAT_ID not set")
        return False

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if signal_id:
        payload["reply_markup"] = vote_keyboard(short_token(signal_id))

    result = _call("sendMessage", payload)
    if result and signal_id:
        set_base_text(short_token(signal_id), text)
    return result is not None


def answer_callback(callback_id: str, text: str):
    _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def edit_message_text(chat_id, message_id, text: str, token: str = None, is_photo: bool = False):
    """
    Photo messages have a CAPTION, not text -- editMessageText fails on
    them, so route those to editMessageCaption.
    """
    payload = {"chat_id": chat_id, "message_id": message_id, "parse_mode": "HTML"}
    if token:
        payload["reply_markup"] = vote_keyboard(token)

    if is_photo:
        payload["caption"] = text
        _call("editMessageCaption", payload)
    else:
        payload["text"] = text
        _call("editMessageText", payload)


def get_updates(offset: int = None, timeout: int = 25):
    payload = {"timeout": timeout}
    if offset is not None:
        payload["offset"] = offset
    return _call("getUpdates", payload) or []


def format_vote_tally(votes: dict) -> str:
    if not votes:
        return ""
    valid = [u for u, v in votes.items() if v == "valid"]
    invalid = [u for u, v in votes.items() if v == "invalid"]
    parts = []
    if valid:
        parts.append(f"Valid ({len(valid)}): {', '.join(valid)}")
    if invalid:
        parts.append(f"Invalid ({len(invalid)}): {', '.join(invalid)}")
    return "\n\n<b>Votes</b>\n" + "\n".join(parts)


def format_trade_alert(pair: str, result: dict) -> str:
    entry = result["entry"]
    zone = result["zone"]

    lvl_names = {"H4": "4H", "H1": "1H", "15": "15m", "5": "5m", "1": "1m"}
    lvl = lvl_names.get(entry.get("entry_level", ""), entry.get("entry_level", entry["source_tf"]))

    d = 3 if "JPY" in pair.upper() else 5
    f = f"{{:.{d}f}}"

    lines = [
        f"<b>{pair} - {entry['direction']}</b>",
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
        names = ", ".join(lvl_names.get(tf, tf) for tf in conf)
        lines.append(f"Confluence: {names}")

    lines.append(f"Method: {entry['method']}")
    lines.append(f"Triggered: {entry['time']}")
    return "\n".join(lines)

def send_photo(photo_path, caption: str, signal_id: str = None) -> bool:
    """
    Send a chart image with the alert as its caption and the vote buttons
    attached. Telegram caps captions at 1024 chars.
    """
    import os
    from pathlib import Path as _P

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not chat_id or not token:
        print("NOTIFIER: chat id / token not set")
        return False

    photo_path = _P(photo_path)
    if not photo_path.exists():
        print(f"NOTIFIER: chart not found at {photo_path} -- sending text only")
        return send_message(caption, signal_id=signal_id)

    short_caption = caption if len(caption) <= 1000 else caption[:990] + "\n..."

    data = {"chat_id": chat_id, "caption": short_caption, "parse_mode": "HTML"}
    if signal_id:
        data["reply_markup"] = json.dumps(vote_keyboard(short_token(signal_id)))

    try:
        with photo_path.open("rb") as fh:
            resp = requests.post(
                API.format(token=token, method="sendPhoto"),
                data=data, files={"photo": fh}, timeout=60,
            )
        payload = resp.json()
        if not payload.get("ok"):
            print(f"NOTIFIER: sendPhoto failed: {payload.get('description')}")
            return False
    except Exception as e:
        print(f"NOTIFIER: sendPhoto error: {e}")
        return False

    if signal_id:
        set_base_text(short_token(signal_id), short_caption)
    return True

def send_photo(photo_path, caption: str, signal_id: str = None) -> bool:
    """
    Send a chart image with the alert as its caption and the vote buttons
    attached. Telegram caps captions at 1024 chars.
    """
    import os
    from pathlib import Path as _P

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not chat_id or not token:
        print("NOTIFIER: chat id / token not set")
        return False

    photo_path = _P(photo_path)
    if not photo_path.exists():
        print(f"NOTIFIER: chart not found at {photo_path} -- sending text only")
        return send_message(caption, signal_id=signal_id)

    short_caption = caption if len(caption) <= 1000 else caption[:990] + "\n..."

    data = {"chat_id": chat_id, "caption": short_caption, "parse_mode": "HTML"}
    if signal_id:
        data["reply_markup"] = json.dumps(vote_keyboard(short_token(signal_id)))

    try:
        with photo_path.open("rb") as fh:
            resp = requests.post(
                API.format(token=token, method="sendPhoto"),
                data=data, files={"photo": fh}, timeout=60,
            )
        payload = resp.json()
        if not payload.get("ok"):
            print(f"NOTIFIER: sendPhoto failed: {payload.get('description')}")
            return False
    except Exception as e:
        print(f"NOTIFIER: sendPhoto error: {e}")
        return False

    if signal_id:
        set_base_text(short_token(signal_id), short_caption)
    return True


def remember_tally(token: str, tally: str):
    """Track the last rendered tally so we skip no-op edits."""
    m = _load_map()
    if token in m:
        m[token]["last_tally"] = tally
        _save_map(m)

