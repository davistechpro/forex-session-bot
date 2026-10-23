"""
Red-news calendar -- high-impact events for the currencies in our 7 pairs.
Information only: never blocks or filters a trade.

Source: the public Forex Factory weekly calendar feed (JSON). Each event has
title, country (currency code), date (ISO with UTC offset) and impact.
If the feed can't be reached, callers get an empty list plus an error note.
"""
from datetime import datetime

import pandas as pd
import pytz
import requests

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NY = pytz.timezone("America/New_York")
PAIRS = ["EUR_USD", "EUR_JPY", "GBP_CAD", "USD_JPY", "USD_CAD", "GBP_USD", "GBP_JPY"]
CURRENCIES = sorted({c for p in PAIRS for c in p.split("_")})


def pairs_for(ccy: str):
    return [p for p in PAIRS if ccy in p.split("_")]


def red_events(day: str = None):
    """High-impact events for our currencies on the given ET day.
    Returns (events, error). events: list of dicts with time (ET datetime),
    currency, title, pairs, forecast, previous."""
    day = day or datetime.now(NY).strftime("%Y-%m-%d")
    try:
        r = requests.get(FEED, timeout=15, headers={"User-Agent": "opt-trade-bot/1.0"})
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return [], f"couldn't reach the news calendar ({type(e).__name__})"
    out = []
    for ev in raw if isinstance(raw, list) else []:
        if str(ev.get("impact", "")).lower() != "high":
            continue
        ccy = str(ev.get("country", "")).upper()
        if ccy not in CURRENCIES:
            continue
        try:
            t = pd.Timestamp(ev.get("date")).tz_convert(NY)
        except Exception:
            continue
        if t.strftime("%Y-%m-%d") != day:
            continue
        out.append({"time": t, "currency": ccy, "title": ev.get("title", ""),
                    "pairs": pairs_for(ccy), "forecast": ev.get("forecast", ""),
                    "previous": ev.get("previous", "")})
    out.sort(key=lambda e: e["time"])
    return out, None


def line(ev: dict) -> str:
    extra = []
    if ev.get("forecast"):
        extra.append(f"forecast {ev['forecast']}")
    if ev.get("previous"):
        extra.append(f"previous {ev['previous']}")
    tail = f" ({', '.join(extra)})" if extra else ""
    pairs = ", ".join(p.replace("_", "/") for p in ev["pairs"])
    return f"{ev['time']:%I:%M %p} ET  {ev['currency']} {ev['title']}{tail}  -> {pairs}"
