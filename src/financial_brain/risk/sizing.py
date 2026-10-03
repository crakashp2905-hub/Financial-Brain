"""How much to hold, when every signal you own fails in the same regime.

[[Result of the regime and decay tests]] found that all four surviving signals - two momentum
measures and two volatility measures - **reverse sign together in CRISIS**. Momentum goes from an IC of
+0.056 to -0.132; low volatility stops winning and reverses to +0.068. They are not complements. A book
holding both is doubly exposed to one regime, not hedged against it.

That makes sizing the live question, and it makes the usual sizing arithmetic wrong in a specific way:
Kelly, mean-variance and risk parity all size against an *unconditional* distribution. If the return
distribution is a mixture - a benign state most of the time and a crisis state occasionally, with the
sign flipped in the second - then the unconditional mean and variance describe a distribution the
portfolio is never actually in, and the position they imply is too large.

## What this module does

**Cornish-Fisher** corrects a normal quantile for the skew and excess kurtosis actually measured, which
is the bias flagged (before it was computed) against the Grinold estimates in
[[Result of the regime and decay tests]]: z = 1.76 for a top decile assumes a normal cross-section, and
Indian returns are not normal.

**Peak-over-threshold EVT** fits a Generalised Pareto to the tail beyond a threshold, which is the right
instrument for a crisis the sample has seen 78 times. A Gaussian tail fitted to 2,500 mostly-calm
sessions says nothing about the 78.

**Regime-mixture Kelly** sizes against the mixture rather than the average.

Applied to the regimes actually measured, **it changes almost nothing**, and that is worth stating
plainly because it is the opposite of what motivated writing it. With CRISIS at 3.1% of sessions and a
mean of -2.85% per 20 days, against a within-state dispersion of 12.3%:

    naive Kelly on pooled moments   0.831
    mixture Kelly                   0.840

The pooled version is a good approximation here - slightly conservative, if anything. The mean shift
between states is small relative to the dispersion inside them, so the mixture's extra variance barely
registers. The correction bites only when the bad state is both frequent and severe: at a 40% crisis
probability with a -20% mean it drives the position negative, and at the measured 3.1% it does not.

The sensitivity is the useful output. Position against crisis probability, holding the two state means
at their measured values:

    p(crisis)    3%     6%    10%    15%    20%    25%
    Kelly     0.724  0.640  0.528  0.392  0.260  0.128

Roughly linear, and still positive at 25%. So on these numbers the regime risk does **not** refuse the
position - which means the binding constraint on size is concentration, not regime, and that belongs
to ``risk/limits.py`` rather than here.

**Conditional drawdown at risk** measures the mean of the worst drawdowns rather than the single worst,
because one maximum drawdown is one observation and sizing against it is sizing against an anecdote.

Pure Python, like the rest of ``risk/``: the core install is duckdb + pytz.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()

#: Fraction of Kelly actually used. Full Kelly maximises log growth and is famously unholdable - its
#: drawdowns are the size of the edge's own uncertainty. Half Kelly gives three quarters of the growth
#: at half the volatility, and the estimation error in any edge measured here is large enough that
#: even that is generous.
KELLY_FRACTION = 0.5

#: Tail fraction used for the EVT threshold and for CDaR. Ten percent of 2,537 sessions is 254
#: observations, which is enough to fit two Generalised Pareto parameters.
TAIL = 0.10


class SizingError(ValueError):
    pass


def _moments(xs: list[float]) -> tuple[float, float, float, float]:
    n = len(xs)
    if n < 8:
        raise SizingError(f"{n} observations is too few for a third and fourth moment")
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        raise SizingError("zero dispersion")
    skew = (sum((x - m) ** 3 for x in xs) / n) / sd ** 3
    kurt = (sum((x - m) ** 4 for x in xs) / n) / sd ** 4 - 3.0
    return m, sd, skew, kurt


def cornish_fisher(xs: list[float], alpha: float = 0.05) -> dict:
    """A quantile corrected for the skew and excess kurtosis actually present.

    The Gaussian quantile assumes both are zero. Indian equity returns are left-skewed and fat-tailed,
    so a normal VaR understates the loss - and by exactly the amount that matters, because it is the
    tail that sizes the position.

    The expansion is only valid while it stays monotone in alpha; past roughly |skew| > 2 it can invert
    and produce a quantile that moves the wrong way. That is checked and reported rather than returned
    silently, because an inverted expansion looks like an ordinary number.
    """
    if not 0 < alpha < 0.5:
        raise SizingError(f"alpha {alpha} must be in (0, 0.5)")
    m, sd, s, k = _moments(xs)
    z = _N.inv_cdf(alpha)
    zc = (z
          + (z * z - 1) * s / 6
          + (z ** 3 - 3 * z) * k / 24
          - (2 * z ** 3 - 5 * z) * s * s / 36)
    # Monotonicity: the adjusted quantile must still fall as alpha falls.
    z2 = _N.inv_cdf(alpha / 2)
    zc2 = (z2 + (z2 * z2 - 1) * s / 6 + (z2 ** 3 - 3 * z2) * k / 24
           - (2 * z2 ** 3 - 5 * z2) * s * s / 36)
    valid = zc2 < zc
    return {
        "alpha": alpha, "mean": m, "sd": sd, "skew": s, "excess_kurtosis": k,
        "gaussian_quantile": m + z * sd,
        "cornish_fisher_quantile": m + zc * sd,
        "adjustment": (zc - z) * sd,
        "valid": valid,
        "why": ("the expansion is monotone here" if valid else
                "the expansion has inverted - |skew| or kurtosis is too large for it, and the "
                "Gaussian quantile should be used with its understatement acknowledged instead"),
    }


def pot_tail(xs: list[float], tail: float = TAIL) -> dict:
    """Generalised Pareto fit to the losses beyond a threshold, by probability-weighted moments.

    Peak-over-threshold rather than a fitted whole distribution: a Gaussian fitted to 2,500
    mostly-calm sessions is dominated by the calm ones and says nothing about the 78 that were not.
    PWM rather than maximum likelihood because it is stable at the sample sizes available here - a
    crisis tail is a few hundred observations, and ML for the GPD is unreliable below that.

    Returns the shape ``xi``: positive means a heavy tail with infinite moments beyond order 1/xi, and
    above 0.5 the variance does not exist, which makes any variance-based sizing meaningless.
    """
    if not 0 < tail < 0.5:
        raise SizingError(f"tail {tail} must be in (0, 0.5)")
    losses = sorted(-x for x in xs)                  # losses positive, ascending
    n = len(losses)
    k = int(n * tail)
    if k < 20:
        raise SizingError(f"{k} tail observations is too few to fit two GPD parameters")
    u = losses[n - k - 1]                            # threshold
    excess = sorted(x - u for x in losses[n - k:] if x > u)
    m = len(excess)
    if m < 20:
        raise SizingError(f"{m} exceedances above the threshold is too few")

    # Probability-weighted moments (Hosking & Wallis). The weight is DESCENDING in the ascending
    # sort - (m - i - 1) / (m - 1), not i / (m - 1). Getting that backwards returns a shape of 3.5
    # for a Pareto(2) tail whose true xi is 0.5, and 4.2 for a Gaussian whose true xi is 0, so the
    # estimator reported a heavier tail for the thin series than for the heavy one.
    b0 = sum(excess) / m
    b1 = sum(excess[i] * (m - i - 1) / (m - 1) for i in range(m)) / m
    if b0 <= 0 or abs(b0 - 2 * b1) < 1e-15:
        raise SizingError("degenerate exceedances")
    xi = 2 - b0 / (b0 - 2 * b1)
    beta = 2 * b0 * b1 / (b0 - 2 * b1)
    return {
        "threshold": u, "exceedances": m, "tail": tail,
        "xi": xi, "beta": beta,
        "finite_variance": xi < 0.5,
        "finite_mean": xi < 1.0,
        "why": ("xi >= 0.5: the variance does not exist, so any variance-based sizing - Kelly, "
                "mean-variance, risk parity - is meaningless on this tail"
                if xi >= 0.5 else
                "xi < 0.5: the variance exists and variance-based sizing is defined"),
    }


@dataclass
class State:
    """One regime: how often it happens, and what the signal does inside it."""

    name: str
    probability: float
    mean: float              #: expected return per period in this state
    sd: float                #: dispersion per period in this state


def mixture_kelly(states: list[State], *, fraction: float = KELLY_FRACTION,
                  max_weight: float = 0.25) -> dict:
    """The growth-optimal fraction under a regime mixture, not under the pooled average.

    The whole point. Kelly on pooled moments sizes against a distribution the portfolio is never in:
    a signal earning +5.6% in the benign state and -13.2% in a crisis has a pooled mean that describes
    neither, and the pooled variance understates how bad the bad state is because the two means are far
    apart.

    Maximises expected log wealth over the mixture by scanning - the mixture has no closed form, and a
    scan over a bounded fraction is exact enough and cannot diverge the way a Newton step can when the
    crisis branch dominates.

    A negative or zero result is the arithmetic saying **do not hold this**, and is returned rather
    than clipped, because a clipped zero and a genuinely negative edge look identical afterwards.
    """
    if not states:
        raise SizingError("no states")
    total = sum(s.probability for s in states)
    if abs(total - 1.0) > 1e-6:
        raise SizingError(f"state probabilities sum to {total:.4f}, not 1")
    if any(s.probability < 0 for s in states):
        raise SizingError("a state cannot have negative probability")

    pooled_mean = sum(s.probability * s.mean for s in states)
    pooled_var = (sum(s.probability * (s.sd ** 2 + s.mean ** 2) for s in states)
                  - pooled_mean ** 2)
    naive = pooled_mean / pooled_var if pooled_var > 0 else float("nan")

    def growth(f: float) -> float:
        """Expected log wealth, with each state's return approximated at its own mean and sd."""
        tot = 0.0
        for s in states:
            # A two-point approximation of each state: mean +/- one sd, equally weighted. Enough to
            # carry the asymmetry that matters without assuming normality inside the state.
            for r in (s.mean + s.sd, s.mean - s.sd):
                v = 1 + f * r
                if v <= 0:
                    return float("-inf")
                tot += 0.5 * s.probability * math.log(v)
        return tot

    lo, hi, best, best_g = -max_weight, max_weight, 0.0, growth(0.0)
    steps = 4001
    for i in range(steps):
        f = lo + (hi - lo) * i / (steps - 1)
        g = growth(f)
        if g > best_g:
            best_g, best = g, f

    sized = best * fraction
    worst = min(states, key=lambda s: s.mean)
    # A bound that silently binds looks exactly like an optimum. Reported, because a position at the
    # cap means the cap chose the size, not the arithmetic.
    at_bound = abs(abs(best) - max_weight) < (hi - lo) / (steps - 1) * 1.5
    return {
        "full_kelly": best,
        "at_max_weight": at_bound,
        "max_weight": max_weight,
        "sized": sized,
        "fraction_used": fraction,
        "naive_kelly_on_pooled_moments": naive,
        "overstatement": (naive - best) if not math.isnan(naive) else None,
        "pooled_mean": pooled_mean,
        "pooled_sd": math.sqrt(pooled_var) if pooled_var > 0 else 0.0,
        "worst_state": worst.name,
        "worst_state_mean": worst.mean,
        "worst_state_probability": worst.probability,
        "hold": sized > 0,
        "why": ("sized against the mixture; Kelly on pooled moments sizes against a distribution "
                "the portfolio is never in, and a non-positive result means the arithmetic says do "
                "not hold this"
                + (" - and this position is AT the max_weight cap, so the cap chose the size"
                   if at_bound else "")),
    }


def cdar(equity: list[float], alpha: float = TAIL) -> dict:
    """Conditional drawdown at risk: the mean of the worst ``alpha`` of drawdowns.

    Maximum drawdown is one observation. Sizing against it is sizing against an anecdote, and it is
    also the statistic most likely to be a data error. The mean of the worst tenth is an average over
    enough episodes to be a property of the strategy.
    """
    if len(equity) < 20:
        raise SizingError(f"{len(equity)} points is too few for a drawdown distribution")
    peak, dd = equity[0], []
    for v in equity:
        peak = max(peak, v)
        dd.append((v / peak - 1) if peak > 0 else 0.0)
    worst = sorted(dd)
    k = max(1, int(len(worst) * alpha))
    return {
        "max_drawdown": worst[0],
        "cdar": sum(worst[:k]) / k,
        "alpha": alpha,
        "episodes_averaged": k,
        "why": "max drawdown is one observation and often the one most likely to be a data error; "
               "CDaR averages the worst tenth",
    }
