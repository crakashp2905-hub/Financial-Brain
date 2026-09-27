"""Covariance estimated honestly, which for a portfolio of this size means shrinkage.

A book of 30 names estimated from 250 sessions asks for 465 covariances from 7,500
observations. The sample covariance matrix is then **badly conditioned** - its smallest
eigenvalues are almost pure estimation noise - and an optimiser or a marginal-risk calculation
that inverts it will load up on exactly the directions that are worst estimated. This is not a
subtle effect: it is the reason naive mean-variance optimisation fails in practice, and it gets
worse as the book gets larger, not better.

Ledoit & Wolf (2003, 2004) is the standard answer and the reason it belongs here rather than in
a tuning parameter: the shrinkage intensity is **estimated from the data**, not chosen.

    Sigma = delta * F + (1 - delta) * S

*S* is the sample covariance. *F* is the constant-correlation target: every pair assigned the
average correlation, with each name's own variance kept. *delta* comes out of the ratio of the
sample estimator's own variance to how wrong the target is, so a long history with stable
correlations shrinks little and a short history with unstable ones shrinks a lot. There is
nothing to tune and nothing to fit.

## Why the constant-correlation target and not the identity

The identity target shrinks toward "no name is correlated with any other", which for Indian
equities is a worse description of the world than "every name is correlated with every other at
about 0.35". Shrinking toward a false independence makes a book look more diversified than it
is, which is the error that matters here. Ledoit & Wolf (2004) make this argument themselves.

## Returns, not prices, and the window is stated

Log returns from the adjusted panel, so corporate actions do not appear as covariance. The
window is an argument with no default hidden in a call: a 60-session covariance and a
750-session covariance describe different worlds, and which one a limit is checked against is a
decision the caller has to make out loud.
"""
from __future__ import annotations

import math
from datetime import date

from . import linalg

#: Sessions of history required per name before it may enter a covariance estimate. Below
#: this the name's own variance is barely estimated, let alone its covariances.
MIN_OBS = 60
#: Observations per asset below which the sample matrix is reported as under-determined. At
#: exactly 1.0 the sample covariance is singular; the literature's rule of thumb for usable
#: estimates without shrinkage is well above 10.
UNDERDETERMINED = 10.0


class CovarianceError(ValueError):
    pass


def returns(con, isins: list[str], *, end: date, sessions: int = 250) -> dict:
    """Aligned log-return series per ISIN, over the last ``sessions`` up to ``end``.

    Aligned on the **intersection** of dates: a name that did not trade on a session cannot
    contribute a return for it, and filling that with zero would understate its covariance with
    everything. The count of usable sessions is returned so the caller can see what was lost.
    """
    if not isins:
        raise CovarianceError("no names given")
    rows = con.execute("""
        WITH cal AS (
            SELECT DISTINCT business_date FROM adjusted_prices
            WHERE business_date <= ? ORDER BY business_date DESC LIMIT ?
        ), px AS (
            SELECT p.isin, p.business_date, p.close_adj,
                   LN(p.close_adj / NULLIF(LAG(p.close_adj) OVER
                       (PARTITION BY p.isin ORDER BY p.business_date), 0)) AS lr
            FROM adjusted_prices p
            WHERE p.isin IN (SELECT UNNEST(?)) AND p.close_adj > 0
              AND p.business_date IN (SELECT business_date FROM cal)
        )
        SELECT isin, business_date, lr FROM px
        WHERE lr IS NOT NULL AND ABS(lr) < 0.5
        ORDER BY business_date
    """, [end, sessions + 1, isins]).fetchall()

    by_name: dict[str, dict] = {}
    for isin, d, lr in rows:
        by_name.setdefault(isin, {})[d] = lr
    present = [i for i in isins if len(by_name.get(i, {})) >= MIN_OBS]
    dropped = {i: len(by_name.get(i, {})) for i in isins if i not in present}
    if not present:
        raise CovarianceError(
            f"no name has {MIN_OBS} usable sessions in the window ending {end}")
    common = sorted(set.intersection(*(set(by_name[i]) for i in present)))
    union = sorted(set().union(*(set(by_name[i]) for i in present)))
    # **Both** are returned, and which one an estimator uses matters more than it looks.
    #
    # Strict intersection is what the first version returned, and it is quietly destructive:
    # one name with a short history truncates every other name to its length. Measured on the
    # real book - 26 names over a 250-session request - the intersection was **105 sessions**,
    # four observations per asset, and the shrinkage intensity came back at 0.998 because
    # there was almost no sample left to keep. The risk numbers were then a property of the
    # shrinkage target rather than of the data.
    #
    # ``sparse`` keeps each name's own dates so a pairwise-complete estimator can use every
    # observation each pair actually shares. ``dates``/``series`` remain the aligned form for
    # callers that need a rectangular panel.
    return {"isins": present, "dates": common, "union_dates": union, "dropped": dropped,
            "series": {i: [by_name[i][d] for d in common] for i in present},
            "sparse": {i: by_name[i] for i in present},
            "intersection_sessions": len(common), "union_sessions": len(union)}


def sample(series: dict[str, list[float]], order: list[str]) -> tuple[linalg.Matrix, list[float]]:
    """Sample covariance and means, with the n-1 denominator."""
    n = len(order)
    obs = len(series[order[0]])
    if obs < 3:
        raise CovarianceError("need at least 3 observations")
    # At obs <= n the sample covariance is **singular by construction** - it has at most
    # obs-1 non-zero eigenvalues for n dimensions - so there is no estimate to shrink, only
    # a rank-deficient matrix that a ridge would silently make invertible. Refusing is the
    # only honest option: shrinkage repairs a noisy estimate, not a missing one.
    if obs <= n + 1:
        raise CovarianceError(
            f"{obs} observations for {n} assets: the sample covariance is singular by "
            f"construction and shrinkage cannot repair a rank deficiency. Use a longer "
            f"window or fewer names.")
    if any(len(series[k]) != obs for k in order):
        raise CovarianceError("series must be aligned to the same length")
    mu = [sum(series[k]) / obs for k in order]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        si, mi = series[order[i]], mu[i]
        for j in range(i, n):
            sj, mj = series[order[j]], mu[j]
            c = sum((si[t] - mi) * (sj[t] - mj) for t in range(obs)) / (obs - 1)
            cov[i][j] = cov[j][i] = c
    return cov, mu


def sample_pairwise(sparse: dict[str, dict], order: list[str]) -> dict:
    """Sample covariance where every pair uses every session **that pair** shares.

    The alternative - a rectangular panel on the intersection of all names' dates - throws away
    most of the data whenever one name is young, and the loss is not marginal: 105 sessions
    instead of 250 on the real book. Pairwise-complete estimation uses each pair's own overlap,
    with each pair's own means, so a long-history pair is estimated from its full history
    regardless of what else is in the book.

    The cost is that the result **need not be positive semi-definite** - different pairs are
    estimated from different samples, so the matrix has no single sample behind it to guarantee
    it. That is exactly what shrinkage and the PD repair in ``shrink`` exist for, and both
    report what they did rather than fixing it quietly.
    """
    n = len(order)
    cov = [[0.0] * n for _ in range(n)]
    counts = [[0] * n for _ in range(n)]
    for i in range(n):
        si = sparse[order[i]]
        for j in range(i, n):
            sj = sparse[order[j]]
            shared = si.keys() & sj.keys() if i != j else si.keys()
            m = len(shared)
            counts[i][j] = counts[j][i] = m
            if m < 3:
                continue
            mi = sum(si[d] for d in shared) / m
            mj = sum(sj[d] for d in shared) / m
            c = sum((si[d] - mi) * (sj[d] - mj) for d in shared) / (m - 1)
            cov[i][j] = cov[j][i] = c
    flat = [counts[i][j] for i in range(n) for j in range(i, n)]
    return {"cov": cov, "counts": counts, "order": order,
            "min_pair_obs": min(flat) if flat else 0,
            "median_pair_obs": sorted(flat)[len(flat) // 2] if flat else 0,
            "diag_obs": [counts[i][i] for i in range(n)]}


def _constant_correlation_target(cov: linalg.Matrix) -> tuple[linalg.Matrix, float]:
    """F: each name's own variance, every pair at the average sample correlation."""
    n = len(cov)
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
             if sd[i] > 0 and sd[j] > 0]
    rbar = (sum(cov[i][j] / (sd[i] * sd[j]) for i, j in pairs) / len(pairs)
            if pairs else 0.0)
    f = [[cov[i][i] if i == j else rbar * sd[i] * sd[j] for j in range(n)]
         for i in range(n)]
    return f, rbar


def shrink_pairwise(sparse: dict[str, dict], order: list[str]) -> dict:
    """Shrinkage on a pairwise-complete sample, toward constant correlation.

    The intensity cannot be estimated the Ledoit-Wolf way here - their estimator for ``pi`` and
    ``rho`` needs one rectangular sample, and pairwise estimation has a different sample per
    entry. So the intensity comes from the **effective sample size** instead, using the same
    shape Ledoit-Wolf's does: shrink in proportion to how few observations there are per
    parameter.

        delta = n_params / (n_params + median_pair_obs)

    with ``n_params = n(n+1)/2``. At 250 sessions and 8 names (36 parameters) that is 0.13; at
    105 sessions and 26 names (351 parameters) it is 0.77. It is a heuristic and it is labelled
    one - ``method`` says which estimator produced the number - because calling it Ledoit-Wolf
    when it is not would misrepresent how principled it is.
    """
    ps = sample_pairwise(sparse, order)
    s_mat, counts = ps["cov"], ps["counts"]
    n = len(order)
    f, rbar = _constant_correlation_target(s_mat)
    params = n * (n + 1) / 2
    med = ps["median_pair_obs"]
    delta = params / (params + med) if med else 1.0
    cov = [[delta * f[i][j] + (1 - delta) * s_mat[i][j] for j in range(n)]
           for i in range(n)]
    ridge = 0.0
    sample_pd = linalg.is_pd(s_mat)
    if not linalg.is_pd(cov):
        before = cov[0][0]
        cov = linalg.nearest_pd(cov)
        ridge = cov[0][0] - before
    return {"cov": cov, "sample": s_mat, "target": f, "order": order,
            "delta": delta, "avg_correlation": rbar,
            "method": "pairwise_effective_n",
            "obs": med, "assets": n,
            "obs_per_asset": med / n if n else float("nan"),
            "min_pair_obs": ps["min_pair_obs"], "median_pair_obs": med,
            "pair_counts": counts,
            "underdetermined": (med / n) < UNDERDETERMINED if n else True,
            "ridge_added": ridge, "sample_is_pd": sample_pd}


def shrink(series: dict[str, list[float]], order: list[str]) -> dict:
    """Ledoit-Wolf shrinkage toward constant correlation, intensity estimated from the data.

    The three quantities the intensity needs, in Ledoit & Wolf's notation:

    ``pi``     the sum of the asymptotic variances of the sample covariance entries - how
               noisy S is;
    ``rho``    the sum of the asymptotic covariances between the target's entries and S's -
               the part of the noise the target shares and therefore does not remove;
    ``gamma``  the squared Frobenius distance between F and S - how wrong the target is.

    Then ``delta = (pi - rho) / gamma / obs``, clipped to [0, 1]. Noisy sample and a
    well-specified target shrinks toward F; plenty of data and a badly-specified target keeps S.
    """
    s, mu = sample(series, order)
    n, obs = len(order), len(series[order[0]])
    f, rbar = _constant_correlation_target(s)
    x = [[series[order[i]][t] - mu[i] for i in range(n)] for t in range(obs)]

    # pi: sum over (i,j) of Var(s_ij) estimated as mean((x_i x_j - s_ij)^2)
    pi_mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            sij = s[i][j]
            pi_mat[i][j] = sum((x[t][i] * x[t][j] - sij) ** 2 for t in range(obs)) / obs
    pi = sum(pi_mat[i][j] for i in range(n) for j in range(n))

    sd = [math.sqrt(s[i][i]) if s[i][i] > 0 else 0.0 for i in range(n)]
    # rho: diagonal terms contribute pi_ii; off-diagonal use Ledoit-Wolf's estimator for the
    # covariance between the constant-correlation target and the sample entries.
    rho = sum(pi_mat[i][i] for i in range(n))
    for i in range(n):
        for j in range(n):
            if i == j or sd[i] == 0 or sd[j] == 0:
                continue
            t_ii = sum((x[t][i] ** 2 - s[i][i]) * (x[t][i] * x[t][j] - s[i][j])
                       for t in range(obs)) / obs
            t_jj = sum((x[t][j] ** 2 - s[j][j]) * (x[t][i] * x[t][j] - s[i][j])
                       for t in range(obs)) / obs
            rho += rbar / 2 * ((sd[j] / sd[i]) * t_ii + (sd[i] / sd[j]) * t_jj)

    gamma = sum((f[i][j] - s[i][j]) ** 2 for i in range(n) for j in range(n))
    delta = 0.0 if gamma <= 0 else max(0.0, min(1.0, (pi - rho) / gamma / obs))

    cov = [[delta * f[i][j] + (1 - delta) * s[i][j] for j in range(n)] for i in range(n)]
    ridge = 0.0
    if not linalg.is_pd(cov):
        before = cov
        cov = linalg.nearest_pd(cov)
        ridge = cov[0][0] - before[0][0]

    return {"cov": cov, "sample": s, "target": f, "mean": mu, "order": order,
            "method": "ledoit_wolf",
            "delta": delta, "avg_correlation": rbar, "obs": obs, "assets": n,
            "obs_per_asset": obs / n if n else float("nan"),
            "underdetermined": (obs / n) < UNDERDETERMINED if n else True,
            "ridge_added": ridge,
            "sample_is_pd": linalg.is_pd(s)}


def annualise(cov: linalg.Matrix, *, sessions: int = 250) -> linalg.Matrix:
    """Daily covariance to annual. Scales by sessions, not sqrt - variance is additive."""
    return [[c * sessions for c in row] for row in cov]
