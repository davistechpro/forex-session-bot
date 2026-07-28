"""
PHASE 2 — Market Data Connection (real implementation)

Pulls candle data from OANDA's REST API and computes precise NY-session
high/low from real price data — not estimated from a screenshot.
"""
from datetime import datetime, timedelta
import pandas as pd
import pytz
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles

from modules.config_loader import load_settings, get_secret

settings = load_settings()

# Map our config's friendly timeframe names to OANDA's granularity codes
GRANULARITY_MAP = {
    "M5": "M5",
    "M15": "M15",
    "H1": "H1",
}


def _get_client() -> API:
    """Build an authenticated OANDA API client using the environment from .env."""
    api_key = get_secret("OANDA_API_KEY")
    environment = get_secret("OANDA_ENVIRONMENT")  # "practice" or "live"
    return API(access_token=api_key, environment=environment)


def get_candles(pair: str, timeframe: str, count: int = 200) -> pd.DataFrame:
    """
    Pull the most recent `count` candles for `pair` at `timeframe`
    (e.g. "M5", "M15", "H1") from OANDA.

    Returns a DataFrame with columns: time, open, high, low, close
    (all floats, time as a UTC-aware pandas Timestamp).
    """
    client = _get_client()
    granularity = GRANULARITY_MAP.get(timeframe)
    if granularity is None:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Use one of {list(GRANULARITY_MAP)}")

    params = {"count": count, "granularity": granularity, "price": "M"}  # M = midpoint price
    request = InstrumentsCandles(instrument=pair, params=params)
    client.request(request)
    raw_candles = request.response["candles"]

    rows = []
    for c in raw_candles:
        if not c["complete"]:
            continue  # skip the in-progress candle, only use closed candles
        rows.append({
            "time": pd.to_datetime(c["time"]),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
        })

    df = pd.DataFrame(rows)
    return df


def get_session_high_low(pair: str, session_date: datetime = None) -> dict:
    """
    Return the exact high and low of the NY session (from config's
    session.start_time to session.end_time, America/New_York) for the
    given date. Defaults to today.
    """
    ny_tz = pytz.timezone(settings["session"]["timezone"])
    if session_date is None:
        session_date = datetime.now(ny_tz).date()

    start_str = settings["session"]["start_time"]
    end_str = settings["session"]["end_time"]

    session_start = ny_tz.localize(datetime.combine(
        session_date, datetime.strptime(start_str, "%H:%M").time()
    ))
    session_end = ny_tz.localize(datetime.combine(
        session_date, datetime.strptime(end_str, "%H:%M").time()
    ))

    # Pull enough M5 candles to comfortably cover the session window
    candles = get_candles(pair, "M5", count=500)
    candles["time"] = candles["time"].dt.tz_convert(ny_tz)

    session_candles = candles[
        (candles["time"] >= session_start) & (candles["time"] <= session_end)
    ]

    if session_candles.empty:
        raise ValueError(
            f"No candles found for session window {session_start} - {session_end}. "
            f"Check that count=500 covers far enough back, or that today isn't a "
            f"non-trading day."
        )

    return {
        "session_high": session_candles["high"].max(),
        "session_low": session_candles["low"].min(),
        "session_start": session_start,
        "session_end": session_end,
    }


if __name__ == "__main__":
    # Manual smoke test — run: python modules/data_feed.py
    pair = settings["instrument"]["pair"]

    print(f"Pulling last 10 H1 candles for {pair}...")
    df = get_candles(pair, "H1", count=10)
    print(df)
    print()

    print("Computing today's NY session high/low...")
    try:
        result = get_session_high_low(pair)
        print(result)
    except ValueError as e:
        print(f"(expected if outside/before today's session window): {e}")
