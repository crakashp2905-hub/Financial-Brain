"""h18: a portfolio no-trade band whose width is the name's own transaction cost.

Every earlier test here ranked names and rebalanced fully into the top of the ranking. That
pays a round trip for every change in the ranking, however small the change - which is why
turnover explains 97% of the variance in the outcomes
([[Turnover explains 97% of it]]) and why the t-statistic of a rule is mostly a
measurement of how often it trades.

Under **proportional** transaction costs the optimal policy is not to trade partially
toward a target - that is the answer for *quadratic* costs (Garleanu & Pedersen 2013) - it
is to do nothing inside a band and trade only at its edge (Constantinides 1986; Davis &
Norman 1990; Dumas & Luciano 1991). Indian cash-equity friction is dominated by STT and
stamp duty, which are strictly proportional to notional, so the band is the right object.

## Pricing the rank, which is what made the band computable

A band compares an alpha to a cost, so both have to be in return units. Grinold's
refinement (1994) is the form of the law of active management that prices a *signal*:

    alpha_i = IC * sigma_cs * z_i

the rank IC of the signal, times the cross-sectional dispersion of h-period returns, times
the name's own cross-sectional z-score. All three are measured on data already in hand, and
the IC is taken from the **trailing** rebalances only, never from the full sample - using
the sample IC to price alphas inside that same sample is look-ahead of the worst kind,
because it is invisible and it always flatters.

## The rule

At each rebalance, holding ``positions`` names, swap incumbent *j* for candidate *i* only if

    alpha_i - alpha_j > (c_i + c_j) / 2

with *c* the per-name round trip from ``costs/book.py``. The halving is the firewall's own
convention: a swap is one round trip's worth of legs shared across two names, and charging
each its full round trip would double-count. There is no free parameter - the band's width
is the measured cost.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

from ..costs import book
from ..costs.book import BUCKETS
from ..costs.india import CostModel, Segment
from . import benchmark
#: Rebalances used to estimate the IC before the book is allowed to hold anything. The
#: alphas are priced from history only, so the first window earns nothing by construction.
WARMUP = 8


def panel(con, feature: str, horizon: int = 20, *, min_adv: float = 1e7,
          direction: int = 1, start=None, end=None) -> list[dict]:
    """Per-rebalance, per-name rows: the signal's cross-sectional z-score, the forward
    return, and the name's liquidity bucket - everything the band rule needs.

    The z-score is computed **within each rebalance date**, so it carries no information
    about the level of the signal across time, only the cross-section - which is what
    Grinold's formula takes. Buckets come from ``costs/book.py`` - the **national** turnover
    rank on that date, because the impact tiers are absolute ranks and ranking inside a
    filtered population inflates every name in it.
    """
    # The feature name is interpolated into SQL, so it is checked against the registry
    # rather than trusted - a column name is the one thing here that cannot be a parameter.
    if feature not in benchmark.FEATURES:
        raise ValueError(f"feature must be one of {sorted(benchmark.FEATURES)}")
    if direction not in (1, -1):
        raise ValueError("direction must be 1 or -1")
    book.ensure_view(con)
    rows = con.execute(f"""
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), fwd AS (
            SELECT a.lineage, c.k, a.close_adj
            FROM adjusted_prices a JOIN cal c USING (business_date)
        ), base AS (
            SELECT c.business_date AS d, f.lineage,
                   CAST(f.{feature} AS DOUBLE) * {direction} AS x,
                   f.adv20,
                   COALESCE(b.close_adj,
                            (SELECT arg_max(l.close_adj, l.k) FROM fwd l
                             WHERE l.lineage = f.lineage AND l.k > c.k AND l.k < c.k + ?),
                            a.close_adj) / a.close_adj - 1 AS y
            FROM features f JOIN cal c USING (business_date)
            JOIN fwd a ON a.lineage = f.lineage AND a.k = c.k
            LEFT JOIN fwd b ON b.lineage = f.lineage AND b.k = c.k + ?
            WHERE (c.k - 1) % ? = 0 AND f.{feature} IS NOT NULL AND f.adv20 >= ?
              AND (? IS NULL OR c.business_date >= CAST(? AS DATE))
              AND (? IS NULL OR c.business_date <= CAST(? AS DATE))
              AND c.k + ? <= (SELECT MAX(k) FROM cal)
        ), scored AS (
            SELECT d, lineage, y,
                   (x - AVG(x) OVER p) / NULLIF(STDDEV_SAMP(x) OVER p, 0) AS z,
                   RANK() OVER (PARTITION BY d ORDER BY x) AS rx,
                   RANK() OVER (PARTITION BY d ORDER BY y) AS ry
            FROM base WHERE y IS NOT NULL
            WINDOW p AS (PARTITION BY d)
        )
        SELECT s.d, s.lineage, s.z, s.y, s.rx, s.ry, COALESCE(b.bucket, 'micro')
        FROM scored s LEFT JOIN _liquidity_buckets b USING (d, lineage)
        WHERE s.z IS NOT NULL ORDER BY s.d, s.z DESC
    """, [horizon, horizon, horizon, min_adv, start, start, end, end, horizon]).fetchall()
    return [{"date": r[0], "lineage": r[1], "z": r[2], "y": r[3],
             "rx": r[4], "ry": r[5], "bucket": r[6]} for r in rows]


def _spearman(rows) -> float | None:
    xs = [r["rx"] for r in rows]
    ys = [r["ry"] for r in rows]
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def run(con, feature: str, horizon: int = 20, *, positions: int = 40,
        banded: bool = True, segment: Segment = Segment.DELIVERY,
        model: CostModel | None = None, **kw) -> dict:
    """Simulate the book rebalance by rebalance.

    ``banded=False`` is the same book with the band's width set to zero: it rebalances
    fully into the top ``positions`` by alpha every period. That is the control, and the
    only difference between the two runs is the hurdle - so the comparison isolates the
    band and nothing else.
    """
    rows = panel(con, feature, horizon, **kw)
    if not rows:
        return {"feature": feature, "dates": 0, "excess": []}

    rt = book.round_trips(segment=segment, model=model)

    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)

    holdings: dict = {}                 # lineage -> bucket, the names currently held
    ic_hist: list[float] = []
    out, turns, costs, mix = [], [], [], dict.fromkeys(BUCKETS, 0)

    for d in dates:
        day = by_date[d]
        ic = _spearman(day)
        # Alphas are priced from the ICs of *earlier* rebalances only. The current one is
        # appended after it has been used, never before.
        ic_hat = mean(ic_hist) if len(ic_hist) >= WARMUP else None
        if ic is not None:
            ic_hist.append(ic)
        if ic_hat is None:
            continue

        sigma = stdev([r["y"] for r in day]) if len(day) > 2 else 0.0
        alpha = {r["lineage"]: ic_hat * sigma * r["z"] for r in day}
        here = {r["lineage"]: r for r in day}
        cost = {lin: rt[here[lin]["bucket"]] for lin in here}

        # Names that vanished from the eligible set are sold at whatever they cost, with
        # no choice in the matter; that is a cost of the rule, not an exception to it.
        forced = [lin for lin in holdings if lin not in here]
        swaps = float(len(forced))
        paid = sum(rt[holdings[lin]] / 2 for lin in forced)
        for lin in forced:
            del holdings[lin]

        ranked = sorted(here, key=lambda lin: -alpha[lin])
        if banded:
            held = [lin for lin in ranked if lin in holdings]
            free = [lin for lin in ranked if lin not in holdings]
            while len(holdings) < positions and free:
                lin = free.pop(0)
                holdings[lin] = here[lin]["bucket"]
                held.append(lin)
                swaps += 1
                paid += cost[lin] / 2
            # Incumbents cheapest to give up first, candidates strongest first.
            held.sort(key=lambda lin: alpha[lin])
            while free and held:
                i, j = free[0], held[0]
                hurdle = (cost[i] + cost[j]) / 2
                if alpha[i] - alpha[j] <= hurdle:
                    break
                del holdings[j]
                holdings[i] = here[i]["bucket"]
                free.pop(0)
                held.pop(0)
                swaps += 1
                paid += hurdle
        else:
            target = ranked[:positions]
            for lin in [x for x in holdings if x not in target]:
                swaps += 1
                paid += rt[holdings[lin]] / 2
                del holdings[lin]
            for lin in target:
                if lin not in holdings:
                    swaps += 1
                    paid += cost[lin] / 2
                holdings[lin] = here[lin]["bucket"]

        if not holdings:
            continue
        for b in holdings.values():
            mix[b] += 1
        gross = mean(here[lin]["y"] for lin in holdings)
        universe = mean(r["y"] for r in day)
        out.append({"date": d, "n": len(holdings), "gross": gross, "universe": universe,
                    "excess_gross": gross - universe,
                    "excess": gross - universe - paid / positions,
                    "turnover": swaps / positions, "cost": paid / positions})
        turns.append(swaps / positions)
        costs.append(paid / positions)

    total = sum(mix.values()) or 1
    ex = [r["excess"] for r in out]
    return {"feature": feature, "horizon": horizon, "positions": positions,
            "banded": banded, "dates": len(out), "series": out, "excess": ex,
            "mean_excess": mean(ex) if ex else float("nan"),
            "mean_excess_gross": mean([r["excess_gross"] for r in out]) if out else float("nan"),
            "turnover": mean(turns) if turns else float("nan"),
            "cost_per_period": mean(costs) if costs else float("nan"),
            "t": (mean(ex) / stdev(ex) * math.sqrt(len(ex))
                  if len(ex) > 2 and stdev(ex) > 0 else float("nan")),
            "bucket_mix": {b: k / total for b, k in mix.items()},
            "ic_used": mean(ic_hist) if ic_hist else float("nan")}
