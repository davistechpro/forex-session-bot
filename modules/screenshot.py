"""
PHASE 4 — Screenshot Capture

Uses Playwright to log into the charting platform and capture the chart
on schedule. This is the system's "eyes" — it doesn't interpret anything,
it just reliably takes the picture.

Exit gate: a full session's worth of screenshots captured automatically,
no missed or broken captures.
"""
from datetime import datetime
from pathlib import Path
from modules.config_loader import load_settings

settings = load_settings()
SAVE_DIR = Path(settings["screenshots"]["save_dir"])
SAVE_DIR.mkdir(exist_ok=True)


def capture_chart(pair: str, timeframe: str) -> Path:
    """
    Open the charting platform in a headless browser and save a screenshot.

    TODO (Phase 4):
      - Launch Playwright, open charting platform URL
      - Log in (store credentials in .env, never hard-coded)
      - Navigate to `pair` / `timeframe`
      - Screenshot and save to SAVE_DIR with a timestamped filename
      - Return the saved file path
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = SAVE_DIR / f"{pair}_{timeframe}_{timestamp}.png"
    raise NotImplementedError("Phase 4: implement Playwright capture here")


if __name__ == "__main__":
    print("screenshot.py — implement capture_chart() with Playwright")
