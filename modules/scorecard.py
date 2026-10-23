"""
Scorecard -- progress toward the 300-vote goal and how often Chad agrees
with the team.

  Team verdict for a post = majority of the team's recorded votes
  (votes.csv, reason required). Ties don't count.
  Chad's verdict for a post = his latest review (agent_reviews.csv).
  Agreement = posts where both exist and Chad said VALID/INVALID and it
  matches the team, divided by all such posts. UNSURE is counted separately.

  py -m modules.scorecard      prints the scorecard
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOTES = ROOT / "votes.csv"
REVIEWS = ROOT / "agent_reviews.csv"
GOAL = 300


def _rows(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def compute(day: str = None) -> dict:
    votes = _rows(VOTES)
    reviews = _rows(REVIEWS)

    # latest verdict per voter per post
    by_post = defaultdict(dict)
    voted_today = set()
    for r in votes:
        tok = r.get("signal_token")
        if not tok:
            continue
        by_post[tok][r.get("voter", "?")] = (r.get("verdict") or "").lower()
        if day and (r.get("voted_at_et") or "").startswith(day):
            voted_today.add(tok)

    team = {}
    for tok, vs in by_post.items():
        c = Counter(vs.values())
        if c["valid"] > c["invalid"]:
            team[tok] = "VALID"
        elif c["invalid"] > c["valid"]:
            team[tok] = "INVALID"

    chad = {}
    for r in reviews:
        if r.get("signal_token"):
            chad[r["signal_token"]] = (r.get("verdict") or "").upper()

    both = [t for t in team if t in chad]
    decided = [t for t in both if chad[t] in ("VALID", "INVALID")]
    agree = [t for t in decided if chad[t] == team[t]]
    disagree = [t for t in decided if chad[t] != team[t]]

    return {
        "posts_voted": len(by_post),
        "goal": GOAL,
        "voted_today": len(voted_today),
        "team_valid": sum(1 for v in team.values() if v == "VALID"),
        "team_invalid": sum(1 for v in team.values() if v == "INVALID"),
        "compared": len(decided),
        "agree": len(agree),
        "disagree_tokens": disagree,
        "chad_unsure": sum(1 for t in both if chad[t] == "UNSURE"),
        "agreement_pct": round(100 * len(agree) / len(decided)) if decided else None,
    }


def text(day: str = None) -> str:
    s = compute(day)
    lines = [f"Votes: {s['posts_voted']} of {s['goal']} posts voted"
             + (f" ({s['voted_today']} today)" if day else "") + "."]
    tv = s["team_valid"] + s["team_invalid"]
    if tv:
        lines.append(f"Bot accuracy by team vote: {s['team_valid']} valid / {s['team_invalid']} invalid "
                     f"({round(100 * s['team_valid'] / tv)}% valid). Gage's target before win/loss tracking: 90%.")
    if s["compared"]:
        lines.append(f"Chad agrees with the team on {s['agree']} of {s['compared']} posts "
                     f"({s['agreement_pct']}%), plus {s['chad_unsure']} he marked UNSURE.")
    else:
        lines.append("Chad agreement: not enough posts with both a team vote and a Chad review yet.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(text())
