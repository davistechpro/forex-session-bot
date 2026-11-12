"""
Shadow report -- where the trend fix (v2) disagreed with the live bot, next to
the team's votes. Run after the session:

  py shadow_report.py               today
  py shadow_report.py 2026-11-10
"""
import csv
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import pytz

NY = pytz.timezone("America/New_York")
day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(NY).strftime("%Y-%m-%d")
path = Path("status") / f"shadow_{day}.csv"
if not path.exists():
    raise SystemExit(f"No shadow log for {day} ({path}). Was watch.py running with the shadow build?")

rows = list(csv.DictReader(path.open(encoding="utf-8")))
diff = [r for r in rows if r["different"] == "YES"]
print(f"Shadow {day}: {len(rows)} scans, {len(diff)} where the fix would act differently\n")

# collapse consecutive identical differences per pair
runs = OrderedDict()
for r in diff:
    key = (r["pair"], r["live_dir"], r["fix_dir"], r["fix_zone"], r["fix_entry"])
    runs.setdefault(key, []).append(r)

for (pair, ld, fd, fz, fe), rs in runs.items():
    first, last = rs[0], rs[-1]
    print(f"{pair}  {first['at_et'][11:16]}-{last['at_et'][11:16]}")
    print(f"  live: {ld.upper():8} D/4H/1H {first['live_d']}/{first['live_h4']}/{first['live_h1']}  "
          f"{first['live_zone'] or 'no zone'}  {first['live_entry']}")
    print(f"  fix : {fd.upper():8} D/4H/1H {first['fix_d']}/{first['fix_h4']}/{first['fix_h1']}  "
          f"{fz or 'no zone'}  {fe}")
    for part in first["why_fix"].split(" | "):
        print(f"    {part}")
    print()

votes = Path("votes.csv")
if votes.exists():
    team = [v for v in csv.DictReader(votes.open(encoding="utf-8"))
            if (v.get("voted_at_et") or "").startswith(day) and v.get("voter") != "Chad"]
    if team:
        print("Team votes today:")
        for v in team:
            print(f"  {v['voted_at_et'][11:16]} {v['voter']:8} {v['verdict']:8} {v['pair']} {v['direction']}  "
                  f"{(v.get('reason') or '')[:90]}")
