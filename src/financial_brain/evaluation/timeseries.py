"""Time-series strategies: entry and exit rules, not a cross-sectional rank.

Everything measured in this project so far has been a **factor**: score every name, rank
them, hold the top fifth, rebalance. That shape covers momentum, value, news flow and the
rest, and ``firewall.validate`` is built around it.

It does not cover the other large family of published strategies - Donchian breakouts,
moving-average crossovers, Bollinger reversion, Faber's trend filter - which are
**stateful**: a name is bought when a condition fires and held until a *different*
condition fires, with no ranking anywhere. A quintile test cannot express "in since the
20-day high broke, out when the 10-day low breaks", so until now those strategies could
not be evaluated here at all. That was an architectural gap, not a judgement about them.

This module closes it, and hands the result to the same gates and the same trial ledger.

## How a strategy is written

A strategy is two SQL predicates over a windowed OHLC panel - when to enter, when to exit -
and the position between them is carried by the standard state-machine idiom:

    last_value(CASE WHEN entry THEN 1 WHEN exit THEN 0 END IGNORE NULLS)
        OVER (PARTITION BY lineage ORDER BY d ROWS UNBOUNDED PRECEDING)

Stateless strategies (a crossover is simply "fast above slow") set ``entry`` and ``exit``
to complementary conditions and get the same machinery for free.

## Three decisions that make the numbers mean something

**Execution lags a day.** A signal computed from a session's close cannot be traded at
that close. The position formed on day *t* earns the return from *t+1* to *t+2*. The
alternative convention - entering at the close that produced the signal - is common in
published backtests and quietly buys a day of hindsight on every trade.

**Prices are adjusted, including the highs and lows.** ``close_adj = close_raw x factor``,
so the same factor adjusts open, high and low. Running a breakout on unadjusted highs
manufactures a signal out of every split and bonus issue.

**The benchmark is the equal-weighted universe, and cash counts against it.** A strategy
flat during a rally has earned nothing while the benchmark earned something, and that
shows up as negative excess. Measuring a timing system against its own invested periods
only is how a strategy that is out of the market for most of a decade looks good. See
[[Beating the median is not an edge]].
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import NormalDist, mean, stdev

from ..costs.india import CostModel
from .firewall import (ALPHA, MIN_DSR, MIN_YEAR_AGREEMENT, REGIME_T, deflated_sharpe)

VERSION = "ts1"
N = NormalDist()
MIN_NAMES = 30              # names held on an average day; below this it is a few bets
MIN_TURNOVER = 1e7          # Rs 1cr traded, the firewall's liquidity floor
EXECUTION_LAG = 1           # sessions between the signal and the fill
MAX_ABS_RETURN = 0.50       # beyond this it is an unadjusted corporate action, not a move

#: The panel every strategy sees: corporate-action adjusted OHLC, liquid names only.
#: eod_prices carries one row per exchange per session, so the NSE EQ series is pinned -
#: it is the leg `adjusted_prices` itself is built from, and joining without that filter
#: silently doubles every bar.
PANEL = """
CREATE OR REPLACE TEMP TABLE _bars AS
SELECT p.lineage, p.business_date AS d,
       p.close_adj                AS c,
       e.high_price  * p.factor   AS h,
       e.low_price   * p.factor   AS l,
       e.open_price  * p.factor   AS o,
       p.turnover                 AS tv
FROM adjusted_prices p
JOIN eod_prices e
  ON e.isin = p.isin AND e.business_date = p.business_date
 AND e.exchange = 'NSE' AND e.series = 'EQ'
WHERE p.close_adj > 0 AND e.high_price > 0 AND e.low_price > 0
  AND p.business_date BETWEEN ? AND ?;
"""


#: Strategies drawn from letianzj/QuantResearch (MIT) and the sources it implements.
#:
#: Of that repository's fourteen backtest files, five are testable on what this project
#: holds. The rest are excluded on a data fact rather than an opinion:
#:
#:   dual_thrust, r_breaker, ghost_trader, dynamic_breakout_ii
#:       intraday systems keyed off the session's opening range - they need minute bars,
#:       and this archive is daily.
#:   comdty_roll, comdty_spread_roll
#:       commodity futures roll yield; there is no futures curve here.
#:   portfolio_optimization
#:       an allocator, not a signal - it needs a return forecast to allocate over, which
#:       is the thing this project does not yet have.
#:   buy_hold
#:       already the control, and already the benchmark every number below is net of.
#:
#: Each entry is (entry predicate, exit predicate, what it claims, where it comes from).
STRATEGIES: dict[str, dict] = {
    "ma_cross_200": {
        "entry": "c > sma_200", "exit": "c <= sma_200",
        "needs": ["sma_200"],
        "claim": "Hold a name while it trades above its 200-session average.",
        "source": "ma_cross.py; the oldest published trend filter there is.",
    },
    "ma_double_cross_50_200": {
        "entry": "sma_50 > sma_200", "exit": "sma_50 <= sma_200",
        "needs": ["sma_50", "sma_200"],
        "claim": "The golden cross: hold while the 50-session average is above the 200.",
        "source": "ma_double_cross.py.",
    },
    "faber_taa_10m": {
        "entry": "c > sma_200 AND is_month_end", "exit": "c <= sma_200 AND is_month_end",
        "needs": ["sma_200", "is_month_end"],
        "claim": "Faber's timing model: the same 10-month filter, checked monthly.",
        "source": "mebane_faber_taa.py; Faber (2007), 'A Quantitative Approach to "
                  "Tactical Asset Allocation'. Deliberately overlaps ma_cross_200 - the "
                  "difference is turnover, and turnover is what killed three earlier "
                  "candidates, so it is worth separating.",
    },
    "bollinger_reversion_20_2": {
        "entry": "c < sma_20 - 2 * sd_20", "exit": "c >= sma_20",
        "needs": ["sma_20", "sd_20"],
        "claim": "Buy two standard deviations below the 20-session mean, exit at the mean.",
        "source": "bollinger_bands.py, long-only leg. Shorting cash equity is not "
                  "available in India, so the short leg is dropped rather than assumed.",
    },
    "turtle_20_10": {
        "entry": "c > don_hi_20", "exit": "c < don_lo_10",
        "needs": ["don_hi_20", "don_lo_10"],
        "claim": "Donchian breakout: in on a 20-session high, out on a 10-session low.",
        "source": "turtle.py; the Dennis and Eckhardt system, long-only leg.",
    },
}

#: Window expressions the predicates above may reference. Every one is strictly backward
#: looking and excludes the current bar where a comparison against it would otherwise be
#: circular - a 20-day high that includes today is broken by definition.
INDICATORS = {
    "sma_20": "AVG(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
    "sma_50": "AVG(c) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)",
    "sma_200": "AVG(c) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)",
    "sd_20": "STDDEV_SAMP(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
    "don_hi_20": "MAX(h) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)",
    "don_lo_10": "MIN(l) OVER (w ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)",
    "is_month_end": "d = LAST_VALUE(d) OVER (PARTITION BY lineage, "
                    "date_trunc('month', d) ORDER BY d "
                    "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
}


def _needed(spec: dict) -> str:
    return ", ".join(f"{INDICATORS[k]} AS {k}" for k in spec["needs"])


def series(con, name: str, *, start=None, end=None, min_turnover: float = MIN_TURNOVER,
           lag: int = EXECUTION_LAG, max_abs: float = MAX_ABS_RETURN) -> list[dict]:
    """One row per session: the strategy's return, the universe's, and the turnover.

    The position formed on a bar is lagged ``lag`` sessions before it earns anything, so
    a signal computed from a close is never filled at that close.
    """
    spec = STRATEGIES[name]
    con.execute(PANEL, [start or "1900-01-01", end or "2999-12-31"])
    rows = con.execute(f"""
        WITH ind AS (
            SELECT lineage, d, c, tv, {_needed(spec)}
            FROM _bars
            WINDOW w AS (PARTITION BY lineage ORDER BY d)
        ), flagged AS (
            SELECT *, CASE WHEN {spec['entry']} THEN 1
                           WHEN {spec['exit']}  THEN 0 END AS flag
            FROM ind
        ), held AS (
            SELECT *, COALESCE(last_value(flag IGNORE NULLS) OVER (
                          PARTITION BY lineage ORDER BY d
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS pos
            FROM flagged
        ), lagged AS (
            SELECT lineage, d, c,
                   LAG(pos, {lag}) OVER (PARTITION BY lineage ORDER BY d) AS pos,
                   LAG(pos, {lag} + 1) OVER (PARTITION BY lineage ORDER BY d) AS prev_pos,
                   -- Liquidity is judged on turnover already observed when the position
                   -- was formed. Filtering on the *current* bar's turnover selects the
                   -- sessions a name moved hard, because volume spikes with price - it
                   -- inflated this universe's return from +19% to +84% a year.
                   LAG(tv, {lag} + 1) OVER (PARTITION BY lineage ORDER BY d) AS tv_known,
                   c / NULLIF(LAG(c) OVER (PARTITION BY lineage ORDER BY d), 0) - 1 AS ret
            FROM held
        )
        SELECT d,
               AVG(ret) FILTER (WHERE pos = 1)                  AS strat_gross,
               AVG(ret)                                          AS universe,
               COUNT(*) FILTER (WHERE pos = 1)                   AS held,
               COUNT(*)                                          AS eligible,
               COUNT(*) FILTER (WHERE pos = 1 AND prev_pos = 0)   AS entries
        FROM lagged
        WHERE ret IS NOT NULL AND pos IS NOT NULL AND prev_pos IS NOT NULL
          AND tv_known >= {min_turnover}
          -- Indian equities trade under circuit limits of 5-20% a session, so a move
          -- beyond 50% is a split or bonus this archive failed to adjust, not a market
          -- move. One of them corrupts an equal-weighted average; the raw panel holds a
          -- +3750% bar and 146 sessions above +100%.
          AND ABS(ret) <= {max_abs}
        GROUP BY d ORDER BY d""").fetchall()
    return [{"date": r[0], "gross": r[1], "universe": r[2], "held": r[3],
             "eligible": r[4], "entries": r[5]} for r in rows]


def run(con, name: str, *, bucket: str = "mid", **kw) -> dict:
    """The strategy's daily excess over the equal-weighted universe, net of costs.

    A session with no position earns cash - zero - and the universe's return that day
    still counts against it. That is the honest treatment of a timing system: being out
    of a rising market is a cost, not an absence of one.
    """
    rows = series(con, name, **kw)
    if not rows:
        return {"strategy": name, "days": 0, "excess": []}

    cost = CostModel().round_trip(turnover=1_000_000, bucket=bucket)["bps"] / 10_000
    excess, turns = [], []
    for r in rows:
        gross = r["gross"] if r["held"] else 0.0        # flat means cash, not absent
        # The firewall's convention, matched deliberately: turnover is the fraction of
        # the book *replaced*, and each replacement costs one round trip. Counting
        # entries and exits both would charge two round trips for one change of hands.
        denom = max(r["held"], 1)
        turn = r["entries"] / denom
        turns.append(min(turn, 2.0))
        excess.append(gross - (r["universe"] or 0.0) - min(turn, 2.0) * cost)
    return {"strategy": name, "days": len(rows), "rows": rows, "excess": excess,
            "turnover": mean(turns), "cost_round_trip": cost,
            "avg_held": mean(r["held"] for r in rows),
            "invested_days": sum(1 for r in rows if r["held"]) / len(rows)}


def _t(xs) -> float:
    return mean(xs) / stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and stdev(xs) > 0 else 0.0


def validate(con, name: str, *, record: bool = True, **kw) -> dict:
    """The same gates the cross-sectional firewall applies, and the same trial ledger.

    Sharing the ledger is the point. A time-series strategy tested here is one more trial
    against every factor ever tested, and a factor tested there is one more trial against
    these. Two separate counters would let the same search be run twice and reported as
    two independent discoveries.
    """
    r = run(con, name, **kw)
    reasons, gates = [], {}
    excess = r.get("excess") or []

    prior = con.execute("SELECT COUNT(*), VAR_SAMP(sharpe) FROM evaluation_runs").fetchone()
    trials = (prior[0] or 0) + 1

    t = _t(excess)
    p = 2 * (1 - N.cdf(abs(t)))
    gates["significance"] = bool(excess) and p * trials < ALPHA
    if not gates["significance"]:
        reasons.append(f"daily excess t={t:+.2f}, p={p:.3g} x {trials} trials "
                       f"is not < {ALPHA}")

    gates["costs"] = bool(excess) and mean(excess) > 0
    if not gates["costs"]:
        got = mean(excess) if excess else float("nan")
        reasons.append(f"excess net of costs {got:+.3%} per session "
                       f"(turnover {r.get('turnover', 0):.1%}/session, "
                       f"round trip {r.get('cost_round_trip', 0):.2%})")

    sr_var = prior[1] if prior[1] is not None else (1 / max(len(excess), 1))
    sr, dsr = deflated_sharpe(excess, trials, sr_var)
    gates["deflated_sharpe"] = dsr >= MIN_DSR
    if not gates["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.2f} < {MIN_DSR} after {trials} trials")

    by_year = defaultdict(list)
    for row, x in zip(r.get("rows", []), excess, strict=True):
        by_year[row["date"].year].append(x)
    sign = 1 if mean(excess or [0]) >= 0 else -1
    years = {y: mean(v) for y, v in by_year.items()}
    agree = sum(1 for v in years.values() if v * sign > 0) / len(years) if years else 0
    gates["walk_forward"] = agree >= MIN_YEAR_AGREEMENT
    if not gates["walk_forward"]:
        reasons.append(f"excess sign held in {agree:.0%} of years "
                       f"(< {MIN_YEAR_AGREEMENT:.0%})")

    regimes = dict(con.execute("SELECT business_date, regime FROM market_regime").fetchall())
    by_reg = defaultdict(list)
    for row, x in zip(r.get("rows", []), excess, strict=True):
        by_reg[regimes.get(row["date"], "UNKNOWN")].append(x)
    against = {g: _t(v) for g, v in by_reg.items() if _t(v) * sign < -REGIME_T}
    gates["regime"] = not against
    if against:
        reasons.append("excess significantly reversed in " + ", ".join(
            f"{g} (t={tt:+.1f})" for g, tt in against.items()))

    held = r.get("avg_held", 0)
    gates["capacity"] = held >= MIN_NAMES
    if not gates["capacity"]:
        reasons.append(f"{held:.0f} names held on an average session < {MIN_NAMES}")

    verdict = "PROMOTE" if all(gates.values()) and excess else "REJECT"
    if not excess:
        reasons.append("no sessions with outcomes")
    out = {"version": VERSION, "strategy": name, "verdict": verdict, "gates": gates,
           "reasons": reasons, "trials": trials, "days": r.get("days", 0),
           "mean_excess": mean(excess) if excess else float("nan"), "excess_t": t,
           "sharpe": sr, "deflated_sharpe": dsr, "turnover": r.get("turnover", 0),
           "avg_held": held, "invested_days": r.get("invested_days", 0),
           "excess_by_year": years,
           "excess_by_regime": {g: mean(v) for g, v in by_reg.items()}}
    if record:
        con.execute("""INSERT INTO evaluation_runs (run_at, version, feature, horizon,
                       params, dates, mean_ic, ic_t, sharpe, deflated_sharpe, verdict,
                       reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), VERSION, name, 1,
                     json.dumps({k: v for k, v in kw.items() if isinstance(v, (int, float, str))}),
                     out["days"], out["mean_excess"], t, sr, dsr, verdict,
                     json.dumps(reasons)])
    return out
