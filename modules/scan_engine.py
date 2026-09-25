"""
Scan Engine — aligned to Jordan's Strategy v3 (source of truth).

DIRECTION (doc 2): 2-of-3 across Daily / 4H / 1H.

ENTRY ZONE (doc 4): among the small zones nested in the 1H/4H zone,
  the one price reaches FIRST is the entry:
    DEMAND sits below price -> the HIGHEST small zone is hit first.
    SUPPLY sits above price -> the LOWEST small zone is hit first.
  No timeframe ranking. The 1H/4H zone itself is the entry only when
  nothing is nested inside it.

STOP (doc 4.2): try 8 pips; if that does not clear the next deeper
  small zone, try 10; if 10 still does not, move the ENTRY to that
  zone and repeat. Deepest level -> plain 8. TP always 2R (doc 6).

TRIGGERS (doc 5):
  4H or 1H zone formed TODAY 6:00 AM - 1:00 PM ET -> wick tap on 15m/5m.
  4H or 1H zone from an earlier day (or today before 6 AM), untouched,
  matching direction, formed within the last 7 days -> 1-minute
  confirmation. Confirmation entry is AT THE 1M ZONE, 8-pip SL, 16 TP.

CALENDAR (doc 8): no Fridays, no US bank holidays, no pair whose
  currency has a bank holiday.
"""
from datetime import datetime
from pathlib import Path

import pytz
import pandas as pd

from modules.data_feed import get_candles
from modules.zone_detector import detect_zones
from modules.trend import determine_trend
from modules.chart_render import render_chart
from modules.confirmation_entry import find_confirmation_entry

NY_TZ = pytz.timezone("America/New_York")

# --- Tunables ---
# The tap or confirmation must have happened within this many minutes.
# Doc 5.3: "a bot setting, not decided on a call."
MAX_ENTRY_AGE_MINUTES = 120

# Doc 5.3: confirmation zones must have formed within one week.
CONFIRMATION_MAX_AGE_DAYS = 7

# How far a wick may overshoot a zone and still count as a tap.
WICK_TAP_TOLERANCE_PIPS = 20

# Doc 1: zones are sourced from 6:00 AM - 1:00 PM ET.
ZONE_WINDOW_START = (6, 0)
ZONE_WINDOW_END = (13, 0)   # exclusive

# Currency -> holiday calendar (doc 8).
CURRENCY_COUNTRY = {"USD": "US", "CAD": "CA", "JPY": "JP", "GBP": "GB", "EUR": "ECB"}


def pip_size(pair: str) -> float:
    """JPY pairs quote to 3 decimals -- 1 pip = 0.01. Everything else 0.0001."""
    return 0.01 if "JPY" in pair.upper() else 0.0001


# ---------------------------------------------------------------- time

def _to_ny(ts):
    return ts.tz_convert(NY_TZ) if ts.tzinfo else NY_TZ.localize(ts)


def in_ny_window(ts, start_h=9, start_m=0, end_h=12, end_m=59):
    t = _to_ny(ts).time()
    return (t.hour, t.minute) >= (start_h, start_m) and (t.hour, t.minute) <= (end_h, end_m)


def is_ny_session_now() -> dict:
    now_ny = datetime.now(pytz.UTC).astimezone(NY_TZ)
    t = now_ny.time()
    active = (t.hour, t.minute) >= (9, 0) and (t.hour, t.minute) <= (12, 59)
    return {"active": active, "current_time_et": now_ny.strftime("%I:%M %p ET")}


def _is_same_day_session_zone(zone, reference_time) -> bool:
    """Zone formed TODAY inside the 6:00 AM - 1:00 PM ET window (doc 1, 5)."""
    origin_ny, ref_ny = _to_ny(zone["origin_time"]), _to_ny(reference_time)
    if origin_ny.date() != ref_ny.date():
        return False
    hm = (origin_ny.hour, origin_ny.minute)
    return ZONE_WINDOW_START <= hm < ZONE_WINDOW_END


def _is_same_day_narrow_window_zone(zone, reference_time) -> bool:
    """Chart-only helper: today, 6:00 AM - 12:00 PM ET."""
    origin_ny, ref_ny = _to_ny(zone["origin_time"]), _to_ny(reference_time)
    if origin_ny.date() != ref_ny.date():
        return False
    hm = (origin_ny.hour, origin_ny.minute)
    return (6, 0) <= hm < (12, 0)


def _within_confirmation_age(zone, reference_time) -> bool:
    """Doc 5.3: confirmation zones must have formed within one week."""
    return zone["origin_time"] >= reference_time - pd.Timedelta(days=CONFIRMATION_MAX_AGE_DAYS)


# ------------------------------------------------------------ calendar

def trading_day_block_reason(pair: str, when=None) -> str | None:
    """
    Doc 8: no Fridays, no US bank holidays, no pair whose currency has a
    holiday. Returns a reason string if blocked, else None.
    """
    now_ny = (when or datetime.now(pytz.UTC)).astimezone(NY_TZ)
    if now_ny.weekday() == 4:
        return "Friday"

    try:
        import holidays as _hol
    except ImportError:
        print("WARN: 'holidays' package not installed -- holiday filter OFF (py -m pip install holidays)")
        return None

    d = now_ny.date()
    currencies = ["USD"] + [c for c in pair.upper().replace("/", "_").split("_") if c != "USD"]
    for ccy in currencies:
        country = CURRENCY_COUNTRY.get(ccy)
        if not country:
            continue
        try:
            if country == "ECB":
                cal = _hol.financial_holidays("ECB", years=d.year)
            elif country == "GB":
                cal = _hol.country_holidays("GB", subdiv="ENG", years=d.year)   # London
            else:
                cal = _hol.country_holidays(country, years=d.year)
        except Exception as e:
            print(f"WARN: holiday calendar for {country} failed ({e}) -- skipping that check")
            continue
        if d in cal:
            return f"{ccy} bank holiday ({cal.get(d)})"
    return None


# --------------------------------------------------------------- zones

def _level_price(zone: dict, zone_type: str) -> float:
    """Supply is hit from below -> entry at its bottom. Demand from above -> top."""
    return zone["bottom"] if zone_type == "supply" else zone["top"]


def _nested_in(zones: list, container: dict) -> list:
    """All same-type zones overlapping the container's price range."""
    return [
        z for z in zones
        if z["type"] == container["type"]
        and not (z["top"] < container["bottom"] or container["top"] < z["bottom"])
    ]


def _plan_trade(container: dict, nested: list, zone_type: str, pip: float) -> dict:
    """
    Doc 4 / 4.2.

    Entry = the small zone price reaches first (demand: highest top,
    supply: lowest bottom). Then the stop cascade: 8 -> 10 -> move the
    entry to the next deeper zone and repeat. Deepest -> plain 8.
    """
    if not nested:
        entry = _level_price(container, zone_type)
        return _finish(entry, 8, zone_type, pip, container.get("timeframe"), [])

    # Order from first-touched to deepest.
    if zone_type == "demand":
        ladder = sorted(nested, key=lambda z: z["top"], reverse=True)
    else:
        ladder = sorted(nested, key=lambda z: z["bottom"])

    def beyond(price, entry_px):
        return price > entry_px if zone_type == "supply" else price < entry_px

    idx = 0
    while True:
        entry_zone = ladder[idx]
        entry = _level_price(entry_zone, zone_type)

        deeper = [_level_price(z, zone_type) for z in ladder[idx + 1:]
                  if beyond(_level_price(z, zone_type), entry)]
        if not deeper:
            sl_pips = 8
            break

        target = max(deeper) if zone_type == "supply" else min(deeper)
        chosen = None
        for cand in (8, 10):
            sl_try = entry + cand * pip if zone_type == "supply" else entry - cand * pip
            if beyond(sl_try, target) or abs(sl_try - target) < 1e-9:
                chosen = cand
                break
        if chosen:
            sl_pips = chosen
            break

        # "If 10 pips still does not clear it -> move the ENTRY to that level and repeat."
        nxt = next((j for j in range(idx + 1, len(ladder))
                    if beyond(_level_price(ladder[j], zone_type), entry)), None)
        if nxt is None:
            sl_pips = 10
            break
        idx = nxt

    confluence = [z.get("timeframe") for z in ladder if z is not entry_zone]
    return _finish(entry, sl_pips, zone_type, pip, entry_zone.get("timeframe"), confluence)


def _finish(entry, sl_pips, zone_type, pip, level_name, confluence):
    if zone_type == "supply":
        sl, tp = entry + sl_pips * pip, entry - 2 * sl_pips * pip
    else:
        sl, tp = entry - sl_pips * pip, entry + 2 * sl_pips * pip
    return {"entry_price": entry, "stop_loss": sl, "take_profit": tp,
            "sl_pips": sl_pips, "tp_pips": 2 * sl_pips,
            "entry_level": level_name, "confluence": confluence}


def _plan_confirmation_trade(container: dict, m1_zones: list, confirm_time, zone_type: str, pip: float):
    """
    Doc 5.2 step 4: "The entry would be at the 1m zone and set an 8 pip
    SL and a 16 pip TP." Uses the most recent unmitigated 1m zone of the
    right type that formed AFTER the confirmation candle and sits inside
    the container. Returns None if no such 1m zone exists.
    """
    cands = [z for z in _nested_in(m1_zones, container)
             if not z["mitigated"] and z["origin_time"] > confirm_time]
    if not cands:
        return None
    zone_1m = max(cands, key=lambda z: z["origin_time"])
    entry = _level_price(zone_1m, zone_type)
    plan = _finish(entry, 8, zone_type, pip, "1", [])
    plan["zone_1m"] = zone_1m
    return plan


# ------------------------------------------------------------ triggers

def _check_wick_tap_entry(m5_candles, m15_candles, zone, zone_type,
                          max_age_minutes: int = None, pip: float = 0.0001):
    """Doc 5.1: a wick into the zone on the 15m or 5m is enough. Newest first."""
    cutoff = None
    if max_age_minutes is not None:
        latest = max((df["time"].max() for df in (m15_candles, m5_candles) if len(df) > 0), default=None)
        if latest is not None:
            cutoff = latest - pd.Timedelta(minutes=max_age_minutes)

    tol = WICK_TAP_TOLERANCE_PIPS * pip
    for tf_name, tf_candles in [("M15", m15_candles), ("M5", m5_candles)]:
        for _, c in tf_candles.sort_values("time", ascending=False).iterrows():
            t = c["time"]
            if cutoff is not None and t < cutoff:
                break
            if not in_ny_window(t) or t <= zone["confirmed_time"]:
                continue
            if zone_type == "demand" and c["low"] <= zone["top"] and c["low"] >= zone["bottom"] - tol:
                return {"timeframe": tf_name, "time": t, "candle": c, "entry_price": zone["top"]}
            if zone_type == "supply" and c["high"] >= zone["bottom"] and c["high"] <= zone["top"] + tol:
                return {"timeframe": tf_name, "time": t, "candle": c, "entry_price": zone["bottom"]}
    return None


# ---------------------------------------------------------------- scan

def run_scan(pair: str, render_chart_image: bool = True) -> dict:
    result = {
        "pair": pair, "error": None, "daily_trend": None, "h4_trend": None, "h1_trend": None,
        "valid_direction": None, "deciding_tf": None, "zone": None, "zone_source_tf": None,
        "entry": None, "entry_method": None, "skip_reason": None,
        "chart_path": None, "chart_path_1h": None,
    }

    daily = get_candles(pair, "D", count=60)
    h4 = get_candles(pair, "H4", count=120)
    h1 = get_candles(pair, "H1", count=200)
    m15 = get_candles(pair, "M15", count=200)
    m5 = get_candles(pair, "M5", count=300)
    m1 = get_candles(pair, "M1", count=1500)

    reference_time = h1["time"].max() if len(h1) > 0 else pd.Timestamp.now(tz="UTC")
    pip = pip_size(pair)

    # ---- direction (doc 2) ----
    daily_trend = determine_trend(daily, "D")
    h4_trend = determine_trend(h4, "H4")
    h1_trend = determine_trend(h1, "H1")
    result.update(daily_trend=daily_trend, h4_trend=h4_trend, h1_trend=h1_trend)

    names = [("Daily", daily_trend["trend"]), ("4H", h4_trend["trend"]), ("1H", h1_trend["trend"])]
    votes = [t for _, t in names]
    if votes.count("bullish") >= 2:
        valid_direction = "bullish"
    elif votes.count("bearish") >= 2:
        valid_direction = "bearish"
    else:
        valid_direction = None
    if valid_direction:
        agreeing = [n for n, t in names if t == valid_direction]
        result["deciding_tf"] = f"{len(agreeing)}-of-3 agree ({'+'.join(agreeing)})"
    result["valid_direction"] = valid_direction
    if not valid_direction:
        return result

    zone_type_needed = "demand" if valid_direction == "bullish" else "supply"

    # ---- candidate zones: 4H first, then 1H (doc 5) ----
    h4_zones = [z for z in detect_zones(h4, "H4") if z["type"] == zone_type_needed and not z["mitigated"]]
    h1_zones = [z for z in detect_zones(h1, "H1") if z["type"] == zone_type_needed and not z["mitigated"]]
    m1_zones_all = detect_zones(m1, "1")

    entry = zone_used = zone_source_tf = entry_method = None
    fixed_plan = None      # set for confirmation trades (doc 5.2 step 4)

    for tf_label, zones in (("4H", h4_zones), ("1H", h1_zones)):
        if entry is not None:
            break

        # Regular trade: formed today 6 AM - 1 PM -> wick tap (doc 5.1)
        today = [z for z in zones if _is_same_day_session_zone(z, reference_time)]
        if today:
            zone = today[-1]
            tap = _check_wick_tap_entry(m5, m15, zone, zone_type_needed,
                                        max_age_minutes=MAX_ENTRY_AGE_MINUTES, pip=pip)
            if tap:
                entry = {"entry_time": tap["time"], "entry_candle": tap["candle"].to_dict(),
                         "entry_price": tap["entry_price"], "confirm_time": None, "breakout_time": None}
                zone_used, zone_source_tf, entry_method = zone, tf_label, "wick_tap"
                break
            if zone_used is None:
                zone_used, zone_source_tf = zone, tf_label

        # Confirmation trade: earlier zone, within 7 days -> 1m sequence (doc 5.2)
        older = [z for z in zones
                 if not _is_same_day_session_zone(z, reference_time)
                 and _within_confirmation_age(z, reference_time)]
        if older:
            zone = older[-1]
            conf = find_confirmation_entry(m1, zone, zone_type_needed, max_age_minutes=MAX_ENTRY_AGE_MINUTES)
            if conf:
                plan = _plan_confirmation_trade(zone, m1_zones_all, conf["confirm_time"], zone_type_needed, pip)
                if plan is None:
                    result["skip_reason"] = f"{tf_label} confirmation fired but no 1m zone formed after it (doc 5.2 step 4)"
                    if zone_used is None:
                        zone_used, zone_source_tf = zone, tf_label
                    continue
                entry, fixed_plan = conf, plan
                zone_used, zone_source_tf, entry_method = zone, tf_label, "confirmation_sequence"
                break
            if zone_used is None:
                zone_used, zone_source_tf = zone, tf_label

    result["zone"], result["zone_source_tf"] = zone_used, zone_source_tf

    if entry:
        if fixed_plan is not None:
            plan = fixed_plan
        else:
            candle_pools = {"15": m15, "5": m5, "1": m1}
            nested = []
            for tf_name, pool_candles in candle_pools.items():
                pool = [z for z in detect_zones(pool_candles, tf_name) if not z["mitigated"]]
                nested.extend(_nested_in(pool, zone_used))
            plan = _plan_trade(zone_used, nested, zone_type_needed, pip)

        c = entry["entry_candle"]
        result["entry"] = {
            "source_tf": zone_source_tf, "method": entry_method, "time": entry["entry_time"],
            "candle": {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]},
            "direction": "SHORT" if valid_direction == "bearish" else "LONG",
            "entry_price": plan["entry_price"], "stop_loss": plan["stop_loss"], "take_profit": plan["take_profit"],
            "sl_pips": plan["sl_pips"], "tp_pips": plan["tp_pips"], "entry_level": plan["entry_level"],
            "confluence": plan.get("confluence", []),
            "confirm_time": entry.get("confirm_time"), "breakout_time": entry.get("breakout_time"),
        }
        result["entry_method"] = entry_method

    if render_chart_image:
        _render_charts(result, pair, reference_time, zone_used, zone_source_tf, zone_type_needed,
                       entry, h4, h1, m15, m5, m1)
    return result


# -------------------------------------------------------------- charts

def _extreme_nested_zone(zones: list, container_zone: dict):
    """Chart helper: the most extreme nested zone of the container's type."""
    nested = _nested_in(zones, container_zone)
    if not nested:
        return None
    return (max(nested, key=lambda z: z["top"]) if container_zone["type"] == "supply"
            else min(nested, key=lambda z: z["bottom"]))


def _render_charts(result, pair, reference_time, zone_used, zone_source_tf, zone_type_needed,
                   entry, h4, h1, m15, m5, m1):
    out_dir = Path("rendered_charts")
    try:
        h1_zones_for_chart = [z for z in detect_zones(h1, "H1")
                              if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
        pools = {tf: [z for z in detect_zones(df, tf)
                      if _is_same_day_narrow_window_zone(z, reference_time) and not z["mitigated"]]
                 for tf, df in (("15", m15), ("5", m5), ("1", m1))}
        zones_for_chart = []
        for h1z in h1_zones_for_chart:
            zones_for_chart.append(h1z)
            for tf in ("15", "5", "1"):
                pick = _extreme_nested_zone(pools[tf], h1z)
                if pick:
                    zones_for_chart.append(pick)

        ref_ny = _to_ny(reference_time)
        s = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("06:00", "%H:%M").time()))
        e = NY_TZ.localize(datetime.combine(ref_ny.date(), datetime.strptime("13:00", "%H:%M").time()))
        m5_ny = m5.copy()
        m5_ny["time_ny"] = m5_ny["time"].dt.tz_convert(NY_TZ)
        m5_window = m5_ny[(m5_ny["time_ny"] >= s) & (m5_ny["time_ny"] <= e)].drop(columns=["time_ny"])
        out_path = out_dir / f"{pair}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if len(m5_window) > 0:
            render_chart(m5_window, zones_for_chart, [], out_path,
                         title=f"{pair} -- 6am-1pm ET session only", trade=result["entry"])
            result["chart_path"] = out_path
    except Exception as ex:
        print(f"DEBUG: session chart render failed with: {ex}")

    try:
        if zone_used:
            candle_pools = {"H4": h4, "H1": h1, "15": m15, "5": m5, "1": m1}
            tf_order = ["H4", "H1", "15", "5", "1"]
            zones_1h_chart = [zone_used]
            start_idx = tf_order.index(zone_source_tf if zone_source_tf in tf_order else "H1")
            for tf_name in tf_order[start_idx + 1:]:
                pool_zones = [z for z in detect_zones(candle_pools[tf_name], tf_name) if not z["mitigated"]]
                pick = _extreme_nested_zone(pool_zones, zone_used)
                if pick:
                    zones_1h_chart.append(pick)
            window_start = zone_used["origin_time"] - pd.Timedelta(hours=8)
            window_end = (entry["entry_time"] if entry else reference_time) + pd.Timedelta(hours=4)
            h1_window = h1[(h1["time"] >= window_start) & (h1["time"] <= window_end)]
            out_path_1h = out_dir / f"{pair}_1H_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            if len(h1_window) > 0:
                render_chart(h1_window, zones_1h_chart, [], out_path_1h,
                             title=f"{pair} 1H candles -- {zone_source_tf} {zone_type_needed.upper()} setup",
                             trade=result["entry"])
                result["chart_path_1h"] = out_path_1h
    except Exception as ex:
        print(f"DEBUG: 1H chart render failed with: {ex}")
