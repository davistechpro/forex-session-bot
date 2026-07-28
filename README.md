# Forex Session Bot — Project Skeleton (Phase 1)

This is the scaffolding from the Build Plan's Phase 1. Every module below
is a stub — it defines the shape of the function and raises
`NotImplementedError` until its phase is actually built. Running `main.py`
right now is *supposed* to fail at Phase 2 — that confirms the pieces are
wired together correctly.

## Project structure

```
forex-session-bot/
├── main.py              # Phase 7 — orchestrator, runs the full cycle
├── dashboard.py          # Phase 8 — Streamlit review dashboard
├── modules/
│   ├── config_loader.py  # loads settings.yaml + .env (done, working)
│   ├── data_feed.py       # Phase 2 — broker API / candle data
│   ├── fvg_detector.py    # Phase 3 — fair value gap detection (pure code)
│   ├── screenshot.py      # Phase 4 — Playwright chart capture
│   ├── ai_judgment.py     # Phase 5 — Claude/GPT vision judgment layer
│   └── risk_gate.py       # Phase 6 — hard risk/position rules
├── config/
│   └── settings.yaml      # session times, pair, risk limits — edit this, not code
├── .env.example           # copy to .env, fill in real API keys
├── requirements.txt
├── screenshots/            # captured chart images land here
├── logs/                   # session logs land here
└── data/                   # any saved candle data lands here
```

## Setup steps (do these on your actual VM, not this sandbox)

1. **Get a Linux VM** (DigitalOcean, AWS EC2, etc.) — Ubuntu 22.04 is a safe
   default, same family you already use for CUI Vault work.

2. **Install Python 3.11+** and clone/copy this project onto the VM.

3. **Create a virtual environment and install dependencies:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium    # downloads the browser Playwright drives
   ```

4. **Set up your secrets:**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and fill in:
   - `OANDA_API_KEY` / `OANDA_ACCOUNT_ID` — from your OANDA account (use a
     **practice/demo** account for now, not live)
   - `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` — whichever model you're using

5. **Review `config/settings.yaml`** — session times, pair, and risk limits
   all live here. Nothing about the strategy is hard-coded into the Python
   files; this file is what you and your partner should be able to tune
   without touching code.

6. **Confirm the skeleton runs:**
   ```bash
   python modules/config_loader.py   # should print settings, no errors
   python main.py                    # should run and stop at the Phase 2 stub
   ```

7. **Confirm the dashboard placeholder runs:**
   ```bash
   streamlit run dashboard.py
   ```
   This opens a browser page with empty metrics — confirms Streamlit itself
   is working before any real data flows into it.

## Next step

Phase 2 — build `modules/data_feed.py` against the OANDA API. Once
`get_candles()` and `get_session_high_low()` are implemented and verified
against manual chart readings, that phase's exit gate is met and Phase 3
(FVG detection) can start.
