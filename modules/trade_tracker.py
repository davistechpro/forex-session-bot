"""
Trade tracker -- follows today's trades for Chad's hourly updates:
filled or still waiting, stop loss or take profit hit, or still open and
how many pips it's up or down. Also the oil read Gage asked for.

Uses completed 1-minute candles from OANDA. If one candle touches both the
stop and the target, it's counted as a STOP (we can't tell the order inside
the candle, so we assume the worse case) and flagged.
"""
import pandas as pd
import pytz

from modules import status
from modules.data_feed import get_candles
from modules.scan_engine import pip_size

NY = pytz.timezone("America/New_York")
# Backup only: OANDA's oil CFD sits about $2 off TradingView's USOIL (different contract).
OIL = [("WTICO_USD", "US Oil (WTI, OANDA feed)"), ("BCO_USD", "Brent crude")]
# Primary: NYMEX WTI front-month futures, the price TradingView's USOIL tracks.
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/CL=F"
US_OIL_NAME = "US Oil (WTI)"


def us_oil(interval: str = "5m", rng: str = "5d", with_meta: bool = False):
    """NYMEX WTI front-month candles (time UTC, open/high/low/close).
    Empty frame if the source can't be reached. with_meta=True also returns
    the feed's info (previous session close etc.)."""
    import requests
    try:
        r = requests.get(YAHOO, params={"interval": interval, "range": rng},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({"time": pd.to_datetime(res["timestamp"], unit="s", utc=True),
                           "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]})
        df = df.dropna().reset_index(drop=True)
        return (df, res.get("meta", {})) if with_meta else df
    except Exception:
        return (pd.DataFrame(), {}) if with_meta else pd.DataFrame()


def _et(ts):
    return pd.Timestamp(ts).tz_convert(NY).strftime("%I:%M %p")


def _ts(s):
    if not s or s in ("None", "NaT"):
        return None
    t = pd.Timestamp(s)
    return t if t.tzinfo else t.tz_localize("UTC")


def follow(t: dict) -> dict:
    """Status of one recorded trade."""
    pair, d = t["pair"], t.get("direction")
    entry, stop, target = t.get("entry"), t.get("stop"), t.get("target")
    out = {**t, "state": "unknown"}
    if None in (entry, stop, target) or d not in ("LONG", "SHORT"):
        out["note"] = "missing entry/stop/target"
        return out
    m1 = get_candles(pair, "M1", count=1500)
    if len(m1) == 0:
        out["note"] = "no price data"
        return out
    pip = pip_size(pair)
    trig = _ts(t.get("triggered_at"))
    after = m1[m1["time"] >= trig] if trig is not None else m1

    filled_at = _ts(t.get("filled_at"))
    if filled_at is None:
        hit = after[after["low"] <= entry] if d == "LONG" else after[after["high"] >= entry]
        if hit.empty:
            last = m1["close"].iloc[-1]
            out.update(state="waiting for fill", price=last,
                       away_pips=round(abs(last - entry) / pip, 1))
            return out
        filled_at = hit["time"].iloc[0]
    live = m1[m1["time"] >= filled_at]
    for _, c in live.iterrows():
        hit_sl = c["low"] <= stop if d == "LONG" else c["high"] >= stop
        hit_tp = c["high"] >= target if d == "LONG" else c["low"] <= target
        if hit_sl or hit_tp:
            out.update(state="stop loss hit" if hit_sl else "take profit hit",
                       closed_at=_et(c["time"]), filled_at=_et(filled_at))
            if hit_sl and hit_tp:
                out["note"] = "stop and target in the same 1m candle -- counted as stop"
            out["pips"] = -t.get("sl_pips", 0) if hit_sl else t.get("tp_pips", 0)
            return out
    last = m1["close"].iloc[-1]
    pips = (last - entry) / pip if d == "LONG" else (entry - last) / pip
    out.update(state="open", filled_at=_et(filled_at), price=last, pips=round(pips, 1))
    return out


def trades_report() -> list:
    rows = []
    for t in status.trades_today().values():
        try:
            rows.append(follow(t))
        except Exception as e:
            rows.append({**t, "state": "couldn't check", "note": str(e)[:120]})
    return rows


def trades_text(rows: list) -> str:
    if not rows:
        return "No trades triggered today."
    lines = []
    for r in rows:
        head = f"{r['pair']} {r.get('direction')} entry {r.get('entry')} stop {r.get('stop')} target {r.get('target')}"
        s = r["state"]
        if s == "waiting for fill":
            lines.append(f"{head}: waiting for fill, price {r['price']} ({r['away_pips']} pips away)")
        elif s == "open":
            lines.append(f"{head}: OPEN since {r['filled_at']}, now {r['price']} ({r['pips']:+} pips)")
        elif s in ("stop loss hit", "take profit hit"):
            lines.append(f"{head}: {s.upper()} at {r['closed_at']} ({r['pips']:+} pips)")
        else:
            lines.append(f"{head}: {s}")
        if r.get("note"):
            lines[-1] += f" [{r['note']}]"
    return "\n".join(lines)


def oil_text() -> str:
    """Gage, Oct 22: how oil is moving for the day and for the hour (% up/down)
    and how volatile it is. NYMEX WTI front month first (matches TradingView
    USOIL), OANDA only as a backup."""
    # Current futures session only (it opens 6 PM ET), measured against the
    # previous session's close -- the same way TradingView shows the day's change.
    m5, meta = us_oil("5m", "1d", with_meta=True)
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if len(m5) and prev_close:
        last = m5["close"].iloc[-1]
        day_chg = (last - prev_close) / prev_close * 100
        hour_ago = m5[m5["time"] <= m5["time"].iloc[-1] - pd.Timedelta(hours=1)]
        hr = (f", {(last - hour_ago['close'].iloc[-1]) / hour_ago['close'].iloc[-1] * 100:+.2f}% in the last hour"
              if len(hour_ago) else "")
        lo, hi = m5["low"].min(), m5["high"].max()
        rng = f", session range {lo:.2f}-{hi:.2f} ({(hi - lo) / lo * 100:.2f}% wide)"
        return f"{US_OIL_NAME} {last:.2f}, {day_chg:+.2f}% today{hr}{rng}"
    for inst, name in OIL:
        try:
            m5 = get_candles(inst, "M5", count=300)
            d = get_candles(inst, "D", count=3)
        except Exception:
            continue
        if len(m5) == 0 or len(d) == 0:
            continue
        last = m5["close"].iloc[-1]
        prev_close = d["close"].iloc[-1]      # completed candles only, so this is yesterday's close
        day_chg = (last - prev_close) / prev_close * 100
        hour_ago = m5[m5["time"] <= m5["time"].iloc[-1] - pd.Timedelta(hours=1)]
        hr = (f", {(last - hour_ago['close'].iloc[-1]) / hour_ago['close'].iloc[-1] * 100:+.2f}% in the last hour"
              if len(hour_ago) else "")
        today = m5[m5["time"].dt.tz_convert(NY).dt.strftime("%Y-%m-%d") ==
                   pd.Timestamp.now(tz=NY).strftime("%Y-%m-%d")]
        rng = ""
        if len(today):
            lo, hi = today["low"].min(), today["high"].max()
            rng = f", today's range {lo:.2f}-{hi:.2f} ({(hi - lo) / lo * 100:.2f}% wide)"
        return f"{name} {last:.2f}, {day_chg:+.2f}% today{hr}{rng}"
    return "Oil price not available from our OANDA account."


def pair_moves_text(pairs) -> str:
    """How far each pair has moved since 9 AM ET, for the oil/currency read."""
    out = []
    nine = pd.Timestamp.now(tz=NY).normalize() + pd.Timedelta(hours=9)
    for p in pairs:
        try:
            m5 = get_candles(p, "M5", count=300)
            since = m5[m5["time"] >= nine.tz_convert("UTC")]
            if len(since) == 0:
                continue
            o, last = since["open"].iloc[0], since["close"].iloc[-1]
            out.append(f"{p} {(last - o) / pip_size(p):+.0f} pips since 9 AM")
        except Exception:
            continue
    return ", ".join(out) or "n/a"


def oil_week_data(pairs) -> str:
    """For the weekly oil report: oil's daily moves, each pair's daily moves,
    and how closely each pair moved with oil hour by hour (correlation of
    hourly % changes over the last ~5 trading days: +1 = moved together,
    -1 = moved opposite, near 0 = no link)."""
    oil, name = None, None
    h = us_oil("60m", "1mo")
    if len(h):
        oil, name = h.tail(120).reset_index(drop=True), US_OIL_NAME
    for inst, nm in ([] if oil is not None else OIL):
        try:
            h = get_candles(inst, "H1", count=120)
            if len(h):
                oil, name = h, nm
                break
        except Exception:
            continue
    if oil is None:
        return "Oil price not available from our OANDA account."
    lines = []
    try:
        d = (us_oil("1d", "10d").tail(6) if name == US_OIL_NAME
             else get_candles(OIL[[n for _, n in OIL].index(name)][0], "D", count=6))
        moves = [f"{pd.Timestamp(r.time).tz_convert(NY):%a} {((r.close - r.open) / r.open * 100):+.2f}%"
                 for r in d.itertuples()]
        lines.append(f"{name} daily moves: " + ", ".join(moves))
    except Exception:
        pass
    oil = oil.assign(time=oil["time"].dt.floor("h")).drop_duplicates("time", keep="last")
    o = oil.set_index("time")["close"].pct_change()
    for p in pairs:
        try:
            ph = get_candles(p, "H1", count=120).set_index("time")["close"]
            corr = pd.concat([o, ph.pct_change()], axis=1, join="inner").corr().iloc[0, 1]
            pd_ = get_candles(p, "D", count=6)
            wk = (pd_["close"].iloc[-1] - pd_["open"].iloc[0]) / pip_size(p)
            lines.append(f"{p}: hourly correlation with oil {corr:+.2f}, {wk:+.0f} pips over the last ~5 days")
        except Exception as e:
            lines.append(f"{p}: n/a ({type(e).__name__})")
    return "\n".join(lines)
