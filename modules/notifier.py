"""
Telegram Notifier — sends trade alerts to a Telegram chat.

Setup (one time):
  1. In Telegram, message @BotFather -> /newbot -> follow prompts.
  2. Message your new bot anything (this opens the chat).
  3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates and copy the
     "chat":{"id":...} value.
  4. Put both in .env as TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
"""
import os
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("NOTIFIER: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- skipping alert")
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        if resp.status_code == 200:
            return True
        print(f"NOTIFIER: Telegram returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"NOTIFIER: send failed: {e}")
        return False


def format_trade_alert(pair: str, result: dict) -> str:
    entry = result["entry"]
    zone = result["zone"]

    lvl_names = {"H4": "4H", "H1": "1H", "15": "15m", "5": "5m", "1": "1m"}
    lvl = lvl_names.get(entry.get("entry_level", ""), entry.get("entry_level", entry["source_tf"]))

    d = 3 if "JPY" in pair.upper() else 5
    f = f"{{:.{d}f}}"

    arrow = "🔻" if entry["direction"] == "SHORT" else "🔺"

    lines = [
        f"{arrow} *{pair} — {entry['direction']}*",
        "",
        f"*Entry:* {f.format(entry['entry_price'])}  _(off the {lvl})_",
        f"*SL:* {f.format(entry['stop_loss'])}  ({entry.get('sl_pips', 8)} pips)",
        f"*TP:* {f.format(entry['take_profit'])}  ({entry.get('tp_pips', 16)} pips, 2R)",
        "",
        f"Trend: {result['deciding_tf']}",
        f"Zone: {zone['type'].upper()} {f.format(zone['bottom'])}-{f.format(zone['top'])} ({result['zone_source_tf']})",
        f"Method: {entry['method']}",
    ]

    if entry.get("ladder"):
        ladder = "  →  ".join(
            f"{lvl_names.get(tf, tf)} {f.format(px)}" for tf, px in entry["ladder"]
        )
        lines.append(f"Nested: {ladder}")

    lines.append(f"Triggered: {entry['time']}")
    return "\n".join(lines)
