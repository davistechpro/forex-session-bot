"""
PHASE 8 — Review Dashboard

Run with:  streamlit run dashboard.py

Pulls from logs/ once main.py has produced real session data. This is a
placeholder page confirming Streamlit is installed and working — real
data wiring happens once Phases 2-7 are producing logged decisions.
"""
import streamlit as st
from datetime import date

st.set_page_config(page_title="Session Monitor", layout="wide")

st.title("Session Monitor — EUR/USD NY Session")
st.caption(f"{date.today().strftime('%A, %B %d, %Y')}")

st.info(
    "This is the Phase 8 placeholder. Once Phases 2-7 are producing real "
    "session logs, this page will show: today's checkpoints, screenshots, "
    "the AI's read on each one, and the agree/disagree verdict control."
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Trades today", "—")
col2.metric("Result so far", "—")
col3.metric("Agreement rate", "—")
col4.metric("Missed calls", "—")

st.divider()
st.subheader("This morning's checkpoints")
st.write("No session data yet — this fills in once main.py has run and logged a real cycle.")
