# Agent Review setup -- run from the forex-session-bot folder:
#   cd C:\Users\devda\Downloads\forex-session-bot
#   powershell -ExecutionPolicy Bypass -File .\agent_review_setup.ps1
# Adds the Trade Reviewer agent: every setup / pending / active post gets an
# agent reply underneath it in Telegram. Stops and changes nothing if
# watch.py isn't the version it expects.
$ErrorActionPreference = "Stop"
if (-not (Test-Path .\watch.py)) { Write-Host "STOP -- run this from the forex-session-bot folder"; exit 1 }
$enc = New-Object System.Text.UTF8Encoding($false)
function Save($path, $text) { [IO.File]::WriteAllText((Join-Path (Get-Location) $path), $text.Replace("`r`n", "`n"), $enc); Write-Host "$path written" }
New-Item -ItemType Directory -Force -Path .\agent | Out-Null

# --- 1. check + edit watch.py first (stops here if anchors are missing)
$editPy = @'
from pathlib import Path
def edit(path, pairs):
    p = Path(path); s = p.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    for a, b in pairs:
        if s.count(a) != 1:
            raise SystemExit(f'STOP -- not found exactly once in {path}: {a[:60]!r}')
        s = s.replace(a, b)
    p.write_text(s, encoding='utf-8')
    print(path, 'updated')

if 'agent_reviewer' in Path('watch.py').read_text(encoding='utf-8-sig'):
    print('watch.py already has the agent hook -- leaving it')
else:
  edit('watch.py', [
    ('from modules import heartbeat\n',
     'from modules import heartbeat\nfrom modules import agent_reviewer\n'),
    ('        print("  pending posted"',
     '        agent_reviewer.review_async(ok, text, chart, "pending", "setups", pair, sid)\n        print("  pending posted"'),
    ('        print("  setup posted"',
     '        agent_reviewer.review_async(ok, text, chart, "setup", "setups", pair, sid)\n        print("  setup posted"'),
    ('        print("  trade alert sent"',
     '        agent_reviewer.review_async(ok, text, chart, "active", "trades", pair, sid)\n        print("  trade alert sent"'),
])
g = Path('.gitignore'); s = g.read_text(encoding='utf-8')
if 'agent_reviews.csv' not in s:
    g.write_text(s.rstrip('\n') + '\nagent_reviews.csv\n', encoding='utf-8'); print('.gitignore updated')
'@
Save "_edit_agent_tmp.py" $editPy
py _edit_agent_tmp.py
if ($LASTEXITCODE -ne 0) { Remove-Item _edit_agent_tmp.py; Write-Host "Nothing changed."; exit 1 }
Remove-Item _edit_agent_tmp.py

# --- 2. new files
$f1 = @'
# Trade Reviewer Agent: Instructions (draft v0.5)

## Who you are
You are the quality-assurance checker for the OPT trading bot, which posts Forex trades for "Jordan's Strategy" to Telegram. The team is Devin, Gage and Steven. Your job is to grade each post the bot makes as VALID, INVALID or UNSURE against the strategy, and to explain why in plain trader language. The team will confirm or override your verdict. Their votes are the final word.

## Ground rules
- Your only sources of truth are the attached strategy document (Jordans-Strategy-v3) and the handoff document (OPT-Bot-Agent-Handoff). If a post and the docs disagree, the docs win.
- Never invent a rule, number or exception. If something isn't covered by the docs, answer UNSURE and name the open question.
- Grade only what you can see. If the post or chart doesn't show enough to check a rule, mark that check "can't verify". Don't guess.
- These are technical traders. Don't bring news or fundamentals into a verdict.

## What you receive
Every message starts with a context line, like this:
`CONTEXT: Posted <day, date, time ET> | Chat: <setups or trades> | Type: <SETUP, PENDING or ACTIVE>`
That is followed by the bot's Telegram post text and the chart screenshot. Treat the context line as fact. If it's missing, say so in "Can't verify".

## Grade by post type
- **SETUP** (a zone qualifies but hasn't triggered): run checks 1 to 5 only. For check 5, judge whether the bot is waiting for the **right kind** of trigger for this zone. A zone formed today means a wick tap; a zone from an earlier day means the 1m confirmation. Checks 6 and 7 don't apply, so don't list them as "can't verify".
- **PENDING** (triggered, entry not hit yet): run all 7 checks. It should be in the setups chat.
- **ACTIVE** (entry hit): run all 7 checks. It should be in the trades chat.

## Reading the chart
Describe what you actually see before judging: the zone box, the candles around it, and any wick that taps or closes through it. If you can't see an image, say "NO IMAGE RECEIVED" on the first line.

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
Your reply goes straight into the team's Telegram chat, under the bot's post. You're one of the boys in the group chat: Devin, Gage and Steven are your guys. Talk like a bro texting the homies about a chart: laid back, hyped when a setup's clean, real when it's not. Run every check in your head, but don't print a checklist.

- Open with a quick greeting to the crew, and switch it up every time so it doesn't get repetitive: "What's up G's", "Yo fellas", "Aight boys", "Ayo", "Sup gang", "Bro, peep this".
- Plain text only: no markdown, bullets, numbered lists, headers or code blocks.
- 3 to 6 short sentences, split into 2 or 3 short paragraphs. Use natural slang ("this one's clean", "lowkey sketchy", "no cap", "I'm not feeling this one", "we chillin", "bro"), but keep it readable. No cursing.
- Lead with what you see on the chart, then say whether it holds up and why. Always name the exact level, candle or time. The vibe is casual, but the read has to be precise.
- If something fails, call it out straight: say which rule and why. If you couldn't check something (like the Daily or 4H trend from a 1H chart), mention it in one natural line.
- No "As an AI", no corporate talk, no emojis.
- Never let the vibe change the grade. A clean-sounding message about a broken trade is still INVALID.
- The very last line is always exactly one of these, on its own line:
Verdict: VALID
Verdict: INVALID
Verdict: UNSURE
You can add a few words after it in brackets, e.g. "Verdict: VALID (peep the entry though)".

Style example only. Never reuse its numbers or its exact words:
What's up G's. GU short off the 1H supply at 1.3319-1.3332, and bro, that green candle at 1 PM got smacked straight down. Price hasn't been back since, so the zone's still fresh, no cap.

Posted 11 AM Monday, so we're inside the window. Zone formed this morning, so waiting on the 15m/5m tap is the right play.

Only thing lowkey bugging me: the planned entry at 1.3318 is sitting under the box. For a short I'd want it inside, around that 15m at 1.3321. Can't see the Daily/4H from here, so I'm trusting the bot on the trend.
Verdict: VALID (peep the entry though)
'@
Save "agent\trade_reviewer.md" $f1

$f2 = @'
"""
Agent Reviewer -- the Trade Reviewer agent grades every bot post and replies
under it in Telegram with what it sees, a check-by-check read, and a verdict.

Model: Claude (Opus 5.5 by default) deployed in Devin's Microsoft Foundry.
Instructions: agent/trade_reviewer.md (edit that file to change how it grades).

Runs in a background thread so a slow model reply never holds up the scan.
Every verdict is also logged to agent_reviews.csv, so agent-vs-team
agreement can be measured later.

.env settings:
  AGENT_REVIEW=on                       turn it on (anything else = off)
  ANTHROPIC_FOUNDRY_API_KEY=...         Foundry deployment Key, OR (if API keys
                                        are disabled) an Entra app login:
  AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
  ANTHROPIC_FOUNDRY_RESOURCE=...        Foundry resource name (or use
  ANTHROPIC_FOUNDRY_BASE_URL=...        the full .../anthropic/ URL instead)
  AGENT_REVIEW_MODEL=claude-opus-5-5    deployment name, if you changed it
  AGENT_SIGNATURE=...                   sign-off line under every review (optional)
"""
import base64
import csv
import html
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import pytz

from modules import notifier

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_FILE = ROOT / "agent" / "trade_reviewer.md"
LOG_FILE = ROOT / "agent_reviews.csv"
NY_TZ = pytz.timezone("America/New_York")
LOG_FIELDS = ["reviewed_at", "post_type", "chat", "pair", "signal_token", "verdict", "review"]

_lock = threading.Lock()
_client = None


def enabled() -> bool:
    if os.getenv("AGENT_REVIEW", "").strip().lower() not in ("on", "1", "true", "yes"):
        return False
    if not (os.getenv("ANTHROPIC_FOUNDRY_API_KEY") or os.getenv("AZURE_CLIENT_ID")):
        print("AGENT: set ANTHROPIC_FOUNDRY_API_KEY, or the AZURE_TENANT_ID / AZURE_CLIENT_ID / "
              "AZURE_CLIENT_SECRET app login -- skipping")
        return False
    if not (os.getenv("ANTHROPIC_FOUNDRY_RESOURCE") or os.getenv("ANTHROPIC_FOUNDRY_BASE_URL")):
        print("AGENT: set ANTHROPIC_FOUNDRY_RESOURCE (or _BASE_URL) -- skipping")
        return False
    return True


def _get_client():
    global _client
    if _client is None:
        from anthropic import AnthropicFoundry  # reads the ANTHROPIC_FOUNDRY_* env vars
        if os.getenv("ANTHROPIC_FOUNDRY_API_KEY"):
            _client = AnthropicFoundry()
        else:
            # Entra ID: app registration (AZURE_TENANT_ID / AZURE_CLIENT_ID /
            # AZURE_CLIENT_SECRET in .env) with the Foundry User role on the resource.
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            _client = AnthropicFoundry(azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), "https://ai.azure.com/.default"))
    return _client


def _plain(text: str) -> str:
    """Telegram HTML -> plain text for the model."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _context_line(post_type: str, chat: str, posted_at=None) -> str:
    now = (posted_at or datetime.now(NY_TZ)).strftime("%a %b %d %Y, %I:%M %p ET")
    return f"CONTEXT: Posted {now} | Chat: {chat} | Type: {post_type.upper()}"


def review(post_text: str, chart_path=None, post_type: str = "setup", chat: str = "setups",
           posted_at=None) -> str:
    """Ask the agent to grade one post. Returns its review text."""
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    content = [{"type": "text",
                "text": _context_line(post_type, chat, posted_at) + "\n\n" + _plain(post_text)}]
    if chart_path and Path(chart_path).exists():
        p = Path(chart_path)
        media = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.b64encode(p.read_bytes()).decode()}})
    else:
        content[0]["text"] += "\n\n(No chart image for this post.)"
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
        with LOG_FILE.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in LOG_FIELDS})


def _run(sent_result, post_text, chart_path, post_type, chat, pair, signal_id, posted_at=None):
    try:
        text = review(post_text, chart_path, post_type, chat, posted_at)
    except Exception as e:
        print(f"AGENT: review failed for {pair} ({e})")
        return
    verdict = _verdict(text)
    sign = os.getenv("AGENT_SIGNATURE", "-- OPT Agent (AI trade review)")
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
        "verdict": verdict, "review": text,
    })
    print(f"AGENT: {pair} {post_type} -> {verdict}")


def review_async(sent_result, post_text, chart_path=None, post_type="setup",
                 chat="setups", pair="", signal_id=None):
    """Fire-and-forget: grade the post that was just sent and reply under it."""
    if not sent_result or not isinstance(sent_result, dict) or not enabled():
        return
    threading.Thread(
        target=_run,
        args=(sent_result, post_text, chart_path, post_type, chat, pair, signal_id),
        daemon=True,
    ).start()
'@
Save "modules\agent_reviewer.py" $f2

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
'@
Save "test_agent_review.py" $f3

# --- 3. SDK with Foundry support + compile check
py -m pip install -U anthropic azure-identity --quiet
py -m py_compile watch.py modules\agent_reviewer.py test_agent_review.py
if ($LASTEXITCODE -ne 0) { Write-Host "STOP -- compile error, send Claude the output"; exit 1 }
py -c "from anthropic import AnthropicFoundry; print('Foundry SDK OK')"
Write-Host ""
Write-Host "DONE. Next: add the AGENT_REVIEW lines to .env, then run: py test_agent_review.py"
