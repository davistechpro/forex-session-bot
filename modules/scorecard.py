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
CHAD = "Chad"


def _rows(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def compute(day: str = None) -> dict:
    votes = _rows(VOTES)
    reviews = _rows(REVIEWS)

    # latest verdict per voter per post. Chad votes too since Nov 3 (voter
    # "Chad") but he is graded against the team, never counted as team.
    by_post = defaultdict(dict)
    voted_today = set()
    for r in votes:
        tok = r.get("signal_token")
        if not tok or r.get("voter") == CHAD:
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


def disagreements(day: str = None) -> list:
    """For the session-end report (Nov 3 call): every post from `day` where
    the team or Chad disagreed with the bot, or with each other, with reasons.
    The bot's own verdict is always VALID (it posted the trade)."""
    votes = _rows(VOTES)
    reviews = {r["signal_token"]: r for r in _rows(REVIEWS) if r.get("signal_token")}
    posts = {}
    for r in votes:
        tok = r.get("signal_token")
        if not tok:
            continue
        if day and not (r.get("voted_at_et") or "").startswith(day):
            # include the post if it was voted on today by anyone
            pass
        p = posts.setdefault(tok, {"token": tok, "pair": r.get("pair"), "post_type": r.get("post_type"),
                                   "direction": r.get("direction"), "entry": r.get("entry_price"),
                                   "zone": r.get("zone"), "team": {}, "chad": None, "days": set()})
        p["days"].add((r.get("voted_at_et") or "")[:10])
        if r.get("voter") == CHAD:
            p["chad"] = {"verdict": (r.get("verdict") or "").upper(), "reason": r.get("reason", "")}
        else:
            p["team"][r.get("voter", "?")] = {"verdict": (r.get("verdict") or "").upper(),
                                              "reason": r.get("reason", "")}
    out = []
    for tok, p in posts.items():
        if day and day not in p["days"]:
            continue
        if p["chad"] is None and tok in reviews:
            rv = reviews[tok]
            p["chad"] = {"verdict": (rv.get("verdict") or "").upper(), "reason": (rv.get("review") or "")[:220]}
        team_v = {v["verdict"] for v in p["team"].values()}
        chad_v = p["chad"]["verdict"] if p["chad"] else None
        if "INVALID" in team_v or (chad_v == "INVALID") or (len(team_v) > 1) \
                or (chad_v and team_v and chad_v not in team_v):
            p["days"] = sorted(p["days"])
            out.append(p)
    return out


def disagreements_text(day: str = None) -> str:
    rows = disagreements(day)
    if not rows:
        return "No disagreements: every voted post today was VALID for the team and for Chad."
    lines = []
    for p in rows:
        lines.append(f"{p['pair']} {p['direction']} {p['post_type']} entry {p['entry']} zone {p['zone']} (post {p['token']})")
        for who, v in p["team"].items():
            lines.append(f"   {who}: {v['verdict']} -- {v['reason']}")
        if p["chad"]:
            lines.append(f"   Chad: {p['chad']['verdict']} -- {p['chad']['reason']}")
        else:
            lines.append("   Chad: no review")
    return "\n".join(lines)


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
