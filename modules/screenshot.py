"""
PHASE 4 — Screenshot Capture (real implementation)

Uses the session saved by save_tradingview_session.py to open your chart
headlessly (no visible window, no repeated login) and capture a screenshot.

Requires auth/tv_session.json to exist first — run
`python -m modules.save_tradingview_session` once before using this.
"""
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

from modules.config_loader import load_settings

settings = load_settings()

ROOT_DIR = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT_DIR / "auth" / "tv_session.json"
SAVE_DIR = ROOT_DIR / settings["screenshots"]["save_dir"]
SAVE_DIR.mkdir(exist_ok=True)

# Fill this in with the URL of your actual saved TradingView chart layout
# (with your indicator loaded) — e.g. https://www.tradingview.com/chart/AbCdEfGh/
CHART_URL = settings.get("screenshots", {}).get("tradingview_chart_url")


def capture_chart(pair: str, timeframe: str) -> Path:
    """
    Open the saved TradingView chart headlessly and save a screenshot.

    Returns the path to the saved screenshot file.
    """
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"No saved session found at {SESSION_FILE}. "
            f"Run `python -m modules.save_tradingview_session` once first "
            f"(from a machine with a display) to log in and save your session."
        )

    if not CHART_URL:
        raise ValueError(
            "No tradingview_chart_url set in config/settings.yaml under 'screenshots'. "
            "Add the URL of your saved chart layout there."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = SAVE_DIR / f"{pair}_{timeframe}_{timestamp}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(SESSION_FILE))
        page = context.new_page()
        page.goto(CHART_URL)

        # Give the chart time to fully render — TradingView loads async,
        # a fixed screenshot immediately after goto() often catches it
        # half-drawn. Adjust this if your chart/indicator is slow to load.
        page.wait_for_timeout(5000)

        page.screenshot(path=str(filename))
        browser.close()

    return filename


if __name__ == "__main__":
    # Manual test — run: python -m modules.screenshot
    pair = settings["instrument"]["pair"]
    timeframe = settings["instrument"]["context_timeframe"]

    print(f"Capturing chart for {pair} {timeframe}...")
    path = capture_chart(pair, timeframe)
    print(f"Saved: {path}")
    print("Open this file to confirm the chart and your indicator actually rendered correctly.")