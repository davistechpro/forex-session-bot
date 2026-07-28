"""
PHASE 6 — Decision & Risk Logic

Hard, non-negotiable rules that sit between the AI's proposal and any live
order. The AI can recommend; only this layer can authorize. Nothing here
should ever be overridden by a model's confidence score.

Exit gate: risk layer correctly blocks every rule-violating test case,
with zero exceptions.
"""
from modules.config_loader import load_settings

settings = load_settings()
RISK = settings["risk"]


class TradeProposal:
    def __init__(self, direction: str, entry: float, stop: float, target: float, size: int):
        self.direction = direction  # "buy" or "sell"
        self.entry = entry
        self.stop = stop
        self.target = target
        self.size = size


def evaluate_proposal(proposal: TradeProposal, trades_today: int, pips_lost_today: float) -> dict:
    """
    Check a proposed trade against hard risk rules.

    Returns: {"approved": bool, "reason": str}

    TODO (Phase 6):
      - Reject if size > RISK["max_position_size_units"]
      - Reject if stop distance != RISK["stop_loss_pips"] (or exceeds it)
      - Reject if trades_today >= RISK["max_trades_per_session"]
      - Reject if pips_lost_today >= RISK["daily_loss_cap_pips"]
      - Otherwise approve
    """
    raise NotImplementedError("Phase 6: implement hard risk checks here")


if __name__ == "__main__":
    print("risk_gate.py — implement evaluate_proposal() and unit test edge cases")
