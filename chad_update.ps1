# Chad update -- run from the forex-session-bot folder:
#   cd C:\Users\devda\Downloads\forex-session-bot
#   powershell -ExecutionPolicy Bypass -File .\chad_update.ps1
# 1. Chad gets the bot's own numbers (BOT FACTS) for every post, so he grades
#    the rules instead of guessing times off the chart (chart axis is UTC).
# 2. New grading standard: INVALID only when a rule clearly fails.
# 3. Clean, professional voice.
# 4. Fixes watch.py: an older setup script had added Chad 4 times, so every
#    post was being reviewed 4 times. Now exactly once.
$ErrorActionPreference = "Stop"
if (-not (Test-Path .\watch.py)) { Write-Host "STOP -- run this from the forex-session-bot folder"; exit 1 }
$enc = New-Object System.Text.UTF8Encoding($false)
function Save($path, $text) { [IO.File]::WriteAllText((Join-Path (Get-Location) $path), $text.Replace("`r`n", "`n"), $enc); Write-Host "$path written" }

$e = @'
import re
from pathlib import Path
p = Path('watch.py'); s = p.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
# 1. one import (an older setup script added it several times)
s, n = re.subn(r'(from modules import agent_reviewer\n)+', 'from modules import agent_reviewer\n', s)
if n != 1:
    raise SystemExit('STOP -- expected the agent_reviewer import in watch.py; nothing changed')
# 2. one review call per post type, now with the bot's facts
for ptype, chat in (('pending', 'setups'), ('setup', 'setups'), ('active', 'trades')):
    pat = (r'(        agent_reviewer\.review_async\(ok, text, chart, "' + ptype + r'", "' + chat +
           r'", pair, sid(?:, facts=agent_reviewer\.facts_from\(result\))?\)\n)+')
    new = (f'        agent_reviewer.review_async(ok, text, chart, "{ptype}", "{chat}", pair, sid, '
           f'facts=agent_reviewer.facts_from(result))\n')
    s, n = re.subn(pat, new, s)
    if n != 1:
        raise SystemExit(f'STOP -- {ptype} review call not found in watch.py; nothing changed')
p.write_text(s, encoding='utf-8')
print('watch.py updated: one review per post, with bot facts')
'@
Save "_edit_facts_tmp.py" $e
py _edit_facts_tmp.py
$rc = $LASTEXITCODE
Remove-Item _edit_facts_tmp.py
if ($rc -ne 0) { Write-Host "Nothing changed."; exit 1 }

$f1 = @'
"""
Agent Reviewer (Chad) -- grades every bot post and replies under it in Telegram.

Model: Claude (Opus 5.5) deployed in Devin's Microsoft Foundry.
Instructions: agent/trade_reviewer.md (edit that file to change how it grades).

Chad gets three things for each post:
  1. the post text,
  2. the chart image,
  3. BOT FACTS -- the bot's own numbers (trend per timeframe, zone times in ET,
     when the zone became ready, trigger time, entry plan). This is what lets
     him check the rules exactly instead of guessing times off the chart.

Runs in a background thread so a slow reply never holds up the scan. Every
verdict is logged to agent_reviews.csv (with the facts it was given), so
agent-vs-team agreement can be measured.

.env settings:
  AGENT_REVIEW=on                       turn it on (anything else = off)
  ANTHROPIC_FOUNDRY_RESOURCE=...        Foundry resource name
  AGENT_REVIEW_MODEL=...                deployment name
  AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET   Entra app login
  ANTHROPIC_FOUNDRY_API_KEY=...         only if key login is enabled (it isn't)
  AGENT_SIGNATURE=-- Chad               sign-off line (optional)
"""
import base64
import csv
import html
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

from modules import notifier

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_FILE = ROOT / "agent" / "trade_reviewer.md"
LOG_FILE = ROOT / "agent_reviews.csv"
NY_TZ = pytz.timezone("America/New_York")
LOG_FIELDS = ["reviewed_at", "post_type", "chat", "pair", "signal_token", "verdict", "review", "facts"]
TF_MIN = {"H4": 240, "4H": 240, "H1": 60, "1H": 60, "15": 15, "5": 5, "1": 1}

_lock = threading.Lock()
_client = None


# ------------------------------------------------------------------ setup

def _clean_placeholders():
    """Treat leftover template values like '<the Key>' as not set."""
    for k in ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL"):
        v = os.getenv(k, "").strip()
        if not v or v.startswith("<"):
            os.environ.pop(k, None)


def enabled() -> bool:
    _clean_placeholders()
    if os.getenv("AGENT_REVIEW", "").strip().lower() not in ("on", "1", "true", "yes"):
        return False
    if not (os.getenv("ANTHROPIC_FOUNDRY_API_KEY") or os.getenv("AZURE_CLIENT_ID")):
        print("AGENT: no login -- need the AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET "
              "lines in .env (run agent_login_setup.ps1) -- skipping")
        return False
    if not (os.getenv("ANTHROPIC_FOUNDRY_RESOURCE") or os.getenv("ANTHROPIC_FOUNDRY_BASE_URL")):
        print("AGENT: set ANTHROPIC_FOUNDRY_RESOURCE in .env -- skipping")
        return False
    return True


def _get_client():
    global _client
    if _client is None:
        from anthropic import AnthropicFoundry  # reads the ANTHROPIC_FOUNDRY_* env vars
        if os.getenv("ANTHROPIC_FOUNDRY_API_KEY"):
            _client = AnthropicFoundry()
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            _client = AnthropicFoundry(azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), "https://ai.azure.com/.default"))
    return _client


# ------------------------------------------------------------------ bot facts

def _et(ts) -> str:
    if ts is None or ts == "":
        return "n/a"
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return t.tz_convert(NY_TZ).strftime("%a %b %d %I:%M %p ET")
    except Exception:
        return str(ts)


def facts_from(result: dict) -> str:
    """The bot's own numbers for this post, in plain lines, all times in ET."""
    try:
        lines = []
        tr = []
        for name, key in (("Daily", "daily_trend"), ("4H", "h4_trend"), ("1H", "h1_trend")):
            tr.append(f"{name} {(result.get(key) or {}).get('trend', 'n/a')}")
        lines.append("Trend by timeframe: " + ", ".join(tr))
        lines.append(f"Direction the bot chose: {result.get('valid_direction')} ({result.get('deciding_tf')})")

        z, ztf = result.get("zone") or {}, result.get("zone_source_tf")
        if z:
            lines.append(f"Zone: {str(z.get('type', '')).upper()} {z.get('bottom')}-{z.get('top')} on the {ztf}")
            lines.append(f"Zone origin candle opened: {_et(z.get('origin_time'))}")
            ct = z.get("confirmed_time")
            lines.append(f"Zone confirming candle opened: {_et(ct)}")
            mins = TF_MIN.get(str(ztf), 0)
            if ct is not None and mins:
                ready = pd.Timestamp(ct) + pd.Timedelta(minutes=2 * mins)
                lines.append(f"Zone became tradeable (confirming candle closed + next {ztf} candle closed): {_et(ready)}")
            lines.append(f"Zone mitigated: {z.get('mitigated', False)}")

        e = result.get("entry")
        s = result.get("setup") or {}
        if e:
            lines.append(f"Trigger: {e.get('method')} at {_et(e.get('time'))}")
            if e.get("confirm_time") is not None:
                lines.append(f"1m confirm close: {_et(e.get('confirm_time'))}, breakout: {_et(e.get('breakout_time'))}")
            lines.append(f"Entry plan: {e.get('direction')} at {e.get('entry_price')} ({e.get('entry_level')} level), "
                         f"stop {e.get('stop_loss')} ({e.get('sl_pips')} pips), target {e.get('take_profit')} "
                         f"({e.get('tp_pips')} pips)")
            if e.get("filled_at") is not None:
                lines.append(f"Entry filled (1m touched entry): {_et(e.get('filled_at'))}")
        elif s:
            lines.append(f"Waiting for: {s.get('waiting_for')}")
            if s.get("planned_entry") is not None:
                lines.append(f"Planned if it triggers: entry {s.get('planned_entry')} ({s.get('planned_level')} level), "
                             f"stop {s.get('planned_sl')} ({s.get('planned_sl_pips')} pips), "
                             f"target {s.get('planned_tp')}")
        lines.append("Chart image time labels are UTC (ET = UTC minus 4 hours). Use the ET times above.")
        return "\n".join(lines)
    except Exception as ex:
        return f"(bot facts unavailable: {ex})"


# ------------------------------------------------------------------ review

def _plain(text: str) -> str:
    """Telegram HTML -> plain text for the model."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _context_line(post_type: str, chat: str, posted_at=None) -> str:
    now = (posted_at or datetime.now(NY_TZ)).strftime("%a %b %d %Y, %I:%M %p ET")
    return f"CONTEXT: Posted {now} | Chat: {chat} | Type: {post_type.upper()}"


def review(post_text: str, chart_path=None, post_type: str = "setup", chat: str = "setups",
           posted_at=None, facts: str = None) -> str:
    """Ask the agent to grade one post. Returns its review text."""
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    body = _context_line(post_type, chat, posted_at) + "\n\n" + _plain(post_text)
    body += "\n\nBOT FACTS:\n" + (facts or "(none provided for this post)")
    content = [{"type": "text", "text": body}]
    if chart_path and Path(chart_path).exists():
        p = Path(chart_path)
        media = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.b64encode(p.read_bytes()).decode()}})
    else:
        content[-1]["text"] += "\n\n(No chart image for this post.)"
    msg = _get_client().messages.create(
        model=os.getenv("AGENT_REVIEW_MODEL", "claude-opus-5-5"),
        max_tokens=int(os.getenv("AGENT_REVIEW_MAX_TOKENS", "8000")),
        system=instructions,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if not text:
        kinds = [getattr(b, "type", "?") for b in msg.content]
        raise RuntimeError(f"no text in reply (stop_reason={msg.stop_reason}, "
                           f"blocks={kinds}, usage={msg.usage})")
    return text


def _verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*(VALID|INVALID|UNSURE)", text or "", re.I)
    return m.group(1).upper() if m else "UNKNOWN"


def _log(row: dict):
    with _lock:
        new = not LOG_FILE.exists()
        if not new:
            with LOG_FILE.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh), [])
            if "facts" not in header:          # older log without the facts column
                LOG_FILE.replace(LOG_FILE.with_name("agent_reviews_old.csv"))
                new = True
        with LOG_FILE.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in LOG_FIELDS})


def _run(sent_result, post_text, chart_path, post_type, chat, pair, signal_id,
         posted_at=None, facts=None):
    text = None
    for attempt in range(1, 4):
        try:
            text = review(post_text, chart_path, post_type, chat, posted_at, facts)
            break
        except Exception as e:
            print(f"AGENT: review attempt {attempt}/3 failed for {pair} ({type(e).__name__}: {e})")
            if attempt < 3:
                time.sleep(15 * attempt)
    if text is None:
        print(f"AGENT: giving up on {pair} -- post went out without a review")
        return
    verdict = _verdict(text)
    sign = os.getenv("AGENT_SIGNATURE", "-- Chad")
    reply = text if len(text) <= 3800 else text[:3790] + "\n..."
    reply += "\n\n" + sign
    notifier._call("sendMessage", {
        "chat_id": sent_result["chat"]["id"],
        "text": reply,
        "reply_to_message_id": sent_result["message_id"],
        "disable_notification": True,
    })
    _log({
        "reviewed_at": datetime.now(NY_TZ).isoformat(timespec="seconds"),
        "post_type": post_type, "chat": chat, "pair": pair,
        "signal_token": notifier.short_token(signal_id) if signal_id else "",
        "verdict": verdict, "review": text, "facts": facts or "",
    })
    print(f"AGENT: {pair} {post_type} -> {verdict}")


def review_async(sent_result, post_text, chart_path=None, post_type="setup",
                 chat="setups", pair="", signal_id=None, facts=None):
    """Fire-and-forget: grade the post that was just sent and reply under it."""
    if not sent_result or not isinstance(sent_result, dict) or not enabled():
        return
    if signal_id and facts:
        try:   # keep the facts with the post so test_agent_review.py can replay it
            notifier.update_signal(notifier.short_token(signal_id), agent_facts=facts)
        except Exception:
            pass
    threading.Thread(
        target=_run,
        args=(sent_result, post_text, chart_path, post_type, chat, pair, signal_id, None, facts),
        daemon=True,
    ).start()
'@
Save "modules\agent_reviewer.py" $f1

$f2 = @'
# Trade Reviewer Agent (Chad): Instructions (v0.6)

## Who you are
You are Chad, the quality-assurance reviewer for the OPT trading bot, which posts Forex setups and trades for "Jordan's Strategy" to Telegram. The team is Devin, Gage and Steven. For each post, you check whether the bot applied the strategy correctly, give a verdict (VALID, INVALID or UNSURE), and explain it in plain trader language. The team confirms or overrides you. Their votes are the final word.

## Ground rules
- The rules in this document are the strategy. They come from Jordan's Strategy v3 and the team's decisions on their calls. Never invent a rule, number or exception.
- These are technical traders. Keep news and fundamentals out of the verdict.

## What you receive
1. A context line: `CONTEXT: Posted <day, date, time ET> | Chat: <setups or trades> | Type: <SETUP, PENDING or ACTIVE>`. Treat it as fact.
2. The bot's Telegram post text.
3. BOT FACTS: the bot's own numbers from its market data. These are the trend on each timeframe, the zone and its times in ET, when the zone became tradeable, the trigger time, and the entry plan. Treat these numbers as accurate data. Your job is to check whether the bot applied the rules to them correctly.
4. A 1H chart image. The time labels on the chart are in UTC (ET is UTC minus 4 hours). Always use the ET times from BOT FACTS and never read times off the chart axis. Use the chart to sanity-check the picture: does the zone box sit where the facts say, and did price come back into it or close through it?

## How to grade (read this carefully)
- Default to VALID when the facts satisfy the rules. A post is INVALID only when a specific rule clearly fails based on the BOT FACTS or something clearly visible on the chart. Name the rule and the exact number or time that breaks it.
- Something you can't see is never a failure. If you can't see the Daily or 4H chart, use the trend in BOT FACTS. If something truly can't be checked, mention it in one line and still grade on everything else.
- If the facts are consistent with the rules but you have a real doubt, or it touches one of the open questions below, the verdict is UNSURE, and you say exactly what the team needs to decide.
- Do not mark a post INVALID because a time "looks" wrong on the chart axis. The chart is in UTC.
- Grade by post type:
  - **SETUP** (zone qualifies, not triggered yet): checks 1 to 5 only. For check 5, judge whether the bot is waiting for the right kind of trigger: a zone formed today between 6 AM and 1 PM ET means a 15m/5m wick tap; a zone from an earlier day means the 1m confirmation sequence. A setup's planned entry is only a preview; mention it if it looks off, but don't fail the setup for it.
  - **PENDING** (triggered, entry not hit yet): all 7 checks. Belongs in the setups chat.
  - **ACTIVE** (entry hit): all 7 checks. Belongs in the trades chat.

## Checklist: run every check, in order
1. **Session.** Was it posted 9:00 AM to 12:59 PM ET, Monday through Thursday? No Fridays, no weekends, no bank holidays for either currency.
2. **Direction.** Do 2 of the 3 timeframes (Daily, 4H, 1H) agree? A bullish majority means LONG from a demand zone. A bearish majority means SHORT from a supply zone. If they don't agree, there's no trade.
3. **Zone.** Is it a real push-pattern zone? That means an opposite-colored origin candle, followed by 1 to 3 candles closing beyond it. Is it still fresh, meaning no wick retest from the far side and no body close through it?
4. **Zone ready.** Did the confirming candle close, and then the next candle of the zone's own timeframe also close, before price came back and triggered? (Next 1H candle for a 1H zone, next 4H candle for a 4H zone.)
5. **Trigger.**
   - For a 4H or 1H zone formed today between 6 AM and 1 PM ET, the trigger is a wick tap into the zone on the 15m or 5m chart. Up to 20 pips of overshoot is allowed.
   - For an untouched zone from an earlier day, up to 7 days old, the trigger is the 1-minute confirmation sequence. A 1m body closes inside the zone, a later 1m candle closes beyond it, then price returns to tag the zone.
   - The trigger must be within the last 120 minutes.
6. **Entry and stop.**
   - **Entry:** it should be the first smaller zone (15m, 5m or 1m) that price reaches inside the big zone. For demand that's the highest one; for supply, the lowest.
   - **Stop:** use 8 pips if that clears the NEXT smaller zone. Otherwise use 10 pips if that clears it. Otherwise move the entry to that next zone and repeat.
   - **Target:** take profit is 2R.
   - **Confirmation trades:** the entry is at the newest 1m zone, with an 8-pip stop and a 16-pip target.
7. **Chat.** A trade in the trades chat must show that the entry was actually hit (ACTIVE). If the entry hasn't been hit yet, it belongs in the setups chat as PENDING.

## Known open questions: answer UNSURE, don't decide these
- Does the trend lock at 9 AM for the session, or update as candles close during the session?
- When a 4H and a 1H zone overlap, is it one trade or two? (The 1H zone should still be drawn on the chart.)
- Do smaller zones that form after the tap count for picking the entry?

## How to write your reply
Your reply goes straight into the team's Telegram chat, under the bot's post. Write like a sharp, friendly colleague on the trading desk: clear, professional and conversational. Plain English, no slang, no hype.

- Start with the pair and what the setup is, in one sentence (e.g. "GBP/USD short off the 1H supply at 1.3319-1.3332.").
- Then 2 to 5 short sentences: what you see, whether the key rules hold, and anything the team should look at. Name exact levels and ET times.
- Split it into 2 or 3 short paragraphs. Plain text only: no markdown, bullets, numbered lists, headers, code blocks or emojis.
- No greetings, no filler, no "As an AI".
- If it fails, say which rule and the exact number or time that breaks it.
- The very last line is always exactly one of these, on its own line:
Verdict: VALID
Verdict: INVALID
Verdict: UNSURE
You can add a few words after it in brackets, e.g. "Verdict: VALID (check the planned entry)".

Style example only. Never reuse its numbers or wording:
GBP/USD short off the 1H supply at 1.3319-1.3332, formed Monday at 9:00 AM ET. The zone became tradeable at 11:00 AM and price hasn't come back into it, so it's still fresh.

Posted at 11:00 AM, inside the session window. Daily and 4H are bearish, so the short is with the trend, and since the zone formed this morning, waiting for a 15m/5m wick tap is the right trigger.

One thing to check: the planned entry at 1.3318 sits just under the zone. I'd expect the 15m level at 1.3321.
Verdict: VALID (check the planned entry)
'@
Save "agent\trade_reviewer.md" $f2

$f3 = @'
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
    if not sig.get("agent_facts"):
        print("NOTE: this post is from before Chad got BOT FACTS -- he will grade it from the picture only.")
    print(f"Post: {pair} {sig.get('direction', '')} ({ptype}) | chart: {chart or 'none'}"
          f" | posted: {posted.strftime('%a %b %d %I:%M %p ET') if posted else 'unknown'}")

    if not args.send:
        print("\n--- agent review ---\n")
        print(agent_reviewer.review(sig["base_text"], chart, ptype, chat, posted, sig.get("agent_facts")))
        return

    text = "[TEST -- agent review demo]\n" + sig["base_text"]
    sent = (notifier.send_photo(chart, text, chat="setups") if chart
            else notifier.send_message(text, chat="setups"))
    if not sent:
        raise SystemExit("STOP -- couldn't post the test message to the setups chat")
    print("Test post sent -- reviewing (this can take 20-60 seconds)...")
    agent_reviewer._run(sent, sig["base_text"], chart, ptype, chat, pair, None, posted, sig.get("agent_facts"))
    print("Done -- check the setups chat.")


if __name__ == "__main__":
    main()
'@
Save "test_agent_review.py" $f3

py -m py_compile watch.py modules\agent_reviewer.py test_agent_review.py
if ($LASTEXITCODE -ne 0) { Write-Host "STOP -- compile error, send Claude the output"; exit 1 }
Write-Host ""
Write-Host "DONE. Restart watch.py (Ctrl+C, then: py watch.py). Chad's next review will use BOT FACTS."
