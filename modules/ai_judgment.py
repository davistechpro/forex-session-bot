"""
PHASE 5 — AI Judgment Layer

Takes the zones/FVGs Phase 3 already flagged (mathematically) and a
screenshot from Phase 4, and asks the AI model to rate quality, context,
and confluence — the genuinely discretionary read that doesn't reduce
cleanly to a formula.

Exit gate: AI's calls on historical setups match your own judgment closely
enough to trust as a filter, not yet as a decision-maker.
"""
from modules.config_loader import load_settings, get_secret

settings = load_settings()

SYSTEM_PROMPT = """You are assisting a discretionary EUR/USD scalper who trades
the New York session (9:00-12:00 ET) using smart-money concepts: demand/supply
zones, fair value gaps, and session highs/lows.

You will be given: (1) a chart screenshot, and (2) a list of FVGs/zones already
detected mathematically from price data. Your job is judgment, not detection —
assess the QUALITY and CONTEXT of what's already been flagged.

Respond ONLY with JSON in this exact shape:
{
  "setup_quality": "high" | "medium" | "low",
  "confluence_notes": "<one sentence>",
  "recommended_action": "watch" | "propose_entry" | "skip",
  "confidence": 0.0-1.0
}
"""


def evaluate_setup(screenshot_path: str, detected_zones: list[dict]) -> dict:
    """
    Send the screenshot + detected zones to the configured AI model and
    return its structured judgment.

    TODO (Phase 5):
      - Load screenshot as base64
      - Call Claude (or GPT) with SYSTEM_PROMPT + image + detected_zones
      - Parse and validate the JSON response
      - Log every call (input + output) for later comparison against your
        own manual verdicts — this is what the backtest in the exit gate needs
    """
    raise NotImplementedError("Phase 5: implement AI vision call here")


if __name__ == "__main__":
    print("ai_judgment.py — implement evaluate_setup() against Claude or GPT")
