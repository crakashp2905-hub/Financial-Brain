"""h19: one strategy per stock, chosen from that stock's own trailing record.

The request this answers is reasonable and the naive implementation of it is the single
most effective way to fabricate an edge in quantitative finance. Both halves matter, so
both are stated before any code runs.

## Why the naive version cannot work, in arithmetic rather than caution

Pick, for each name, whichever of *S* strategies scored best on that name **over the same
history you then report**. The reported statistic is then a maximum over *S* draws, not a
draw, and the expected maximum of *S* independent standard normals is roughly

    E[max] ~ sqrt(2 ln S)

which is **+2.1 at S = 10** and **+2.6 at S = 35** - on pure noise, per name. Averaging that
choice across 1,000 names does not wash it out, because the *same* selection bias is present
in every name and they add rather than cancel. A per-name strategy library built this way
will report t near +2 with nothing inside it, and it will look like the first thing in this
project ever to work.

[[h13]] made the same point about per-name *parameters* and drew the same line. This module
holds that line for per-name *strategy choice*, which is the larger version of it.

## The legitimate version, which is what is implemented

Selection uses a **trailing window only**, and the chosen strategy is then traded **forward**
into sessions that took no part in the choice:

1. every ``REBALANCE`` sessions, for each name, score each strategy on that name over the
   last ``LOOKBACK`` sessions;
2. require ``MIN_TRADES`` entries in the window, or the name holds nothing - a strategy
   that never fired has no record to select on;
3. hold the winner until the next selection point;
4. the return earned is entirely out of sample with respect to the choice.

That is walk-forward per-name selection. It is genuinely dynamic per stock, it is not
fitted, and it has a real chance of being worth something: it lets a mean-reverting name
trade a reversion rule while a trending name trades a trend rule, which is the substantive
idea behind the request.

## And it is reported against its own null

The identical procedure runs on the Brownian-motion panel from ``evaluation/synthetic.py``.
Per-name selection on noise has a *positive* expected statistic even done walk-forward,
because the trailing window and the forward window share the same estimation noise whenever
the underlying series has any autocorrelation at all. The number that means something is
**real minus null**, never the absolute t.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

from ..costs import book
from . import timeseries as ts

#: Sessions between selection points. One month: long enough that the choice is not
#: re-rolled on noise, short enough to be "dynamic" in any useful sense.
REBALANCE = 20
#: Trailing window a strategy is judged on, per name. Roughly one year.
LOOKBACK = 250
#: Entries a strategy must have made in the window to be selectable on that name.
MIN_TRADES = 3
#: Names held at most, ranked by trailing turnover - capacity, and it bounds the work.
MAX_NAMES = 200


def positions(con, names: list[str], *, start=None, end=None,
              min_turnover: float = ts.MIN_TURNOVER,
              lag: int = ts.EXECUTION_LAG,
              max_abs: float = ts.MAX_ABS_RETURN,
              max_names: int = MAX_NAMES) -> str:
    """Build ``_genome_pos(strategy, lineage, d, pos, prev_pos, ret, bucket)``.

    One row per strategy, name and session - the per-name detail ``timeseries.series``
    aggregates away. Materialised once because the selection passes over it many times.

    **The capacity cut happens here, not after.** Sixteen strategies over the whole panel is
    forty-odd million rows, and the first version of this built all of them and then loaded
    them into Python to select from - which does not finish. Restricting ``_bars`` to the
    ``max_names`` most liquid lineages first is both the capacity constraint the hypothesis
    states and the only way the computation is tractable; doing it afterwards would have been
    the same answer at a hundred times the cost.
    """
    con.execute(ts.PANEL, [start or "1900-01-01", end or "2999-12-31"])
    # MEDIAN, not AVG: a name with one enormous session is not liquid, and averaging says
    # it is - the same error that once put a two-session IPO above HDFC Bank.
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _bars AS
        SELECT * FROM _bars WHERE lineage IN (
            SELECT lineage FROM _bars GROUP BY lineage
            HAVING MEDIAN(tv) >= {min_turnover}
            ORDER BY MEDIAN(tv) DESC NULLS LAST LIMIT {max_names})""")
    if ts._has_candles(con):
        con.execute(ts.CANDLE_JOIN)
    if ts._has_table(con, "event_flags"):
        con.execute(ts.EVENT_JOIN)
    if ts._has_table(con, "index_levels"):
        for st in [q for q in ts.VIX_JOIN.strip().split(";\n") if q.strip()]:
            con.execute(st)
    book.ensure_view(con)

    con.execute("DROP TABLE IF EXISTS _genome_pos")
    first = True
    for name in names:
        spec = ts.STRATEGIES[name]
        verb = "CREATE TEMP TABLE _genome_pos AS" if first else "INSERT INTO _genome_pos"
        con.execute(f"""
            {verb}
            WITH ind AS (
                SELECT lineage, d, c, tv, {ts._needed(spec)}
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
                SELECT lineage, d,
                       LAG(pos, {lag}) OVER q AS pos,
                       LAG(pos, {lag} + 1) OVER q AS prev_pos,
                       LAG(tv, {lag} + 1) OVER q AS tv_known,
                       c / NULLIF(LAG(c) OVER q, 0) - 1 AS ret
                FROM held
                WINDOW q AS (PARTITION BY lineage ORDER BY d)
            )
            SELECT '{name}' AS strategy, l.lineage, l.d, l.pos, l.prev_pos, l.ret,
                   COALESCE(b.bucket, 'micro') AS bucket
            FROM lagged l LEFT JOIN _liquidity_buckets b USING (d, lineage)
            WHERE l.ret IS NOT NULL AND l.pos IS NOT NULL AND l.prev_pos IS NOT NULL
              AND l.tv_known >= {min_turnover} AND ABS(l.ret) <= {max_abs}
        """)
        first = False
    return "_genome_pos"


def run(con, names: list[str] | None = None, *, rebalance: int = REBALANCE,
        lookback: int = LOOKBACK, min_trades: int = MIN_TRADES,
        max_names: int = MAX_NAMES, cheat: bool = False, **kw) -> dict:
    """Walk-forward per-name strategy selection.

    ``cheat=True`` selects on the **whole** sample instead of a trailing window. It exists
    to be run and reported, not used: it measures how much a per-name library inflates
    itself when selection and evaluation share their data, which is the number that makes
    the walk-forward result interpretable.
    """
    names = names or [n for n in ts.STRATEGIES if not n.startswith("event_")]
    positions(con, names, max_names=max_names, **kw)

    cal = [r[0] for r in con.execute(
        "SELECT DISTINCT d FROM _genome_pos ORDER BY d").fetchall()]
    if len(cal) < lookback + rebalance:
        return {"dates": 0, "excess": [], "names": names}

    # Per strategy, name and session: the excess the strategy earned on that name, and
    # whether it entered. Kept in Python because the selection is a sequential decision.
    rows = con.execute("""
        SELECT strategy, lineage, d, pos, prev_pos, ret, bucket FROM _genome_pos
        ORDER BY d
    """).fetchall()
    per: dict = {}
    universe: dict = {}
    for strat, lin, d, pos, prev, ret, bucket in rows:
        per.setdefault((strat, lin), {})[d] = (pos, prev, ret, bucket)
        universe.setdefault(d, []).append(ret)
    uni = {d: mean(v) for d, v in universe.items()}

    # Trailing score of a strategy on a name: its mean excess over the window, gross.
    def score(strat, lin, lo, hi):
        h = per.get((strat, lin))
        if not h:
            return None, 0
        ex, entries = [], 0
        for d in cal[lo:hi]:
            v = h.get(d)
            if v is None:
                continue
            pos, prev, ret, _ = v
            ex.append((ret if pos else 0.0) - uni.get(d, 0.0))
            entries += 1 if (pos and not prev) else 0
        return (mean(ex) if ex else None), entries

    # `positions` already cut the panel to the capacity set, so everything here is in it.
    lineages = sorted({lin for _, lin in per})
    rt = book.round_trips()
    excess: list[float] = []
    picks: dict = {}
    chosen_log: dict = {}
    turns, costs = [], []

    starts = range(lookback, len(cal) - 1, rebalance)
    for s in starts:
        lo, hi = (0, len(cal)) if cheat else (s - lookback, s)
        fwd = cal[s:s + rebalance]
        # Selection, per name, on the window only.
        for lin in lineages:
            best, best_s = None, None
            for strat in names:
                sc, n = score(strat, lin, lo, hi)
                if sc is None or n < min_trades:
                    continue
                if best_s is None or sc > best_s:
                    best, best_s = strat, sc
            picks[lin] = best
            if best:
                chosen_log[best] = chosen_log.get(best, 0) + 1
        # Trade the choice forward.
        for d in fwd:
            held, paid = [], []
            for lin, strat in picks.items():
                if not strat:
                    continue
                v = per.get((strat, lin), {}).get(d)
                if v is None:
                    continue
                pos, prev, ret, bucket = v
                if pos:
                    held.append(ret)
                    if not prev:
                        paid.append(rt[bucket])
            if not held:
                continue
            turn = len(paid) / max(len(held), 1)
            cost = (sum(paid) / len(paid)) if paid else 0.0
            turns.append(turn)
            costs.append(cost)
            excess.append(mean(held) - uni.get(d, 0.0) - turn * cost)

    return {"dates": len(excess), "excess": excess, "names": names,
            "selection": "in_sample" if cheat else "walk_forward",
            "rebalance": rebalance, "lookback": lookback,
            "mean_excess": mean(excess) if excess else float("nan"),
            "turnover": mean(turns) if turns else float("nan"),
            "cost": mean(costs) if costs else float("nan"),
            "t": (mean(excess) / stdev(excess) * math.sqrt(len(excess))
                  if len(excess) > 2 and stdev(excess) > 0 else float("nan")),
            "chosen": dict(sorted(chosen_log.items(), key=lambda kv: -kv[1]))}
