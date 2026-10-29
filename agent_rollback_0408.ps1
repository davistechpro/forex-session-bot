# Roll back the agent code to the exact version that worked at 4:08 AM,
# then show what the bot reads from .env (no secrets printed).
#   powershell -ExecutionPolicy Bypass -File .\agent_rollback_0408.ps1
$ErrorActionPreference = "Stop"
if (-not (Test-Path .\watch.py)) { Write-Host "STOP -- run this from the forex-session-bot folder"; exit 1 }
$enc = New-Object System.Text.UTF8Encoding($false)
$stamp = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item modules\agent_reviewer.py "modules\agent_reviewer.before_rollback_$stamp.py.bak"
Copy-Item test_agent_review.py "test_agent_review.before_rollback_$stamp.py.bak"
$a = @'
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


def _context_line(post_type: str, chat: str) -> str:
    now = datetime.now(NY_TZ).strftime("%a %b %d %Y, %I:%M %p ET")
    return f"CONTEXT: Posted {now} | Chat: {chat} | Type: {post_type.upper()}"


def review(post_text: str, chart_path=None, post_type: str = "setup", chat: str = "setups") -> str:
    """Ask the agent to grade one post. Returns its review text."""
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    content = [{"type": "text",
                "text": _context_line(post_type, chat) + "\n\n" + _plain(post_text)}]
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


def _run(sent_result, post_text, chart_path, post_type, chat, pair, signal_id):
    try:
        text = review(post_text, chart_path, post_type, chat)
    except Exception as e:
        print(f"AGENT: review failed for {pair} ({e})")
        return
    verdict = _verdict(text)
    reply = text if len(text) <= 3900 else text[:3890] + "\n..."
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
[IO.File]::WriteAllText((Join-Path (Get-Location) "modules\agent_reviewer.py"), $a.Replace("`r`n","`n"), $enc)
$t = @'
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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from modules import agent_reviewer, notifier  # noqa: E402


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
    print(f"Post: {pair} {sig.get('direction', '')} ({ptype}) | chart: {chart or 'none'}")

    if not args.send:
        print("\n--- agent review ---\n")
        print(agent_reviewer.review(sig["base_text"], chart, ptype, chat))
        return

    text = "[TEST -- agent review demo]\n" + sig["base_text"]
    sent = (notifier.send_photo(chart, text, chat="setups") if chart
            else notifier.send_message(text, chat="setups"))
    if not sent:
        raise SystemExit("STOP -- couldn't post the test message to the setups chat")
    print("Test post sent -- reviewing (this can take 20-60 seconds)...")
    agent_reviewer._run(sent, sig["base_text"], chart, ptype, chat, pair, None)
    print("Done -- check the setups chat.")


if __name__ == "__main__":
    main()
'@
[IO.File]::WriteAllText((Join-Path (Get-Location) "test_agent_review.py"), $t.Replace("`r`n","`n"), $enc)
Write-Host "Rolled back to the 4:08 AM version (backups saved as .bak)"
Write-Host ""
Write-Host "--- what the bot reads from .env ---"
py -c "from dotenv import load_dotenv; load_dotenv(); import os; print('RESOURCE =', repr(os.getenv('ANTHROPIC_FOUNDRY_RESOURCE'))); print('BASE_URL =', repr(os.getenv('ANTHROPIC_FOUNDRY_BASE_URL'))); print('API KEY LINE PRESENT =', bool(os.getenv('ANTHROPIC_FOUNDRY_API_KEY'))); print('MODEL =', os.getenv('AGENT_REVIEW_MODEL')); print('AZURE_CLIENT_ID SET =', bool(os.getenv('AZURE_CLIENT_ID'))); print('AZURE_CLIENT_SECRET SET =', bool(os.getenv('AZURE_CLIENT_SECRET')))"
Write-Host ""
Write-Host "--- same test that worked at 4:08 ---"
py test_agent_review.py
