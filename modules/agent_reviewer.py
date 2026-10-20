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