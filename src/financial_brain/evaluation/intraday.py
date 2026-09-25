"""Intraday systems on minute bars: the four that were excluded for want of data.

``evaluation/timeseries.py`` lists four strategies from ``letianzj/QuantResearch`` (MIT)
that it could not test - dual_thrust, r_breaker, ghost_trader and dynamic_breakout_ii -
excluded on a data fact rather than an opinion, because each keys off the *session's*
opening range and the archive was daily. Kite supplies minute bars, so they are testable
now, and this module is where they are tested.

## What these systems share

All four are **opening-range breakout** systems. They compute a range from recent
sessions, place a long trigger some fraction above the open and a short trigger below,
and act when price crosses one intraday. They differ in how the range is built and how
the triggers are placed, and those differences are the whole content:

    dual_thrust            range from N days of high/low/close; symmetric k1/k2 triggers
    r_breaker              six pivot levels from yesterday's H/L/C, reversal and breakout
    dynamic_breakout_ii    lookback that adapts to volatility; Bollinger confirmation
    ghost_trader           sits out after a win, trades only after a hypothetical loss

## Long only, and why the results will look conservative

Shorting cash equity intraday is possible in India (MIS), but this project has never
modelled borrowing, margin or the auto-square-off that brokers enforce near the close, and
a backtest that shorts without them is fiction. So every system below trades the long leg
only. That systematically understates a symmetric system - and understating is the error
this project prefers to make.

## The two costs a daily backtest never had to face

**Square-off.** An intraday position is closed at the session's end whether or not the
rule says so. The close used is the last traded minute, not the official settlement price.

**Round trips.** These systems trade every session they fire, so turnover is one to two
round trips *a day* rather than a quarter. At the pessimistic `mid` bound of 70.9 bps that
is roughly 1.4% a session of friction to overcome - which is why the cost bracket built in
``costs/measured`` matters more here than anywhere else, and why a gross edge that would
be comfortable on a monthly rebalance is nothing at all on this horizon.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, stdev

#: Minutes from the open used to define the opening range, where a system needs one.
OPENING_RANGE_MIN = 15
#: NSE cash equity trades 09:15-15:30 IST; positions are squared off at the last minute.
SESSION_MINUTES = 375


def sessions(con, tradingsymbol: str, *, start=None, end=None) -> list[dict]:
    """Minute bars folded into sessions, with the pieces every system below needs.

    Returned per session: the open, the running high/low, the close, the opening range,
    and the minute series itself, so a rule can be evaluated bar by bar without another
    query per day.
    """
    rows = con.execute("""
        SELECT ts::DATE AS d, ts, open, high, low, close, volume
        FROM minute_bars
        WHERE tradingsymbol = ?
          AND (? IS NULL OR ts::DATE >= ?) AND (? IS NULL OR ts::DATE <= ?)
        ORDER BY ts""", [tradingsymbol, start, start, end, end]).fetchall()

    by_day: dict = defaultdict(list)
    for d, ts, o, h, low, c, v in rows:
        by_day[d].append({"ts": ts, "open": o, "high": h, "low": low, "close": c,
                          "volume": v})

    out = []
    for d in sorted(by_day):
        bars = by_day[d]
        if len(bars) < 60:                       # a truncated session proves nothing
            continue
        opening = bars[:OPENING_RANGE_MIN]
        out.append({
            "date": d, "bars": bars,
            "open": bars[0]["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": bars[-1]["close"],
            "or_high": max(b["high"] for b in opening),
            "or_low": min(b["low"] for b in opening),
            "minutes": len(bars),
        })
    return out


# --- the systems --------------------------------------------------------------
#
# Each takes the sessions *before* today plus today's bars, and returns a long entry
# trigger price or None. Nothing reads a bar later than the one being decided on.

def dual_thrust(history: list[dict], k1: float = 0.5, n: int = 4) -> float | None:
    """Dual Thrust: range = max(HH-LC, HC-LL) over n sessions; long above open + k1*range.

    HH/LL are the highest high and lowest low; HC/LC the highest and lowest close. The
    asymmetry is deliberate in the original and is preserved here.
    """
    if len(history) < n:
        return None
    window = history[-n:]
    hh = max(s["high"] for s in window)
    ll = min(s["low"] for s in window)
    hc = max(s["close"] for s in window)
    lc = min(s["close"] for s in window)
    rng = max(hh - lc, hc - ll)
    return rng * k1 if rng > 0 else None


def r_breaker(history: list[dict]) -> dict | None:
    """R-Breaker's six pivots from the previous session's high, low and close."""
    if not history:
        return None
    prev = history[-1]
    h, low, c = prev["high"], prev["low"], prev["close"]
    pivot = (h + low + c) / 3
    return {
        "break_buy": h + 2 * (pivot - low),          # breakout long
        "setup_sell": pivot + (h - low) * 0.35,      # reversal short trigger (unused: long only)
        "enter_buy": 2 * pivot - h,                  # reversal long trigger
        "break_sell": low - 2 * (h - pivot),
    }


def dynamic_breakout_ii(history: list[dict], base: int = 20,
                        floor_days: int = 20, ceiling_days: int = 60) -> dict | None:
    """Lookback that shortens as volatility rises, with a Bollinger confirmation.

    The original adjusts the lookback by the ratio of current to prior volatility; the
    band then has to be broken *and* the close outside the Bollinger band, which is what
    stops it trading every noisy day.
    """
    if len(history) < ceiling_days + base:
        return None
    closes = [s["close"] for s in history]
    recent = stdev(closes[-base:]) if len(closes) >= base else 0.0
    older = stdev(closes[-2 * base:-base]) if len(closes) >= 2 * base else 0.0
    if older <= 0 or recent <= 0:
        return None
    look = int(base * (older / recent))
    look = max(floor_days, min(ceiling_days, look))
    window = history[-look:]
    sma = mean(s["close"] for s in window)
    sd = stdev(s["close"] for s in window) if len(window) > 1 else 0.0
    return {"lookback": look,
            "breakout": max(s["high"] for s in window),
            "upper_band": sma + 2 * sd}


def ghost_trader(outcomes: list[float]) -> bool:
    """Trade only after a *hypothetical* loss; sit out after a hypothetical win.

    ``outcomes`` is the sequence of returns the underlying rule would have produced,
    whether or not it was taken. The premise is that breakout systems lose in streaks, so
    skipping the trade after a winner avoids the mean-reverting one that follows. It is
    the one system here whose claim is about the *strategy's own* autocorrelation rather
    than the market's, which makes it the most testable and the easiest to fool oneself
    with.
    """
    return bool(outcomes) and outcomes[-1] < 0


def _t(xs) -> float:
    return mean(xs) / stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and stdev(xs) > 0 else 0.0


# --- the backtest -------------------------------------------------------------

def _square_off_return(bars: list[dict], entry_price: float, entry_i: int,
                       stop_frac: float | None = None) -> float:
    """Return from an intraday long entered at ``entry_i``, closed at the session's end.

    A stop, if given, is checked against each subsequent bar's **low** and filled at the
    stop price - the optimistic assumption, and flagged as such: a gap through the stop
    fills worse. It is used here only to bound the loss, not to generate the edge.
    """
    stop = entry_price * (1 - stop_frac) if stop_frac else None
    for b in bars[entry_i + 1:]:
        if stop is not None and b["low"] <= stop:
            return stop / entry_price - 1
    return bars[-1]["close"] / entry_price - 1


def backtest(con, tradingsymbol: str, system: str, *, start=None, end=None,
             stop_frac: float | None = 0.01, **params) -> dict:
    """One system on one name: a trade list and the per-session returns it produced.

    Point in time throughout. The trigger for a session is computed from sessions
    *strictly before* it, and the entry scans that session's bars forward - so nothing
    reads a price later than the bar it is deciding on, and the opening range is only
    available after the minutes that form it.
    """
    days = sessions(con, tradingsymbol, start=start, end=end)
    trades, hypothetical, history = [], [], []

    for s in days:
        bars = s["bars"]
        trigger = None

        if system == "dual_thrust":
            offset = dual_thrust(history, **params)
            trigger = s["open"] + offset if offset else None
        elif system == "r_breaker":
            piv = r_breaker(history)
            trigger = piv["break_buy"] if piv else None
        elif system == "dynamic_breakout_ii":
            dbo = dynamic_breakout_ii(history, **params)
            trigger = max(dbo["breakout"], dbo["upper_band"]) if dbo else None
        elif system == "opening_range":
            # The plainest version of the family, as a reference: long if the session
            # trades above its own opening range. Included so the other three are
            # measured against the simplest thing that could work, not against zero.
            trigger = s["or_high"]
        else:
            raise ValueError(f"unknown system {system!r}")

        history.append(s)
        if trigger is None:
            continue

        # The opening range is not known until its minutes have passed.
        first = OPENING_RANGE_MIN if system == "opening_range" else 0
        entry_i = next((i for i in range(first, len(bars))
                        if bars[i]["high"] >= trigger), None)
        if entry_i is None:
            hypothetical.append(0.0)
            continue

        fill = max(trigger, bars[entry_i]["open"])   # a gap fills worse than the trigger
        ret = _square_off_return(bars, fill, entry_i, stop_frac)
        hypothetical.append(ret)
        trades.append({"date": s["date"], "entry": fill, "ret": ret,
                       "minute": entry_i,
                       "ghost_skip": ghost_trader(hypothetical[:-1])})

    return {"symbol": tradingsymbol, "system": system, "sessions": len(days),
            "trades": trades, "hypothetical": hypothetical}


def evaluate(con, symbols: list[str], system: str, *, bucket: str = "mid",
             ghost: bool = False, **kw) -> dict:
    """Aggregate a system across names, net of a round trip per trade.

    The benchmark is **cash**, not the market: an intraday system holds nothing overnight,
    so the alternative to trading is being flat, and beating flat is the whole claim.
    That is a *lower* bar than the daily strategies faced, which is worth stating plainly
    rather than letting a favourable comparison pass unnoticed.
    """
    from ..costs.india import CostModel
    cost = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000

    gross, net, per_symbol = [], [], {}
    for sym in symbols:
        r = backtest(con, sym, system, **kw)
        taken = [t for t in r["trades"] if (not ghost) or t["ghost_skip"]]
        g = [t["ret"] for t in taken]
        n = [x - cost for x in g]
        gross += g
        net += n
        per_symbol[sym] = {"sessions": r["sessions"], "trades": len(taken),
                           "fire_rate": len(taken) / max(r["sessions"], 1),
                           "mean_net": mean(n) if n else None}
    return {"system": system, "ghost": ghost, "symbols": len(symbols),
            "trades": len(net), "cost_per_trade": cost,
            "mean_gross": mean(gross) if gross else float("nan"),
            "mean_net": mean(net) if net else float("nan"),
            "t_net": _t(net), "win_rate": (sum(1 for x in net if x > 0) / len(net))
            if net else float("nan"), "per_symbol": per_symbol}
