"""
Record votes whose reason was typed in the chat but never captured
(before the bot could see normal group messages).

  py backfill_votes.py            shows what it would record
  py backfill_votes.py --apply    records them (skips any already recorded)
"""
import sys
from datetime import datetime

import pytz

import vote_listener as vl
from modules import notifier

NY = pytz.timezone("America/New_York")

# token, voter name, verdict, reason (Gage's messages in the setups chat, Oct 20 ~9:20 PM)
BACKFILL = [
    ("8051869d", "GmanP03", "valid",
     "Good trade, the pair is bearish and this short is valid. However, the bot has sent another "
     "trade for this pair in the opposite direction shortly after. Not sure what happened there."),
    ("f05e0595", "GmanP03", "invalid",
     "Invalid; the price is bearish and should not have been called out."),
]


def main():
    apply = "--apply" in sys.argv
    for token, name, verdict, reason in BACKFILL:
        sig = notifier.lookup_signal(token)
        if not sig:
            print(f"{token}: not found in signal_map.json -- skipped")
            continue
        if name in (sig.get("votes") or {}):
            print(f"{token} {sig.get('pair')}: {name} already recorded -- skipped")
            continue
        pend = next((k for k, v in (sig.get("pending") or {}).items() if v.get("name") == name), None)
        uid = int(pend) if pend else None
        print(f"{token} {sig.get('pair')} {sig.get('direction')} ({sig.get('post_type')}): "
              f"{name} {verdict.upper()} -- {reason[:60]}...")
        if not apply:
            continue
        sig = notifier.record_vote(token, name, verdict, reason=reason, user_id=uid)
        if uid is not None:
            vl._clear_prompts(token, uid)
        vl._append_vote({
            "voted_at_et": datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S"),
            "voter": name, "verdict": verdict, "reason": reason, "signal_token": token,
            "post_type": sig.get("post_type") or "trade",
            **{k: sig.get(k, "") for k in vl.SIGNAL_FIELDS},
        })
        vl._refresh_tally(token)
        print("   recorded")
    if not apply:
        print("\nDry run. Run again with --apply to record these.")


if __name__ == "__main__":
    main()
