"""
Status files -- how the watcher, the vote listener and Chad know what the
others are doing. Everything lives in the status/ folder (not in git).

  status/watch.json            last scan pass (written every loop)
  status/listener.json         last Telegram poll
  status/pairs_YYYY-MM-DD.json what each pair did today, with history
  status/errors_YYYY-MM-DD.jsonl  errors from any part of the bot
"""
import json
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "status"
NY = pytz.timezone("America/New_York")
_lock = threading.Lock()


def _now():
    return datetime.now(NY)


def _today():
    return _now().strftime("%Y-%m-%d")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data):
    DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ heartbeats

def beat(name: str, **fields):
    """Record that a process is alive (watch / listener / chad)."""
    with _lock:
        path = DIR / f"{name}.json"
        data = _read(path, {})
        data.update(fields)
        data["ts"] = _now().isoformat(timespec="seconds")
        _write(path, data)


def last_beat(name: str):
    """(datetime or None, data dict)"""
    data = _read(DIR / f"{name}.json", {})
    try:
        return datetime.fromisoformat(data["ts"]), data
    except Exception:
        return None, data


# ------------------------------------------------------------------ per pair

def _et(ts):
    try:
        return pd.Timestamp(ts).tz_convert(NY).strftime("%a %I:%M %p")
    except Exception:
        return str(ts)


def summarize(result: dict) -> str:
    """One line on what the bot saw for a pair on this scan."""
    if not result:
        return "no result"
    trends = "/".join(str((result.get(k) or {}).get("trend", "?")) for k in ("daily_trend", "h4_trend", "h1_trend"))
    d = result.get("valid_direction")
    parts = [f"trend D/4H/1H {trends}", f"direction {d or 'none (no 2-of-3)'}"]
    z = result.get("zone")
    if z:
        parts.append(f"zone {result.get('zone_source_tf')} {z.get('type')} {z.get('bottom')}-{z.get('top')} "
                     f"origin {_et(z.get('origin_time'))}")
    elif d:
        parts.append("no usable 4H/1H zone")
    e = result.get("entry")
    if e:
        parts.append(f"trigger {e.get('method')} at {_et(e.get('time'))}")
    elif result.get("skip_reason"):
        parts.append(result["skip_reason"])
    return " | ".join(parts)


def note_pair(pair: str, outcome: str):
    """Latest outcome per pair; history keeps each change of outcome."""
    with _lock:
        path = DIR / f"pairs_{_today()}.json"
        data = _read(path, {})
        p = data.setdefault(pair, {"history": []})
        t = _now().strftime("%I:%M %p")
        if p.get("outcome") != outcome:
            p["history"].append(f"{t}  {outcome}")
            p["history"] = p["history"][-25:]
        p["outcome"], p["time"] = outcome, t
        _write(path, data)


def pairs_today(day: str = None) -> dict:
    return _read(DIR / f"pairs_{day or _today()}.json", {})


# ------------------------------------------------------------------ errors

def error(source: str, msg: str):
    with _lock:
        DIR.mkdir(exist_ok=True)
        with (DIR / f"errors_{_today()}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _now().isoformat(timespec="seconds"),
                                 "source": source, "msg": str(msg)[:500]}) + "\n")


def errors_today(day: str = None) -> list:
    path = DIR / f"errors_{day or _today()}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ trades

def record_trade(sid: str, pair: str, entry: dict, kind: str):
    """Remember a pending/active trade so Chad can follow it (fill, SL, TP)."""
    key = sid.replace("PENDING|", "")
    with _lock:
        path = DIR / f"trades_{_today()}.json"
        data = _read(path, {})
        t = data.setdefault(key, {})
        t.update({
            "pair": pair, "direction": entry.get("direction"),
            "entry": entry.get("entry_price"), "stop": entry.get("stop_loss"),
            "target": entry.get("take_profit"), "sl_pips": entry.get("sl_pips"),
            "tp_pips": entry.get("tp_pips"), "method": entry.get("method"),
            "level": entry.get("entry_level"),
            "triggered_at": str(entry.get("time", "")),
            "posted_as": kind if not t.get("posted_as") else
            (t["posted_as"] if t["posted_as"] == "active" else kind),
        })
        if entry.get("filled_at") is not None:
            t["filled_at"] = str(entry["filled_at"])
        _write(path, data)


def trades_today(day: str = None) -> dict:
    return _read(DIR / f"trades_{day or _today()}.json", {})
