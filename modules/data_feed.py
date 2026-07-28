"""
PHASE 2 — Market Data Connection

Responsible for pulling candle data (historical + live) from the broker API
and computing precise session highs/lows. This is the foundation every other
module depends on — get this solid before building anything on top of it.

Exit gate (from the build plan): data pulled here matches manual chart
readings exactly, across several test days.
"""
from modules.config_loader import load_settings, get_secret

settings = load_settings()


def get_candles(pair: str, timeframe: str, count: int = 200):
    """
    Pull the most recent `count` candles for `pair` at `timeframe`
    (e.g. "M5", "M15", "H1") from the broker API.

    TODO (Phase 2):
      - Authenticate using OANDA_API_KEY / OANDA_ACCOUNT_ID from .env
      - Call the broker's candle endpoint
      - Return a pandas DataFrame with columns: time, open, high, low, close
    """
    raise NotImplementedError("Phase 2: connect to broker API here")


def get_session_high_low(pair: str, session_date):
    """
    Return the exact high and low of the NY session for a given date,
    computed from real candle data — not read off a screenshot.

    TODO (Phase 2): pull session-window candles and reduce to high/low.
    """
    raise NotImplementedError("Phase 2: compute session high/low here")


if __name__ == "__main__":
    # Manual smoke test once Phase 2 is implemented:
    # python modules/data_feed.py
    print("data_feed.py — implement get_candles() and get_session_high_low()")
