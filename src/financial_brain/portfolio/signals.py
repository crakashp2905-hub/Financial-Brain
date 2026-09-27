"""Are two signals two bets, or one bet written twice?

A portfolio of signals is not the sum of its signals. Two rules with the same sign, holding
the same names at the same time, are a single position at double the size - and the firewall
cannot see that, because it evaluates one feature at a time against the universe and never
against another feature.

That is not a hypothetical concern here. Of the 150 trials recorded, exactly two came out net
positive with separation from a correct null, and **both are momentum-family**: `mom_12_1`
(Jegadeesh & Titman 1993) and `dist_52w_high` (George & Hwang 2004). George & Hwang's own
paper argues the 52-week high *subsumes* momentum - their claim is that proximity to the high
is what momentum was measuring all along. If that holds on this data, the project's "two
surviving candidates" is one candidate counted twice, and a book holding both is concentrated
rather than diversified.

## What is measured

**Holdings overlap.** The share of one signal's top quintile that the other also holds, per
rebalance. This is the direct question and it needs no returns.

**Return correlation.** The correlation of the two quintiles' excess returns across
rebalances. Two signals can hold different names and still be one bet if those names move
together.

**Marginal contribution.** Whether the pair's combined excess is better than the better of
the two alone, after each is charged its own turnover. Diversification that does not improve
the risk-adjusted result is a story rather than a benefit.

Nothing here is a trial: no new signal is proposed and no threshold decides anything. It is a
property of signals already tested, and the reason to measure it is that a portfolio built
from correlated survivors is riskier than its parts suggest.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

from ..costs import book
from ..evaluation import benchmark

#: Overlap above this means the two rules are substantially the same book. Not a gate - a
#: label, so a report can say "one bet" without the reader doing the arithmetic.
SAME_BOOK = 0.60


def _corr(xs, ys) -> float | None:
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[:n], ys[:n]
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _turnover(series) -> float:
    prev, ch = None, []
    for s in series:
        cur = set(s["top"] or [])
        if prev is not None and cur:
            ch.append(1 - len(cur & prev) / len(cur))
        prev = cur
    return mean(ch) if ch else 1.0


def _t(xs) -> float:
    return (mean(xs) / stdev(xs) * math.sqrt(len(xs))
            if len(xs) > 2 and stdev(xs) > 0 else float("nan"))


def overlap(con, a: str, b: str, horizon: int = 20, *,
            direction_a: int = 1, direction_b: int = 1, **kw) -> dict:
    """Holdings overlap and return correlation between two signals' top quintiles.

    Overlap is **Jaccard on the intersection over the smaller book**, not over the union: the
    question is "how much of one is inside the other", and with equal quintile sizes the two
    agree, while with unequal ones the smaller book being wholly contained is the fact worth
    reporting.
    """
    ra = benchmark.evaluate(con, a, horizon, direction=direction_a, **kw)
    rb = benchmark.evaluate(con, b, horizon, direction=direction_b, **kw)
    by_b = {s["date"]: s for s in rb["series"]}

    shares, pairs_a, pairs_b, dates = [], [], [], []
    for sa in ra["series"]:
        sb = by_b.get(sa["date"])
        if sb is None:
            continue
        ta, tb = set(sa["top"] or []), set(sb["top"] or [])
        if not ta or not tb:
            continue
        shares.append(len(ta & tb) / min(len(ta), len(tb)))
        if sa["top_excess"] is not None and sb["top_excess"] is not None:
            pairs_a.append(sa["top_excess"])
            pairs_b.append(sb["top_excess"])
            dates.append(sa["date"])

    return {"a": a, "b": b, "horizon": horizon, "rebalances": len(shares),
            "mean_overlap": mean(shares) if shares else float("nan"),
            "min_overlap": min(shares) if shares else float("nan"),
            "max_overlap": max(shares) if shares else float("nan"),
            "excess_correlation": _corr(pairs_a, pairs_b),
            "same_book": bool(shares) and mean(shares) >= SAME_BOOK,
            "aligned_rebalances": len(pairs_a), "dates": dates}


def combine(con, specs: list[tuple[str, int]], horizon: int = 20, **kw) -> dict:
    """Equal-weight the signals' top quintiles and compare against each one alone.

    Each leg is charged **its own** turnover at **its own** holdings' cost
    (``costs/book.py``), and the combination is charged the turnover of the *combined* book -
    which is lower than the average of the legs whenever they overlap, because a name held by
    both is bought once. That is the only real benefit a correlated pair can offer here, and
    measuring it is the point.
    """
    legs, per = {}, {}
    for name, d in specs:
        r = benchmark.evaluate(con, name, horizon, direction=d, **kw)
        s = [x for x in r["series"] if x["top_excess"] is not None]
        if len(s) < 6:
            continue
        cost = book.per_rebalance(con, s)
        flat = 0.007087
        turn = _turnover(r["series"])
        net = [x["top_excess"] - turn * (c if c is not None else flat)
               for x, c in zip(s, cost)]
        legs[name] = {"series": s, "turn": turn,
                      "cost": [c if c is not None else flat for c in cost],
                      "net": net, "t": _t(net), "mean": mean(net)}
        per[name] = {d2["date"]: d2 for d2 in s}

    if len(legs) < 2:
        return {"legs": legs, "combined": None}

    names = list(legs)
    common = sorted(set.intersection(*(set(per[n]) for n in names)))
    if len(common) < 6:
        return {"legs": legs, "combined": None, "note": "too few shared rebalances"}

    # The combined book is the union of the quintiles, equal-weighted by name - so a name
    # both signals hold is held once, not twice. Doubling it would be leverage disguised as
    # diversification.
    members = {d: set().union(*(set(per[n][d]["top"] or []) for n in names)) for d in common}
    prev, turns = None, []
    for d in common:
        cur = members[d]
        if prev is not None and cur:
            turns.append(1 - len(cur & prev) / len(cur))
        prev = cur
    cturn = mean(turns) if turns else 1.0

    # Gross excess of the union, approximated by the size-weighted mean of the legs' excess
    # over their shared dates. Exact would need per-name returns; the legs are equal-weighted
    # quintiles of the same universe, so the weighted mean is the union's excess up to the
    # double-counted intersection, which is stated rather than hidden.
    gross = []
    for d in common:
        w, num = 0.0, 0.0
        for n in names:
            k = len(per[n][d]["top"] or [])
            num += per[n][d]["top_excess"] * k
            w += k
        gross.append(num / w if w else 0.0)

    # Charge the combined book the mean of its legs' per-name costs - the union's liquidity
    # mix sits between them.
    mixcost = mean([mean(legs[n]["cost"]) for n in names])
    net = [g - cturn * mixcost for g in gross]
    best = max(legs, key=lambda n: legs[n]["t"])
    return {
        "legs": {n: {"t": v["t"], "mean": v["mean"], "turnover": v["turn"],
                     "cost": mean(v["cost"])} for n, v in legs.items()},
        "combined": {"rebalances": len(net), "turnover": cturn, "cost": mixcost,
                     "mean_net": mean(net), "t": _t(net)},
        "best_leg": best, "best_leg_t": legs[best]["t"],
        "improvement_t": _t(net) - legs[best]["t"],
        # A combination that does not beat its best leg is not a portfolio, it is two
        # positions in the same idea.
        "diversified": _t(net) > legs[best]["t"],
    }
