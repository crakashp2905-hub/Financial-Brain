"""Where the book's risk actually comes from, which is never where its weights are.

A position's weight says how much money is in it. Its **marginal contribution to risk** says
how much of the portfolio's volatility it is responsible for, and the two are different numbers
whenever correlations are not zero - which is always. A 4% position in a name correlated 0.8
with the rest of the book contributes more risk than an 8% position in something uncorrelated.

Everything here follows from one identity. With weights *w* and covariance *S*:

    portfolio variance    sigma^2 = w' S w
    marginal vol          d sigma / d w_i = (S w)_i / sigma
    risk contribution     RC_i = w_i * (S w)_i / sigma

and critically **sum(RC_i) = sigma exactly**, because sum_i w_i (Sw)_i = w'Sw = sigma^2. That
adding-up property is what makes risk contributions interpretable as shares: they are not a
heuristic allocation of risk, they are the Euler decomposition of a homogeneous function.

## Two different questions, and the first version of this file confused them

**How concentrated is risk across positions?** The reciprocal Herfindahl of risk shares,
`1 / sum_i (RC_i/sigma)^2`. It answers "is one position carrying everything", and it is
reported here as ``effective_positions``.

It is **not** a measure of diversification, and believing it was is a mistake worth recording.
With equal weights and equal volatilities every risk share is exactly `1/n` **whatever the
correlations are** - because `(Sw)_i` is then the same for every `i` - so the statistic returns
`n` for a book of four independent names *and* for four perfectly correlated ones. Measured:
4.00 and 4.00; and 12.00 for a book that is demonstrably two factors. A report built on it
would have said "twelve effective bets" about one sector trade.

**How many independent directions of risk are there?** That is Meucci (2009), and it is computed
on the **eigenvalue spectrum** of the covariance rather than on positions:

    p_k = lambda_k / sum(lambda)          the share of variance in direction k
    ENB_herfindahl = 1 / sum_k p_k^2
    ENB_entropy    = exp(-sum_k p_k ln p_k)

The spectrum is what responds to correlation: four perfectly correlated names have one non-zero
eigenvalue and `ENB = 1`, and a two-block book has two. Both the Herfindahl and entropy forms
are reported because they weight the tail differently - entropy is more sensitive to the many
small directions, Herfindahl to the few large ones.

The **diversification ratio**, `sum(w_i sigma_i) / sigma_p`, is kept as a third view. It was
correct in the first version (2.00 for four independent names, 1.000 for four identical ones)
and it fails differently from ENB: one tiny uncorrelated position flatters it, and does not
move the spectrum.
"""
from __future__ import annotations

import math

from . import linalg

#: Below this many effective *bets* - independent directions of risk, from the eigenvalue
#: spectrum - a book is concentrated however many names it holds. Not a gate;
#: ``risk/limits.py`` owns the gating. This is the number a report should lead with.
CONCENTRATED_ENB = 3.0


def portfolio_vol(cov: linalg.Matrix, w: list[float]) -> float:
    v = linalg.quad_form(cov, w)
    if v < 0:
        # Only reachable with a non-PD matrix, which covariance.shrink refuses to return.
        raise linalg.NotPositiveDefinite(
            f"portfolio variance came out negative ({v:.3e}); the covariance matrix is not "
            f"positive semi-definite and no volatility can be taken from it")
    return math.sqrt(v)


def contributions(cov: linalg.Matrix, w: list[float], names: list[str]) -> dict:
    """Risk contributions, marginal vols and betas to the book itself.

    ``beta_to_book`` is a position's covariance with the portfolio over the portfolio's
    variance - how much it moves when the book moves. A name with beta 2 to the book is a
    leveraged version of what is already held, whatever its own volatility is.
    """
    n = len(w)
    if len(names) != n or len(cov) != n:
        raise ValueError("weights, names and covariance must agree in size")
    sigma = portfolio_vol(cov, w)
    sw = linalg.mat_vec(cov, w)
    if sigma == 0:
        return {"sigma": 0.0, "positions": [], "effective_bets": 0.0,
                "diversification_ratio": float("nan")}
    own = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    rc = [w[i] * sw[i] / sigma for i in range(n)]
    shares = [r / sigma for r in rc]
    # Concentration of risk *across positions*. NOT diversification - see the docstring: with
    # equal weights and vols this is n whatever the correlations are.
    eff_pos = 1.0 / sum(s * s for s in shares) if any(shares) else 0.0
    # Independent directions of risk, on the sub-covariance of the names actually held. The
    # spectrum of the full matrix would count directions the book has no exposure to.
    held = [i for i in range(n) if w[i] != 0.0]
    enb = effective_bets([[cov[i][j] for j in held] for i in held]) if held else {}
    weighted_own = sum(abs(w[i]) * own[i] for i in range(n))
    return {
        "sigma": sigma,
        "sigma_annual": sigma * math.sqrt(250),
        "positions": [
            {"name": names[i], "weight": w[i], "own_vol": own[i],
             "marginal_vol": sw[i] / sigma,
             "risk_contribution": rc[i], "risk_share": shares[i],
             # A position whose risk share exceeds its weight share is amplified by the rest
             # of the book; below, it is damped. This ratio is the whole point.
             "amplification": (shares[i] / (w[i] / sum(w)) if sum(w) and w[i] else
                               float("nan")),
             "beta_to_book": sw[i] / (sigma ** 2)}
            for i in range(n)],
        # sum(RC) == sigma by the Euler identity; carried so a caller can assert it rather
        # than trust it.
        "sum_risk_contributions": sum(rc),
        "effective_positions": eff_pos,
        "effective_bets": enb.get("herfindahl", float("nan")),
        "effective_bets_entropy": enb.get("entropy", float("nan")),
        "concentrated": enb.get("herfindahl", 0.0) < CONCENTRATED_ENB,
        "first_factor_share": enb.get("first_share", float("nan")),
        "diversification_ratio": weighted_own / sigma if sigma else float("nan"),
    }


def effective_bets(cov: linalg.Matrix) -> dict:
    """Independent directions of risk, from the eigenvalue spectrum (Meucci 2009).

    Four perfectly correlated names have one non-zero eigenvalue and one bet; four independent
    ones have four. This is the statistic that responds to correlation, which the reciprocal
    Herfindahl of *position* risk shares does not.
    """
    vals, _ = linalg.eigen_sym(cov)
    pos = [v for v in vals if v > 0]
    total = sum(pos)
    if total <= 0:
        return {"herfindahl": float("nan"), "entropy": float("nan"),
                "first_share": float("nan"), "spectrum": []}
    p = [v / total for v in pos]
    return {
        "herfindahl": 1.0 / sum(x * x for x in p),
        "entropy": math.exp(-sum(x * math.log(x) for x in p if x > 0)),
        "first_share": p[0],
        "spectrum": p,
    }


def principal_components(cov: linalg.Matrix, names: list[str], *, top: int = 5) -> dict:
    """How many independent directions of risk the book has, and what they are made of.

    The first component's share of the trace is the single most compressive statistic about a
    portfolio: at 70% the book is one bet. Each component's loadings name which positions move
    together in that direction, which is what a correlation matrix shows only pairwise.
    """
    vals, vecs = linalg.eigen_sym(cov)
    total = sum(v for v in vals if v > 0)
    if total <= 0:
        return {"components": [], "first_share": float("nan")}
    out = []
    for k in range(min(top, len(vals))):
        if vals[k] <= 0:
            break
        load = [(names[i], vecs[i][k]) for i in range(len(names))]
        load.sort(key=lambda kv: -abs(kv[1]))
        out.append({"index": k, "eigenvalue": vals[k], "share": vals[k] / total,
                    "vol": math.sqrt(vals[k]),
                    "top_loadings": load[:6]})
    return {"components": out, "first_share": vals[0] / total,
            "cumulative_share": [sum(vals[:k + 1]) / total
                                 for k in range(min(top, len(vals)))]}


def marginal_add(cov: linalg.Matrix, w: list[float], names: list[str],
                 *, candidate: str, weight: float) -> dict:
    """What adding ``candidate`` at ``weight`` does to the book, before adding it.

    The covariance must already include the candidate - it is priced with the book, not against
    it. This is the calculation the portfolio gate needs: a position is accepted or refused on
    its effect on the *whole book*, not on its own merits, and "increases portfolio vol by
    18 bps while adding 31% of total risk" is a sentence a gate can act on.
    """
    if candidate not in names:
        raise ValueError(f"{candidate} must be in the covariance; price it with the book")
    j = names.index(candidate)
    before = [x for x in w]
    before[j] = 0.0
    after = [x for x in w]
    after[j] = weight

    b = contributions(cov, before, names) if any(before) else None
    a = contributions(cov, after, names)
    pos = next(p for p in a["positions"] if p["name"] == candidate)
    return {
        "candidate": candidate, "weight": weight,
        "sigma_before": b["sigma"] if b else 0.0,
        "sigma_after": a["sigma"],
        "sigma_change": a["sigma"] - (b["sigma"] if b else 0.0),
        "risk_share": pos["risk_share"],
        "amplification": pos["amplification"],
        "beta_to_book": pos["beta_to_book"],
        "effective_bets_before": b["effective_bets"] if b else 0.0,
        "effective_bets_after": a["effective_bets"],
        "effective_positions_before": b["effective_positions"] if b else 0.0,
        "effective_positions_after": a["effective_positions"],
        # The question that matters: does this position make the book more diversified or
        # just larger? Effective bets falling while notional rises is the definition of
        # concentration disguised as deployment.
        "diversifies": (a["effective_bets"] > b["effective_bets"]) if b else True,
        "max_correlation_to_book": max(
            (linalg.corr_from_cov(cov)[j][i] for i in range(len(names))
             if i != j and before[i] != 0.0), default=float("nan")),
    }
