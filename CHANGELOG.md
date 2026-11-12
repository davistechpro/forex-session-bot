# OPT Bot Changelog

Every change to the bot gets an entry here BEFORE it goes live. This is how we
answer "it used to do X, why doesn't it anymore?" and get back to a version
that worked. (Team decision, Nov 3 call: "every time we take a step forward
we take two steps back.")

## The routine

1. **Save point.** Tag the current code before touching anything:
   `git tag before-YYYY-MM-DD` (add `-b`, `-c` if there's more than one change that day).
2. **One change at a time.** Never bundle a trade-logic change with anything
   else. If two things go in together and something breaks, we can't tell which.
3. **Trade-logic changes get a replay check** (anything in `modules/scan_engine.py`,
   `zone_detector.py`, `trend.py`, `rejection.py`, `confirmation_entry.py`, `setup_planner.py`):
   - before: `py replay_check.py --save before-YYYY-MM-DD`
   - after:  `py replay_check.py --compare before-YYYY-MM-DD`
   - Paste the "N of M reference days changed" line into the entry. If a day
     changed that the change wasn't meant to touch, stop and look first.
   - Chad, chat, chart-drawing and ops changes don't need it.
4. **Write the entry** (template below), commit, then tag the result:
   `git tag <name>-YYYY-MM-DD`.
5. **Revert** = the REVERT line of the entry. Then restart the three windows.

Add a day the team judged to the reference set any time:
`py replay_check.py --add GBP_CAD 2026-10-27 "Gage: wrong 4H zone"`

## Entry template

```
### YYYY-MM-DD  <short name>
TAG:      <tag after>   (previous: <tag before>)
WHAT:     what changed, in trader terms
WHY:      who asked / which trade showed the problem
AFFECTS:  Trade decisions? yes/no. Which pairs, timeframes, post types.
          Side effects we know of.
PROOF:    replay_check result, or the test that shows it works
REVERT:   exact command
```

---

## Open leads

- **Trend accuracy (Gage, Nov 3: "it used to be really accurate").** The
  trend lookback (Daily 60 / 4H 120 / 1H 200 candles) has NOT changed since
  Sept 9, so "we limited how far it looks back" isn't supported by the
  history. Trend = the most recent push zone on each timeframe, or a later
  rejection of a zone (`modules/trend.py`). Anything that changes which zones
  exist or when they die changes trend. Suspects, in order: the Oct 28 zone fix
  (zones live longer), the Sept 9 invalidation fix, and the overlap rule (a
  new zone overlapping a live one is skipped). Next step: Gage/Steven send
  3-5 wrong-trend posts -> add them as reference days -> replay them on the
  tags around each suspect. UPDATE Nov 10: 5 posts received; two code flaws
  found (rejection touch check, hidden newest push); fix running in shadow.
  If `--why` shows the live trend coming from a push that is many days old,
  there is a third cause: a steady trend with no pullback candle never makes
  a new push, so the trend stays on the old one. That one needs a team rule.

---

## 2026-11-11  Trend board in Telegram
TAG:      trend-board-2026-11-11   (previous: zone-break-shadow-2026-11-11)
WHAT:     Chad posts a trend board: a FRESH scan of every pair, the same
          run_scan the Streamlit app's "Scan now" calls, laid out like the
          app (Daily/4H/1H + direction; holidays still scanned, marked ⚪).
          Once at ~9:06 in the setups chat, and on request: "Chad, trends" /
          "trend board". Where the zone-break shadow reads a timeframe
          differently it adds a "zone-break test:" line. Purpose: compare the
          app and Telegram button-press for button-press to pin down where the
          trend discrepancy the team sees comes from (stale earlier posts vs
          a different setup/price feed).
WHY:      Nov 11: the team wants the Streamlit app's view in Telegram.
          Checked first: at the same moment the app and the bot give the
          same trends (21 of 21 matched, Nov 11 21:43 vs 21:56) -- same code.
          The board shows the app's view; it does not change any trend call.
AFFECTS:  Trade decisions: no. One extra silent message per day.
REVERT:   git checkout zone-break-shadow-2026-11-11 -- chad.py vote_listener.py

---

## 2026-11-11  Zone-break trend rule in SHADOW (live trading unchanged)
TAG:      zone-break-shadow-2026-11-11   (previous: a70abea + 0fd638d)
WHAT:     The Nov 10 "push fixes" (v2) did NOT fix the flagged posts:
          replay of the 5 posts gave the same trend as live (3 of 10 team
          calls matched either way). Root cause: the bot reads trend from the
          most recent zone that FORMED; the team's rule (recorded Sept,
          restated Nov 11) is a candle BODY CLOSE through a zone -- above
          supply = bullish, below demand = bearish -- holding until the
          opposite break. `modules/trend_v2.py` now has that rule in four
          versions (b1 any unbroken zone, b2 + rejections, b3 most recent zone
          only, b4 + rejections). `replay_check.py --score` scores live, v2 and
          b1-b4 against every team call in one table; `--why` shows all of
          them. Shadow runs the version in SHADOW_TREND (.env, default b1) and
          now posts in the Chad chat whenever any timeframe's trend differs.
          Dashboard: fixed the crash on "TRADE FOUND" (entry has no
          'timeframe' field) and the out-of-date trend caption.
WHY:      Gage + Steven Nov 5-9; Devin Nov 11 ("the breaking points of a
          supply and demand zone [identify the trend]").
AFFECTS:  Trade decisions: NO. Live trend still modules/trend.py.
OPEN:     Which version is the rule (any vs most recent zone; rejections or
          not) is the team's call -- the --score table is evidence, not the
          decision.
REVERT:   git checkout a70abea -- modules/trend_v2.py modules/shadow.py replay_check.py shadow_report.py dashboard/app.py

### 2026-11-11  Dashboard: six package
COMMIT:   0fd638d (Devin). Streamlit Cloud rebuilt on Python 3.14 and the OANDA
          library no longer pulled in `six`; app crashed on load.
AFFECTS:  Dashboard only.

---

## 2026-11-10  Trend v2 in SHADOW (live trading unchanged)
TAG:      shadow-trend-2026-11-10   (previous: chad-v1-2026-11-03)
WHAT:     `modules/trend_v2.py` = the live trend rule plus two fixes:
          A "touch": a rejection only counts if the candle actually enters
            the zone (the written rule). Live code only checked one side, so a
            red candle anywhere above an old supply zone could count.
          B "push": the newest push is taken from every push, including new
            zones the zone list skips because they overlap a live one.
          The watcher runs it side by side on the SAME candles after each
          pass's posts (`modules/shadow.py`): logs every scan to
          status/shadow_DATE.csv and posts a quiet "SHADOW" line in the Chad
          chat when the fix would act differently. `py shadow_report.py`
          lists the day's differences next to the team's votes.
          `replay_check.py`: --trend v2|A|B, --why PAIR DAY HH:MM, and a
          TEAM CHECK score; 5 flagged posts added as reference days with the
          team's read (Nov 5 GBP/JPY, EUR/JPY; Nov 9 EUR/USD, GBP/USD, GBP/JPY).
          `detect_zones(dedupe=...)` parameter added; default = old behaviour.
WHY:      Gage + Steven, Nov 5-9: "marking the right zones but marking
          direction wrong", mostly the Daily.
AFFECTS:  Trade decisions: NO. Live trend still comes from modules/trend.py.
          Adds ~2 s per pair per pass, after the posts. SHADOW_TREND=off disables.
PROOF:    trend_v2 with no fixes == live trend on 400 random series.
          Shadow ran on frozen Oct 29 scans with no errors.
          Replay of the 6 earlier reference days with v2: 0 of 6 changed
          (nothing that was right before moves). The 5 flagged days need the
          desktop (OANDA): py replay_check.py --trend v2.
TO GO LIVE: only after the team confirms from the shadow log; then one line in
          scan_engine.py (import determine_trend from trend_v2), its own
          entry, replay check, tag.
REVERT:   git checkout chad-v1-2026-11-03 -- watch.py modules/zone_detector.py replay_check.py
          (or SHADOW_TREND=off and restart watch.py)

---

## 2026-11-03  Phase 1 + versioning (this entry covers several non-trade changes)

### 2026-11-03  Versioning: CHANGELOG + replay check
TAG:      chad-v1-2026-11-03   (previous: before-2026-11-03)
WHAT:     This file, `replay_check.py` (frozen-candle replays of reference
          days, save/compare), `reference_days.json` seeded with 6 days.
AFFECTS:  Trade decisions: no. Tools only.
REVERT:   delete the files.

### 2026-11-03  Chad is the fourth voter; verdict on the post; Chad chat
TAG:      chad-v1-2026-11-03
WHAT:     Chad's verdict + one-line reason is recorded in votes.csv as voter
          "Chad" and shown ON the bot's post ("Chad: ✅ VALID -- reason")
          above the team's votes. His full review goes to a separate Chad
          chat (TELEGRAM_CHAD_CHAT_ID) with a link to the post; without that
          setting he replies under the post as before. Scorecard never counts
          Chad as team.
WHY:      Nov 3 call (Gage): main chat should show trades, not Chad; Chad's
          read should be captured like the team's.
AFFECTS:  Trade decisions: no. Posts get edited once more (Chad's line).
REVERT:   git checkout before-2026-11-03 -- modules/agent_reviewer.py modules/notifier.py vote_listener.py modules/scorecard.py

### 2026-11-03  Chad always commits (no UNSURE), 25-word reason
TAG:      chad-v1-2026-11-03
WHAT:     trade_reviewer.md v1.0: VALID or INVALID only; open questions are
          graded the way the bot currently works and flagged after "Check:".
          Two Oct 27 questions added to the open list. Reason line ≤ 25 words.
WHY:      Gage, Nov 3: "it should be him saying valid or invalid."
AFFECTS:  Trade decisions: no. Chad's agreement score now counts every post.
REVERT:   git checkout before-2026-11-03 -- agent/trade_reviewer.md

### 2026-11-03  Session disagreement report
TAG:      chad-v1-2026-11-03
WHAT:     ~1:05 PM Chad posts (Chad chat, else setups) every post where the
          team or he disagreed with the bot or each other, the rule it comes
          down to, and proposed rules a teammate stated. Manual:
          `py chad.py --now disagreements`.
WHY:      Nov 3 call: stop reading votes out on calls.
AFFECTS:  Trade decisions: no.

### 2026-11-03  Gate (built, OFF)
TAG:      chad-v1-2026-11-03
WHAT:     CHAD_GATE=on makes Chad review a TRADE before it posts; INVALID holds
          it (Chad chat only). Timeout/error = post anyway. Default off.
WHY:      Phase 3 of the Nov 3 plan; turn on when Chad's agreement with the
          team holds ~90% (Gage's bar).
AFFECTS:  Trade decisions: ONLY when switched on. Adds 20-60 s before a trade posts.
REVERT:   CHAD_GATE=off in .env, restart watch.py.

### 2026-11-03  Charts in ET; ⚪ holiday line
TAG:      chad-v1-2026-11-03
WHAT:     Chart axis and boxes drawn in New York time (title says "times in
          ET"). Morning note's not-trading line starts with ⚪.
WHY:      Devin (17:00 = 1 PM confusion), Steven (holiday bubble), Nov 3.
AFFECTS:  Trade decisions: no. Chad's chart-time warning in trade_reviewer.md
          is now outdated but harmless (BOT FACTS are ET either way).

### 2026-10-29  Watcher crash fix (charts drawn off-screen)
TAG:      chad-v1-2026-11-03 (first committed here; was on the desktop since Oct 29)
WHAT:     matplotlib uses the Agg backend instead of Tk.
WHY:      "Tcl_AsyncDelete" killed the watcher right after the first posts
          (Oct 21, 22, 29 "9:05 freezes").
AFFECTS:  Trade decisions: no. Stability.

---

## Backfill (before this log existed, from git history and call notes)

### 2026-10-28  Prompt-size fix
TAG:      chad-fix-2026-10-28 (commit 25d0553)
WHAT:     Errors stored as one short line; Chad's day data capped.
WHY:      OANDA 504 page (190 KB) saved as a scan error -> Chad prompt 1.76M tokens.
AFFECTS:  Trade decisions: no.

### 2026-10-28  Zone wick-before-ready fix  ** TRADE LOGIC **
TAG:      chad-fix-2026-10-28   (previous: before-2026-10-28 = 9387a72)
WHAT:     A wick into a zone no longer kills it before the zone is ready
          (push candle + next candle of its timeframe closed). A body close
          through the zone still kills it any time.
WHY:      Gage's Oct 20 rule; GBP/CAD Oct 27 fell back to an old Friday 4H.
AFFECTS:  Trade decisions: YES. All pairs, all timeframes, including the
          zones trend is read from. More zones stay alive; newer zones that
          overlap a still-live one are skipped (GBP/CAD Mon 5 PM 4H vanished).
          Suspect for the trend complaint -- see Open leads.
PROOF:    why_no_post GBP_CAD 2026-10-27 picks the Mon 5 AM 4H 1.86995-1.87242.
          No replay check existed yet.
REVERT:   git checkout before-2026-10-28 -- modules/zone_detector.py modules/scan_engine.py

### 2026-10-28  Chad: short reviews, on-request updates, self-review, team rules
TAG:      chad-fix-2026-10-28
AFFECTS:  Trade decisions: no.

### 2026-10-22  Chad ops, Friday test day (Oct 23 only), oil from NYMEX WTI
TAG:      chad-ops-2026-10-22, friday-test-2026-10-23
AFFECTS:  Trade decisions: Friday Oct 23 only (scan_engine TEST_FRIDAYS).

### 2026-10-20/21  Chad reviews every post; trend direction shown on posts
TAG:      safe-2026-10-20 (f9706ac)
AFFECTS:  Trade decisions: no.

### 2026-10-12  No scanning on weekends   ** TRADE LOGIC (calendar) **
COMMIT:   553e25d

### 2026-10-08  Zone must wait for the NEXT candle of its timeframe   ** TRADE LOGIC **
COMMIT:   4b8b3f8   WHY: Gage, Oct 8.
AFFECTS:  Entries only (when a tap counts). Not trend.

### 2026-10-08  Setups 9 AM start; PENDING vs ACTIVE; setup voting
COMMITS:  388cac1, 932fb0f, 70f2ad8

### 2026-10-01  Setups/trades chat split, wait for zone candle close, cascade fix   ** TRADE LOGIC **
COMMIT:   40f36d1

### 2026-09-25  Aligned to Jordan's Strategy v3   ** TRADE LOGIC (large) **
COMMIT:   cfa102f
WHAT:     First-touched entry, doc 4.2 stop cascade, 4H wick tap today,
          confirmation at the 1m zone 8/16, 7-day zone age limit (entries,
          not trend), Friday/holiday calendar.

### 2026-09-15 / 09-17  Entry selection, cascade, wick tolerance   ** TRADE LOGIC **
COMMITS:  50452e9, f4f9f03

### 2026-09-09  2-of-3 trend agreement; zone invalidation direction fixed   ** TRADE LOGIC **
COMMITS:  0db99bf, 8b084fc
WHAT:     The trend rule and lookbacks (D 60 / 4H 120 / 1H 200) date from here.
