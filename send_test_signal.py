"""Send a sample trade alert with vote buttons to the Telegram group."""
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from modules import notifier

FAKE = {
    "zone_source_tf": "1H",
    "deciding_tf": "3-of-3 agree (Daily+4H+1H)",
    "zone": {
        "type": "demand",
        "bottom": 178.912,
        "top": 179.068,
        "origin_time": pd.Timestamp("2026-09-16 00:00", tz="UTC"),
    },
    "entry": {
        "direction": "LONG",
        "entry_price": 179.000,
        "stop_loss": 178.920,
        "take_profit": 179.160,
        "sl_pips": 8,
        "tp_pips": 16,
        "entry_level": "15",
        "source_tf": "1H",
        "method": "confirmation_sequence",
        "time": pd.Timestamp("2026-09-16 00:12", tz="UTC"),
        "ladder": [("15", 179.000), ("5", 178.952), ("1", 178.932)],
    },
}

if __name__ == "__main__":
    if not notifier.is_configured():
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from .env")
        raise SystemExit(1)

    sid = "TEST|EUR_JPY|LONG|179.000"
    notifier.remember_signal(notifier.short_token(sid), {
        "pair": "EUR_JPY", "direction": "LONG", "entry_price": 179.000,
        "entry_level": "15", "sl_pips": 8, "method": "confirmation_sequence",
        "trend": "3-of-3 agree (Daily+4H+1H)",
        "zone": "DEMAND 178.912-179.068 (1H)",
        "triggered_at": "2026-09-16 00:12:00+00:00",
    })

    text = "<b>TEST SIGNAL - not a real trade</b>\n\n" + notifier.format_trade_alert("EUR_JPY", FAKE)
    print("sent:", notifier.send_message(text, signal_id=sid))
