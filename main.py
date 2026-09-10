"""
PHASE 7 — Orchestration (shadow mode)

Ties every phase together into one running cycle. Runs on a schedule
through the NY session window. SHADOW_MODE=True means the full pipeline
executes and logs its decisions, but places zero live orders — this is
the required state until Phase 9 (paper trading) explicitly turns it off.

Run manually with:  python main.py
Eventually this gets triggered by cron during 9:00-12:00 ET on weekdays.
"""
import logging
from datetime import datetime
from pathlib import Path

from modules.config_loader import load_settings
from modules import data_feed, fvg_detector, screenshot, ai_judgment, risk_gate

SHADOW_MODE = True  # DO NOT set False until Phase 9

settings = load_settings()

log_dir = Path(settings["logging"]["log_dir"])
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    filename=log_dir / f"session_{datetime.now().strftime('%Y%m%d')}.log",
    level=settings["logging"]["level"],
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger("main")


def run_cycle():
    """
    One full pass of the pipeline: data -> FVG detection -> screenshot ->
    AI judgment -> risk gate -> logged decision.

    Each call below is currently a Phase stub (raises NotImplementedError)
    until that phase is built — that's expected at this point in the plan.
    """
    pair = settings["instrument"]["pair"]
    log.info(f"Starting cycle for {pair} | shadow_mode={SHADOW_MODE}")

    # Phase 2
    candles = data_feed.get_candles(pair, settings["instrument"]["context_timeframe"])

    # Phase 3
    zones = fvg_detector.detect_fvgs(candles)

    # Phase 4
    shot_path = screenshot.capture_chart(pair, settings["instrument"]["context_timeframe"])

    # Phase 5
    judgment = ai_judgment.evaluate_setup(shot_path, zones)

    # Phase 6 — only relevant once judgment recommends an entry
    if judgment.get("recommended_action") == "propose_entry":
        # build a TradeProposal from judgment + zones, then:
        # decision = risk_gate.evaluate_proposal(proposal, trades_today, pips_lost_today)
        pass

    log.info("Cycle complete.")


if __name__ == "__main__":
    print("Running one cycle in shadow mode (no live orders will be placed).")
    print("This will raise NotImplementedError until Phases 2-6 are built — expected for now.\n")
    run_cycle()
