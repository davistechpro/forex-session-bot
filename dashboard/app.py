"""
Session Monitor Dashboard — scans all 7 approved pairs using the real
strategy logic (trend hierarchy, zone ID, rejection detection, entry
check) and shows results as a simple, shared web page.
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

# Bridge: on Streamlit Cloud, credentials come from st.secrets (set in the
# app's Settings -> Secrets), not from a .env file. Copy them into
# environment variables so config_loader.get_secret() works identically
# whether running locally (.env) or deployed (st.secrets).
for _key in ["OANDA_API_KEY", "OANDA_ACCOUNT_ID", "OANDA_ENVIRONMENT"]:
    if _key in st.secrets and not os.getenv(_key):
        os.environ[_key] = st.secrets[_key]

from modules.scan_engine import run_scan, is_ny_session_now

PAIRS = ["EUR_USD", "EUR_JPY", "GBP_CAD", "USD_JPY", "USD_CAD", "GBP_USD", "GBP_JPY"]

st.set_page_config(page_title="Session Monitor", layout="wide")


def check_password():
    def password_entered():
        if st.session_state.get("password") == st.secrets.get("DASHBOARD_PASSWORD", ""):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct"):
        return True

    st.text_input("Password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("Incorrect password")
    return False


if not check_password():
    st.stop()

st.title("Session Monitor")

session = is_ny_session_now()
if session["active"]:
    st.success(f"NY session ACTIVE — {session['current_time_et']}")
else:
    st.warning(f"Outside trading window (9:00 AM-12:59 PM ET) — currently {session['current_time_et']}")

st.caption("Trend hierarchy: 4H is main reference, drops to 1H if 4H is unclear. Includes push + rejection (engulfing + volume) signals.")

if st.button("Scan now", type="primary"):
    progress = st.progress(0, text="Starting scan...")
    results = []

    for i, pair in enumerate(PAIRS):
        progress.progress((i) / len(PAIRS), text=f"Scanning {pair}...")
        try:
            result = run_scan(pair, render_chart_image=False)
            results.append(result)
        except Exception as e:
            results.append({"pair": pair, "error": str(e)})

    progress.progress(1.0, text="Done")
    st.session_state["last_results"] = results

if "last_results" in st.session_state:
    st.divider()
    for result in st.session_state["last_results"]:
        pair = result["pair"]

        if result.get("error"):
            with st.expander(f"{pair} — ERROR", expanded=False):
                st.error(result["error"])
            continue

        direction = result.get("valid_direction")
        entry = result.get("entry")

        if entry:
            badge = "TRADE FOUND"
        elif direction:
            badge = f"{direction.upper()} — no entry yet"
        else:
            badge = "No valid direction"

        with st.expander(f"{pair} — {badge}", expanded=bool(entry)):
            col1, col2, col3 = st.columns(3)
            for col, label, trend_data in [
                (col1, "Daily", result["daily_trend"]),
                (col2, "4H", result["h4_trend"]),
                (col3, "1H", result["h1_trend"]),
            ]:
                with col:
                    st.metric(
                        label,
                        trend_data["trend"].upper() if trend_data else "-",
                        help=f"via {trend_data.get('signal_type', 'n/a')}" if trend_data else None,
                    )

            if direction:
                st.write(f"**Valid direction:** {direction.upper()} (per {result['deciding_tf']})")
            else:
                st.write("**No valid direction** — both 4H and 1H unclear")

            zone = result.get("zone")
            if zone:
                st.write(
                    f"**Zone:** {zone['type'].upper()} "
                    f"{zone['bottom']:.5f}-{zone['top']:.5f} "
                    f"(confirmed {zone['confirmed_time']})"
                )
            elif direction:
                st.write("**Zone:** none active")

            if entry:
                st.success(
                    f"**{entry['direction']} @ {entry['entry_price']:.5f}**  \n"
                    f"SL: {entry['stop_loss']:.5f}  |  TP: {entry['take_profit']:.5f}  \n"
                    f"Trigger: {entry['timeframe']} wick tap at {entry['time']}"
                )
            elif zone:
                st.info("No wick tap into the zone during the trading window yet.")
else:
    st.info("Click Scan now to pull live data across all 7 pairs.")
