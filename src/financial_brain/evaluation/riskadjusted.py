"""Alpha and beta, because raw excess is the wrong question for some factors.

Every gate in this project has asked one question of a long-only quintile: **did it out-earn
the equal-weighted universe in raw percentage terms?** For momentum that is nearly the right
question, because a momentum book carries a beta close to one and raw excess and alpha come to
almost the same number.

For low volatility it is the wrong question, and the harness has now rejected the
low-volatility anomaly three times on a criterion the anomaly does not claim to meet.
`vol_60` has the strongest rank IC of anything measured here - **t = +12.30** at a two-session
horizon - and its lowest-volatility quintile's raw excess is **negative at every horizon
tested**. Both facts are correct and they are not in tension: the claim in Haugen & Heins
(1975), Ang, Hodrick, Xing & Zhang (2006), Blitz & van Vliet (2007) and Frazzini & Pedersen
(2014) is that low-beta assets earn *more than their beta justifies* - a positive intercept
against the benchmark, not a higher raw return.

So the measurement needs the intercept.

## What is computed

    r_q - rf  =  alpha  +  beta * (r_b - rf)  +  e

`alpha` is Jensen's (1968) alpha: what the book earned that its benchmark exposure does not
explain. `beta` says how much of the benchmark it was carrying to earn it.

Standard errors are **Newey-West** (1987) with a Bartlett kernel, as the full 2x2 HAC sandwich

    Var(b) = (X'X)^-1 S (X'X)^-1,   S = sum_k w_k sum_t u_t u_(t-k) (x_t x_(t-k)' + ...)

because a regression on serially correlated rebalance returns has standard errors that are too
small and t-statistics that are too large, and that is the most common way a factor regression
overstates itself.

## The bug this file was written with, recorded because the numbers it produced were good

The first version demeaned *x* before fitting, to make the intercept's variance a one-line
long-run variance of the residuals. With *x* demeaned the intercept is **mean(y)** - the
quintile's own return over the risk-free rate, carrying no beta adjustment whatever. It
reported momentum at alpha +3.27% per 40 sessions with t = +8.78 and low volatility at t =
+4.99, and every one of those numbers was the rising market being read as skill.

The sandwich below fits on the original *x*, so `alpha = mean(y) - beta * mean(x)` is the
quantity it is named after.

## What this does not do

It does not lower any bar. An alpha is still charged the book's own transaction costs
(``costs/book.py``), still counted as a trial, still measured against the same Bonferroni
threshold, and a positive alpha carried entirely by one regime still fails the regime gate.
It changes *which number* is tested, for the factors whose claim is about risk rather than
return, and it is declared per factor rather than switched to after seeing a result.

## And the benchmark matters more than the estimator

An alpha against the **equal-weighted** universe of ~1,000 Indian names is not an alpha
against the market: that benchmark is small-cap heavy and far more volatile than any
cap-weighted index, so a low-volatility book has beta below one against it close to by
construction, and the intercept can be measuring "not holding the junk" rather than the
anomaly. Pass ``benchmark=`` a cap-weighted index series to get the version that answers the
question.
"""
from __future__ import annotations

import math
from statistics import NormalDist, mean

N = NormalDist()
#: Indian risk-free proxy, per session. ~6% a year over 250 sessions. A constant because no
#: term-structure series is in this archive yet; the alternative is silently assuming zero,
#: which biases beta and inflates alpha.
RF_ANNUAL = 0.06
SESSIONS = 250


def _hac(xs, resid, lag) -> tuple[float, float]:
    """Newey-West variances of (intercept, slope) for a simple regression on ``xs``.

    The full sandwich, not a shortcut: with regressors z_t = (1, x_t)', the meat is

        S = G_0 + sum_{k=1..L} (1 - k/(L+1)) (G_k + G_k')

    with G_k = (1/n) sum_t u_t u_{t-k} z_t z_{t-k}', and the bread is (Z'Z/n)^-1.
    """
    n = len(xs)
    if n < 4:
        return float("nan"), float("nan")
    lag = max(0, min(lag, n - 2))
    z = [(1.0, x) for x in xs]

    def gk(k):
        acc = [[0.0, 0.0], [0.0, 0.0]]
        for t in range(k, n):
            w = resid[t] * resid[t - k]
            a, b = z[t]
            c, d = z[t - k]
            acc[0][0] += w * a * c
            acc[0][1] += w * a * d
            acc[1][0] += w * b * c
            acc[1][1] += w * b * d
        return [[v / n for v in row] for row in acc]

    s = gk(0)
    for k in range(1, lag + 1):
        g = gk(k)
        w = 1 - k / (lag + 1)
        for i in range(2):
            for j in range(2):
                s[i][j] += w * (g[i][j] + g[j][i])

    # Bread: (Z'Z / n)^-1
    sx = sum(xs) / n
    sxx = sum(x * x for x in xs) / n
    det = sxx - sx * sx
    if det <= 0:
        return float("nan"), float("nan")
    inv = [[sxx / det, -sx / det], [-sx / det, 1 / det]]

    # V = inv @ s @ inv, divided by n for the estimator's variance.
    mid = [[sum(inv[i][k] * s[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    v = [[sum(mid[i][k] * inv[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    return max(v[0][0], 0.0) / n, max(v[1][1], 0.0) / n


def capm(quintile: list[float], benchmark: list[float], *, horizon: int = 20,
         rf_annual: float = RF_ANNUAL, lag: int | None = None) -> dict:
    """Jensen's alpha and beta of the quintile against the benchmark, Newey-West t on both.

    ``quintile`` and ``benchmark`` are per-rebalance *total* returns over ``horizon``
    sessions, aligned. The risk-free rate is scaled to the same horizon.
    """
    if len(quintile) != len(benchmark):
        raise ValueError("quintile and benchmark must be the same length")
    nan = float("nan")
    if len(quintile) < 6:
        return {"n": len(quintile), "alpha": nan, "beta": nan, "alpha_t": nan,
                "beta_t": nan, "rf": nan}
    rf = (1 + rf_annual) ** (horizon / SESSIONS) - 1
    xs = [b - rf for b in benchmark]
    ys = [q - rf for q in quintile]
    n = len(ys)
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return {"n": n, "alpha": nan, "beta": nan, "alpha_t": nan, "beta_t": nan, "rf": rf}
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    resid = [y - alpha - beta * x for x, y in zip(xs, ys)]
    # Rebalances are spaced `horizon` apart so the outcomes do not overlap; the lag covers
    # residual persistence rather than construction overlap. One year of rebalances.
    if lag is None:
        lag = max(1, int(SESSIONS / horizon))
    va, vb = _hac(xs, resid, lag)
    at = alpha / math.sqrt(va) if va and va > 0 else nan
    bt = (beta - 1) / math.sqrt(vb) if vb and vb > 0 else nan
    return {"n": n, "alpha": alpha, "beta": beta, "alpha_t": at,
            # beta's t is against **one**, not zero: "is this book carrying less market
            # than the benchmark" is the question, and beta differing from zero is not news.
            "beta_t_vs_one": bt,
            "alpha_p": 2 * (1 - N.cdf(abs(at))) if at == at else nan,
            "rf": rf, "lag": lag,
            "bench_mean": mean(benchmark),
            "raw_excess": mean(q - b for q, b in zip(quintile, benchmark))}


def from_series(series, *, horizon: int = 20, costs=None, turnover: float = 0.0,
                benchmark: list[float] | None = None, **kw) -> dict:
    """``capm`` from a ``benchmark.evaluate`` series, net of costs if given.

    ``costs`` is the per-rebalance round trip from ``costs.book.per_rebalance``; charging it
    to the quintile and not to the benchmark is deliberate - the benchmark is a buy-and-hold
    equal-weighted book and does not rebalance. ``benchmark`` overrides that with an external
    index's returns over the same windows, which is the comparison that matters for any
    low-beta factor.
    """
    rows = [(s, c) for s, c in zip(series, costs or [None] * len(series))
            if s.get("top_excess") is not None and s.get("universe") is not None]
    if not rows:
        return {"n": 0, "alpha": float("nan"), "beta": float("nan"),
                "alpha_t": float("nan")}
    q = [s["universe"] + s["top_excess"] - turnover * (c or 0.0) for s, c in rows]
    b = benchmark if benchmark is not None else [s["universe"] for s, _ in rows]
    return capm(q, b, horizon=horizon, **kw)
