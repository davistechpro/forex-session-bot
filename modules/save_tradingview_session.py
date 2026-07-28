"""
PHASE 4a — One-time TradingView login (run this ONCE, interactively)

This opens a real, visible browser window so you can log into TradingView
manually (username/password, 2FA, whatever your account needs). Once
you're logged in and looking at your actual chart with your indicator
loaded, come back to this terminal and press Enter — it saves your
session (cookies + local storage) to a file so every future screenshot
run can skip the login step entirely.

Run with:  python -m modules.save_tradingview_session

Note: this needs a visible browser, so it should be run once from a
machine with a display — NOT over a headless SSH/Bastion session. If
your VM has no display, run this step on your own computer instead,
then copy the resulting auth/tv_session.json file onto the VM.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

AUTH_DIR = Path(__file__).resolve().parent.parent / "auth"
AUTH_DIR.mkdir(exist_ok=True)
SESSION_FILE = AUTH_DIR / "tv_session.json"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.tradingview.com/chart/")

        print("\nA browser window has opened.")
        print("1. Log into TradingView.")
        print("2. Navigate to your saved chart layout with your indicator loaded.")
        print("3. Once the chart looks correct, come back here and press Enter.\n")
        input("Press Enter once you're logged in and the chart is ready...")

        context.storage_state(path=str(SESSION_FILE))
        print(f"\nSession saved to {SESSION_FILE}")
        print("Future screenshot runs will reuse this — no login needed.")

        browser.close()


if __name__ == "__main__":
    main()