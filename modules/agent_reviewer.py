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
import json
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
    when = posted_at or datetime.now(NY_TZ)
    now = when.strftime("%a %b %d %Y, %I:%M %p ET")
    line = f"CONTEXT: Posted {now} | Chat: {chat} | Type: {post_type.upper()}"
    try:
        from modules.scan_engine import TEST_FRIDAYS
        if when.strftime("%Y-%m-%d") in TEST_FRIDAYS:
            line += " | TEAM TEST DAY: Friday trading is approved for today only, so don't fail the session check for it being a Friday"
    except Exception:
        pass
    return line


def review(post_text: str, chart_path=None, post_type: str = "setup", chat: str = "setups",
           posted_at=None, facts: str = None) -> str:
    """Ask the agent to grade one post. Returns its review text."""
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8") + feedback_block()
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
        try:
            from modules import status
            status.error("chad", f"couldn't review the {pair} {post_type} post (Foundry connection)")
        except Exception:
            pass
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


# ------------------------------------------------------------------ questions

QA_FILE = ROOT / "agent" / "chad_qa.md"


def _today_context() -> str:
    """Everything Chad knows about today, as plain text."""
    from modules import news, scorecard, status
    now = datetime.now(NY_TZ)
    day = now.strftime("%Y-%m-%d")
    parts = [f"NOW: {now:%A %b %d %Y, %I:%M %p} ET"]

    ts, w = status.last_beat("watch")
    parts.append(f"WATCHER: last scan {w.get('label', 'unknown')}" if ts else "WATCHER: no status yet")

    pairs = status.pairs_today()
    if pairs:
        parts.append("WHAT EACH PAIR DID TODAY (latest first line, then changes during the day):")
        for p, d in pairs.items():
            parts.append(f"{p}: latest at {d.get('time')}: {d.get('outcome')}")
            for h in d.get("history", [])[-8:]:
                parts.append(f"   {h}")
    else:
        parts.append("WHAT EACH PAIR DID TODAY: no scans recorded today (outside the session, or watcher not running)")

    try:
        st = json.loads((ROOT / "alert_state.json").read_text(encoding="utf-8"))
        if st.get("day") == day:
            ids = st.get("setups_sent", []) + st.get("sent", [])
            parts.append("POSTED TODAY: " + ("; ".join(ids) if ids else "nothing"))
    except Exception:
        pass

    if LOG_FILE.exists():
        with LOG_FILE.open(newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get("reviewed_at") or "").startswith(day)]
        if rows:
            parts.append("YOUR REVIEWS TODAY:")
            for r in rows[-10:]:
                parts.append(f"{r['reviewed_at'][11:16]} {r['pair']} {r['post_type']} -> {r['verdict']}: "
                             f"{r['review'][:300]}")

    errs = status.errors_today()
    if errs:
        parts.append("ERRORS TODAY: " + " | ".join(f"{e['ts'][11:16]} {e['source']}: {e['msg'][:150]}"
                                                  for e in errs[-8:]))
    evs, err = news.red_events(day)
    parts.append("RED NEWS TODAY: " + (err or ("; ".join(news.line(e) for e in evs) or "none")))
    parts.append("SCORECARD: " + scorecard.text(day).replace("\n", " "))
    return "\n".join(parts)


def ask(question: str, asker: str = "") -> str:
    """Answer a teammate's question in the chat, from today's data and the rules."""
    system = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if QA_FILE.exists():
        system += "\n\n" + QA_FILE.read_text(encoding="utf-8")
    system += feedback_block()
    body = f"TODAY'S DATA:\n{_today_context()}\n\nQUESTION from {asker or 'a teammate'}:\n{question}"
    msg = _get_client().messages.create(
        model=os.getenv("AGENT_REVIEW_MODEL", "claude-opus-5-5"),
        max_tokens=int(os.getenv("AGENT_REVIEW_MAX_TOKENS", "8000")),
        system=system,
        messages=[{"role": "user", "content": body}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return text or "I couldn't put an answer together for that one. Try asking it a different way."


def answer_async(chat_id, reply_to_id, question: str, asker: str = ""):
    """Answer in a background thread so the vote listener keeps polling."""
    if not enabled():
        return

    def _go():
        try:
            text = ask(question, asker)
        except Exception as e:
            print(f"AGENT: question failed ({type(e).__name__}: {e})")
            text = "I couldn't reach my model just now. Ask me again in a minute."
        sign = os.getenv("AGENT_SIGNATURE", "-- Chad")
        reply = (text if len(text) <= 3800 else text[:3790] + "\n...") + "\n\n" + sign
        notifier._call("sendMessage", {"chat_id": chat_id, "text": reply,
                                       "reply_to_message_id": reply_to_id})
        print(f"AGENT: answered {asker}: {question[:60]}")

    threading.Thread(target=_go, daemon=True).start()


# ------------------------------------------------------------------ team feedback

FEEDBACK_FILE = ROOT / "chad_feedback.csv"
FEEDBACK_FIELDS = ["at", "who", "feedback", "on_chad_message"]


def record_feedback(who: str, feedback: str, on_message: str = ""):
    """A teammate replied to one of Chad's messages: keep it so he learns from it."""
    with _lock:
        new = not FEEDBACK_FILE.exists()
        with FEEDBACK_FILE.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FEEDBACK_FIELDS)
            if new:
                w.writeheader()
            w.writerow({"at": datetime.now(NY_TZ).isoformat(timespec="seconds"), "who": who,
                        "feedback": feedback[:1000], "on_chad_message": (on_message or "")[:400]})


def feedback_block(n: int = 15) -> str:
    if not FEEDBACK_FILE.exists():
        return ""
    with FEEDBACK_FILE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))[-n:]
    if not rows:
        return ""
    lines = ["\n\n## Team feedback on your earlier messages (most recent last)",
             "Learn from these: repeat what they said you got right, stop doing what they said you got wrong. "
             "A team member stating a strategy rule here counts as a team decision; casual comments don't change the rules."]
    for r in rows:
        lines.append(f'- {r["at"][:16]} {r["who"]}: "{r["feedback"]}" (about: "{r["on_chad_message"][:160]}")')
    return "\n".join(lines)


# ------------------------------------------------------------------ hourly updates

UPDATES_FILE = ROOT / "agent" / "chad_updates.md"


def compose(kind: str, data: str) -> str:
    """Write an hourly update / end-of-day recap from the data the bot gathered."""
    system = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if UPDATES_FILE.exists():
        system += "\n\n" + UPDATES_FILE.read_text(encoding="utf-8")
    system += feedback_block()
    msg = _get_client().messages.create(
        model=os.getenv("AGENT_REVIEW_MODEL", "claude-opus-5-5"),
        max_tokens=int(os.getenv("AGENT_REVIEW_MAX_TOKENS", "8000")),
        system=system,
        messages=[{"role": "user", "content": f"WRITE THE {kind.upper()}.\n\nDATA:\n{data}"}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
