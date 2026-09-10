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

GRANULARITY_MAP = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "H1": "H1",
    "H4": "H4",
    "D": "D",
}


def _get_client() -> API:
    api_key = get_secret("OANDA_API_KEY")
    environment = get_secret("OANDA_ENVIRONMENT")
    return API(access_token=api_key, environment=environment)


def get_candles(pair: str, timeframe: str, count: int = 200) -> pd.DataFrame:
    client = _get_client()
    granularity = GRANULARITY_MAP.get(timeframe)
    if granularity is None:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Use one of {list(GRANULARITY_MAP)}")

    params = {"count": count, "granularity": granularity, "price": "M"}
    request = InstrumentsCandles(instrument=pair, params=params)
    client.request(request)
    raw_candles = request.response["candles"]

    rows = []
    for c in raw_candles:
        if not c["complete"]:
            continue
        rows.append({
            "time": pd.to_datetime(c["time"]),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": int(c.get("volume", 0)),
        })

    df = pd.DataFrame(rows)
    return df


def get_session_high_low(pair: str, session_date: datetime = None) -> dict:
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

    candles = get_candles(pair, "M5", count=500)
    candles["time"] = candles["time"].dt.tz_convert(ny_tz)

    session_candles = candles[
        (candles["time"] >= session_start) & (candles["time"] <= session_end)
    ]

    if session_candles.empty:
        raise ValueError(
            f"No candles found for session window {session_start} - {session_end}."
        )

    return {
        "session_high": session_candles["high"].max(),
        "session_low": session_candles["low"].min(),
        "session_start": session_start,
        "session_end": session_end,
    }


if __name__ == "__main__":
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
