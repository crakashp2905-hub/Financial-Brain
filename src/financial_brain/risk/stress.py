"""Stress testing from what happened, not from what someone imagined might.

The usual stress test is a list of round numbers: NIFTY −10%, oil +15%, rates +100bp. The
trouble is that it requires a *second* set of assumptions to be useful - how much does this book
fall when NIFTY falls 10%? - and those assumptions are usually a single beta, estimated in calm
conditions, applied to a crisis. Beta is not stable across regimes, and the whole point of a
stress test is the regime where it is not.

So the primary method here is **historical replay**: take the book's weights, apply the actual
returns of those names over every window in the archive, and read the worst outcomes off the
result. That uses the co-movement that really occurred, including the part where correlations go
to one, and it needs no beta at all.

Four things are measured.

**Worst historical windows.** The book's own return over every rolling window of a given length,
with the worst ones named and dated. A book that would have lost 31% in March 2020 has that as a
fact about itself, not an estimate.

**Expected shortfall, empirically.** The mean of the worst 5% of windows rather than a normal
quantile. Indian equity returns are fat-tailed and the normal assumption understates precisely
the part a limit is about.

**Conditional liquidity.** What turnover did on the worst market days, measured. This matters
because every capacity limit divides by average daily value, and if that value collapses when
you need it the limit was computed against a number that does not exist in the state you need it
in. It is measured rather than assumed in either direction.

**Factor and regime conditioning.** Betas and volatilities estimated *within* regimes and within
India VIX terciles, so the stressed beta is the one that applied in stressed conditions.

## What cannot be measured here, stated plainly

Stress on the **spread** half of transaction cost is not available. The one attempt to estimate
spreads directly from this data (Corwin-Schultz 2012) produced numbers roughly tenfold too
large and was declared unusable. So a crisis widening of spreads - which is real and large - is
absent from every number below, and the capacity results are optimistic by that unmeasured
amount.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import mean, stdev

#: Rolling windows the book is replayed over, in sessions. 1 is a gap, 5 a week, 21 a month,
#: 63 a quarter - the horizons at which a drawdown limit is actually breached.
WINDOWS = (1, 5, 21, 63)
#: Tail level for expected shortfall.
ES_LEVEL = 0.95
#: A "worst market day" for the conditional-liquidity measurement is one in this bottom
#: percentile of index returns.
CRISIS_PERCENTILE = 0.05


def book_returns(con, weights: dict[str, float], *, end: date,
                 sessions: int = 2000) -> dict:
    """The book's daily return series, from its actual constituents' actual returns.

    Weights are renormalised over the names that have a return on each session, so a name that
    had not listed yet does not silently count as zero - which would damp every early window and
    make the book look more robust the further back you look.
    """
    isins = [k for k, v in weights.items() if v]
    if not isins:
        return {"dates": [], "returns": [], "coverage": []}
    rows = con.execute("""
        WITH cal AS (
            SELECT DISTINCT business_date FROM adjusted_prices
            WHERE business_date <= ? ORDER BY business_date DESC LIMIT ?
        )
        SELECT p.business_date, p.isin,
               p.close_adj / NULLIF(LAG(p.close_adj) OVER
                   (PARTITION BY p.isin ORDER BY p.business_date), 0) - 1 AS r
        FROM adjusted_prices p
        WHERE p.isin IN (SELECT UNNEST(?)) AND p.close_adj > 0
          AND p.business_date IN (SELECT business_date FROM cal)
        ORDER BY p.business_date
    """, [end, sessions + 1, isins]).fetchall()

    by_date: dict = {}
    for d, isin, r in rows:
        if r is not None and abs(r) < 0.5:
            by_date.setdefault(d, {})[isin] = r

    dates, rets, cover = [], [], []
    for d in sorted(by_date):
        have = by_date[d]
        w = {k: weights[k] for k in have if weights.get(k)}
        tot = sum(w.values())
        if tot <= 0:
            continue
        dates.append(d)
        rets.append(sum(w[k] / tot * have[k] for k in w))
        cover.append(tot / sum(v for v in weights.values() if v))
    return {"dates": dates, "returns": rets, "coverage": cover}


def rolling(returns: list[float], window: int) -> list[float]:
    """Compounded returns over every rolling window. Compounded, not summed - a −10% then a
    −10% is −19%, and over a stress horizon the difference is the part that matters."""
    if window <= 1:
        return list(returns)
    out = []
    for i in range(len(returns) - window + 1):
        acc = 1.0
        for r in returns[i:i + window]:
            acc *= 1 + r
        out.append(acc - 1)
    return out


def worst_windows(series: dict, *, windows=WINDOWS, top: int = 5) -> dict:
    """The worst outcomes the book would have had, dated."""
    rets, dates = series["returns"], series["dates"]
    out = {}
    for w in windows:
        vals = rolling(rets, w)
        if len(vals) < max(20, w):
            out[w] = {"n": len(vals), "note": "too little history for this window"}
            continue
        idx = sorted(range(len(vals)), key=lambda i: vals[i])[:top]
        k = max(1, int(math.floor((1 - ES_LEVEL) * len(vals))))
        tail = sorted(vals)[:k]
        out[w] = {
            "n": len(vals),
            "worst": [{"return": vals[i],
                       "start": dates[i], "end": dates[min(i + w - 1, len(dates) - 1)]}
                      for i in idx],
            "var": sorted(vals)[k - 1],
            "expected_shortfall": sum(tail) / len(tail),
            "tail_n": k,
            "mean": mean(vals), "vol": stdev(vals) if len(vals) > 2 else float("nan"),
        }
    return out


def conditional_liquidity(con, isins: list[str], *, end: date,
                          index_name: str = "Nifty 500",
                          sessions: int = 2000) -> dict:
    """What turnover did on the worst market days, measured rather than assumed.

    Every capacity limit divides by average daily traded value. If that value collapses in the
    state where an exit is needed, the limit was computed against a number that does not exist
    then. The ratio below is the honest input to that question.

    Turnover is only **half** of liquidity. The other half is the spread, and this archive
    cannot estimate it - see the module docstring. A turnover ratio above 1 does not mean
    exiting is cheap in a crisis; it means the volume was there, at a price this data cannot
    measure.
    """
    if not isins:
        return {}
    idx = con.execute("""
        SELECT business_date, close_level / NULLIF(LAG(close_level) OVER
                   (ORDER BY business_date), 0) - 1 AS r
        FROM index_levels WHERE index_name = ? AND close_level > 0
          AND business_date <= ? ORDER BY business_date DESC LIMIT ?
    """, [index_name, end, sessions]).fetchall()
    moves = {d: r for d, r in idx if r is not None}
    if len(moves) < 100:
        return {"note": f"only {len(moves)} index sessions for {index_name!r}"}
    ranked = sorted(moves.values())
    cutoff = ranked[max(0, int(CRISIS_PERCENTILE * len(ranked)) - 1)]
    crisis = {d for d, r in moves.items() if r <= cutoff}

    rows = con.execute("""
        SELECT isin, business_date, turnover FROM adjusted_prices
        WHERE isin IN (SELECT UNNEST(?)) AND business_date <= ?
          AND turnover > 0
        ORDER BY business_date DESC LIMIT 400000
    """, [isins, end]).fetchall()
    per: dict = {}
    for isin, d, tv in rows:
        per.setdefault(isin, {"crisis": [], "normal": []})[
            "crisis" if d in crisis else "normal"].append(tv)

    ratios = {}
    for isin, v in per.items():
        if len(v["crisis"]) >= 5 and len(v["normal"]) >= 50:
            ratios[isin] = mean(v["crisis"]) / mean(v["normal"])
    if not ratios:
        return {"note": "no name had enough crisis and normal sessions",
                "crisis_sessions": len(crisis), "index_cutoff": cutoff}
    vals = sorted(ratios.values())
    return {
        "index": index_name, "crisis_sessions": len(crisis),
        "crisis_threshold_return": cutoff,
        "names": len(ratios),
        "turnover_ratio_median": vals[len(vals) // 2],
        "turnover_ratio_mean": mean(vals),
        "turnover_ratio_p10": vals[max(0, int(0.10 * len(vals)) - 1)],
        "turnover_ratio_p90": vals[min(len(vals) - 1, int(0.90 * len(vals)))],
        "names_where_turnover_falls": sum(1 for v in vals if v < 1.0),
        "per_name": ratios,
        "caveat": "turnover only; the spread half of liquidity is not estimable on this data",
    }


def conditional_vol(con, series: dict, *, end: date, sessions: int = 2000) -> dict:
    """The book's volatility within India VIX terciles and within market regimes.

    A single volatility number describes no state the book is ever actually in. Conditioning it
    on VIX says how much worse the calm estimate gets when it stops being calm, and the ratio
    between the top and bottom tercile is the multiplier a stress test should be using instead
    of a round number.
    """
    dates, rets = series["dates"], series["returns"]
    if len(rets) < 100:
        return {"note": f"only {len(rets)} sessions"}
    by_date = dict(zip(dates, rets))

    vix = dict(con.execute("""
        SELECT business_date, close_level FROM index_levels
        WHERE index_name = 'India VIX' AND close_level > 0 AND business_date <= ?
        ORDER BY business_date DESC LIMIT ?
    """, [end, sessions]).fetchall())
    from ..regime import brain as _regime
    regimes = _regime.series(con)

    out: dict = {"overall_vol_annual": stdev(rets) * math.sqrt(250)}

    paired = [(vix[d], by_date[d]) for d in dates if d in vix]
    if len(paired) >= 90:
        paired.sort()
        k = len(paired) // 3
        buckets = {"vix_low": paired[:k], "vix_mid": paired[k:2 * k],
                   "vix_high": paired[2 * k:]}
        out["by_vix"] = {
            name: {"n": len(b), "vix_range": (b[0][0], b[-1][0]),
                   "vol_annual": (stdev([r for _, r in b]) * math.sqrt(250)
                                  if len(b) > 2 else float("nan")),
                   "mean_daily": mean([r for _, r in b])}
            for name, b in buckets.items()}
        lo = out["by_vix"]["vix_low"]["vol_annual"]
        hi = out["by_vix"]["vix_high"]["vol_annual"]
        out["vix_vol_multiplier"] = hi / lo if lo else float("nan")

    byreg: dict = {}
    for d in dates:
        byreg.setdefault(regimes.get(d, "UNKNOWN"), []).append(by_date[d])
    out["by_regime"] = {
        r: {"n": len(v), "vol_annual": (stdev(v) * math.sqrt(250)
                                        if len(v) > 2 else float("nan")),
            "mean_daily": mean(v)}
        for r, v in sorted(byreg.items(), key=lambda kv: -len(kv[1]))}
    return out


def report(con, weights: dict[str, float], *, end: date, capital_inr: float | None = None,
           sessions: int = 2000) -> dict:
    """Everything the portfolio gate needs about what this book does under stress."""
    series = book_returns(con, weights, end=end, sessions=sessions)
    if not series["returns"]:
        return {"note": "no return history for these names"}
    isins = [k for k, v in weights.items() if v]
    out = {
        "as_of": end, "names": len(isins),
        "sessions": len(series["returns"]),
        "min_coverage": min(series["coverage"]) if series["coverage"] else None,
        "windows": worst_windows(series),
        "conditional_vol": conditional_vol(con, series, end=end, sessions=sessions),
        "conditional_liquidity": conditional_liquidity(con, isins, end=end,
                                                       sessions=sessions),
    }
    if capital_inr:
        w21 = out["windows"].get(21, {})
        if "expected_shortfall" in w21:
            out["expected_shortfall_inr_21d"] = w21["expected_shortfall"] * capital_inr
            out["worst_month_inr"] = w21["worst"][0]["return"] * capital_inr
    return out
