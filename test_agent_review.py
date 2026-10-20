"""
Test the Trade Reviewer agent without waiting for the market.

  py test_agent_review.py
      Grades the most recent post the bot made (from signal_map.json) with
      the newest chart for that pair, and prints the review. Sends nothing.

  py test_agent_review.py --send
      Same, but re-posts that post to the SETUPS chat marked [TEST] and lets
      the agent reply under it, exactly like it will live.

  py test_agent_review.py --pair EUR_USD
      Use the most recent post for a specific pair.
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from modules import agent_reviewer, notifier  # noqa: E402


def chart_time(chart):
    """When the post went out = the chart file's timestamp (local desktop time = ET)."""
    m = re.search(r"(\d{8}_\d{6})", chart.name) if chart else None
    if not m:
        return None
    return agent_reviewer.NY_TZ.localize(datetime.strptime(m.group(1), "%Y%m%d_%H%M%S"))


def latest_post(pair=None):
    m = json.loads(Path("signal_map.json").read_text()) if Path("signal_map.json").exists() else {}
    rows = [(tok, s) for tok, s in m.items() if s.get("base_text") and (not pair or s.get("pair") == pair)]
    if not rows:
        raise SystemExit("STOP -- no posts with text found in signal_map.json"
                         + (f" for {pair}" if pair else ""))
    return rows[-1]


def newest_chart(pair):
    charts = sorted(Path("rendered_charts").glob(f"{pair}_1H_*.png")) or \
             sorted(Path("rendered_charts").glob(f"{pair}_*.png"))
    return charts[-1] if charts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair")
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()

    if not agent_reviewer.enabled():
        raise SystemExit("STOP -- agent not configured. Check AGENT_REVIEW=on and the "
                         "ANTHROPIC_FOUNDRY_* lines in .env")

    tok, sig = latest_post(args.pair)
    pair, post_type = sig.get("pair", ""), sig.get("post_type") or "trade"
    chat = "trades" if post_type == "trade" else "setups"
    ptype = "active" if post_type == "trade" else post_type
    chart = newest_chart(pair)
    posted = chart_time(chart)
    print(f"Post: {pair} {sig.get('direction', '')} ({ptype}) | chart: {chart or 'none'}"
          f" | posted: {posted.strftime('%a %b %d %I:%M %p ET') if posted else 'unknown'}")

    if not args.send:
        print("\n--- agent review ---\n")
        print(agent_reviewer.review(sig["base_text"], chart, ptype, chat, posted))
        return

    text = "[TEST -- agent review demo]\n" + sig["base_text"]
    sent = (notifier.send_photo(chart, text, chat="setups") if chart
            else notifier.send_message(text, chat="setups"))
    if not sent:
        raise SystemExit("STOP -- couldn't post the test message to the setups chat")
    print("Test post sent -- reviewing (this can take 20-60 seconds)...")
    agent_reviewer._run(sent, sig["base_text"], chart, ptype, chat, pair, None, posted)
    print("Done -- check the setups chat.")


if __name__ == "__main__":
    main()