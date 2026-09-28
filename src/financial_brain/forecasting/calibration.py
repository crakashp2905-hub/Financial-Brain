"""Scoring a distributional forecast, and the estimator bias that decides who wins.

Three things have to be measured separately, because a model can be excellent at one and useless at
the others, and a single headline number hides which:

**Calibration** - when it says 30%, does it happen 30% of the time. Measured by the probability
integral transform: if the forecast distribution is right, the observation's rank within the sampled
paths is uniform. A model can be perfectly calibrated and say nothing (climatology is), so this is
necessary and not sufficient.

**Sharpness** - how narrow the distribution is, measured without reference to the observation. A
model can be sharp and wrong. Only sharp *and* calibrated is skill, which is why they are reported
as a pair and never averaged into one score.

**Skill** - CRPS against a named null, as a ratio. The absolute value of a CRPS means nothing: it is
in units of price and it scales with the instrument's volatility, so a CRPS of 12 is good for a name
at Rs 3,000 and catastrophic for one at Rs 40.

## The estimator bias that matters more than any of them

CRPS from an ensemble has two estimators in circulation. The naive one,

    CRPS = mean|x_i - y| - (1 / 2n^2) * sum_ij |x_i - x_j|

subtracts too little spread - by a factor of (n-1)/n - so it reports a **worse** score the smaller
the ensemble, for reasons that have nothing to do with the forecast. Sampling 20 paths from a good
model and 1000 from a bootstrap null, the null wins on ensemble size alone. The fair estimator
(Ferro 2014; Zamo & Naveau 2018) divides the spread term by 2n(n-1) instead, which makes the score
unbiased at any ensemble size:

    CRPS = mean|x_i - y| - (1 / 2n(n-1)) * sum_ij |x_i - x_j|

This module uses the fair estimator, and :func:`crps` refuses a single-member ensemble rather than
silently falling back to absolute error. Measured on 20 members: fair 0.2713, naive 0.3015 - an 11%
handicap that a cheap model with 1000 cheap paths does not pay.
"""
from __future__ import annotations

from .distribution import ForecastDistribution, ForecastError, quantile

#: Nominal coverage levels reported. Central intervals, so 0.90 is [p05, p95].
COVERAGE_LEVELS = (0.50, 0.80, 0.90)


def crps(sample: list[float], observed: float, *, fair: bool = True) -> float:
    """Continuous ranked probability score of an ensemble against one observation.

    Lower is better. Reduces to absolute error when the ensemble is a point mass, which is the
    property that makes it comparable across models of different kinds.

    ``fair=False`` reproduces the naive estimator only so that a test can pin the direction of the
    bias; no caller in this package should pass it.
    """
    n = len(sample)
    if n < 2:
        raise ForecastError(
            "CRPS needs at least two ensemble members; a one-member ensemble has no spread "
            "term and scoring it as absolute error flatters it against any real distribution")
    mae = sum(abs(x - observed) for x in sample) / n
    spread = 0.0
    for i, a in enumerate(sample):
        for b in sample[i + 1:]:
            spread += abs(a - b)
    spread *= 2.0                                  # the double sum counts each pair twice
    denom = (2.0 * n * (n - 1)) if fair else (2.0 * n * n)
    return mae - spread / denom


def pinball(sample: list[float], observed: float, q: float) -> float:
    """Quantile (pinball) loss at level ``q``. The proper score for a single quantile."""
    qhat = quantile(sample, q)
    return (observed - qhat) * q if observed >= qhat else (qhat - observed) * (1 - q)


def pit(sample: list[float], observed: float) -> float:
    """Probability integral transform of the observation within the ensemble.

    Uses the rank / (n+1) convention, which maps an observation below every member to 1/(n+1) rather
    than to 0. The alternative - counting the fraction of members below - puts mass at exactly 0 and
    1, which then fails a uniformity test for a reason that is an artefact of the convention rather
    than a miscalibration.
    """
    n = len(sample)
    if n < 2:
        raise ForecastError("PIT needs at least two ensemble members")
    below = sum(1 for x in sample if x < observed)
    ties = sum(1 for x in sample if x == observed)
    return (below + 0.5 * ties + 0.5) / (n + 1)


def sharpness(sample: list[float], level: float = 0.90) -> float:
    """Width of the central ``level`` interval, in price units. Lower is sharper.

    No reference to the observation, deliberately: sharpness is a property of the forecast alone,
    and mixing it with accuracy is how an overconfident model gets credit.
    """
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    return quantile(sample, hi) - quantile(sample, lo)


def _uniformity(pits: list[float], bins: int = 10) -> dict:
    """Chi-square of the PIT histogram against uniform, plus where the mass actually sits.

    The statistic alone does not say *how* a model is miscalibrated, and the two failure modes need
    opposite fixes: mass piled in the middle bins means the forecast is too wide, mass in the end
    bins means it is too narrow and the observations keep landing outside it.
    """
    n = len(pits)
    if n < bins * 5:
        return {"chi2": None, "bins": bins, "n": n,
                "why": f"{n} observations across {bins} bins is too few for the statistic"}
    counts = [0] * bins
    for p in pits:
        counts[min(int(p * bins), bins - 1)] += 1
    expected = n / bins
    chi2 = sum((c - expected) ** 2 / expected for c in counts)
    edges = counts[0] + counts[-1]
    middle = sum(counts[bins // 2 - 1: bins // 2 + 1])
    return {
        "chi2": chi2,
        "dof": bins - 1,
        "bins": bins,
        "n": n,
        "histogram": [c / n for c in counts],
        # 2/bins and 2/bins are the uniform shares of two bins each.
        "edge_mass": edges / n,
        "edge_mass_expected": 2 / bins,
        "middle_mass": middle / n,
        "shape": ("too narrow - observations land outside the interval"
                  if edges / n > 1.5 * (2 / bins) else
                  "too wide - observations pile in the middle"
                  if middle / n > 1.5 * (2 / bins) else "no strong shape"),
    }


def score(records: list[tuple[ForecastDistribution, float]], *,
          levels=COVERAGE_LEVELS) -> dict:
    """Score a set of forecasts against their realised terminal prices.

    ``records`` pairs each forecast with the price actually observed at its horizon. Returns
    calibration, sharpness and the raw CRPS - but **no verdict**, because a CRPS is not
    interpretable without a null; that comparison is :func:`skill`.
    """
    if not records:
        raise ForecastError("nothing to score")
    crpss, pits, sharps, pinballs = [], [], [], {q: [] for q in (0.05, 0.25, 0.5, 0.75, 0.95)}
    inside = {lv: 0 for lv in levels}
    horizons, models, n_paths = set(), set(), set()
    for fc, observed in records:
        sample = fc.terminal()
        crpss.append(crps(sample, observed))
        pits.append(pit(sample, observed))
        sharps.append(sharpness(sample))
        for q in pinballs:
            pinballs[q].append(pinball(sample, observed, q))
        for lv in levels:
            lo = quantile(sample, (1 - lv) / 2)
            hi = quantile(sample, 1 - (1 - lv) / 2)
            if lo <= observed <= hi:
                inside[lv] += 1
        horizons.add(fc.horizon)
        models.add(fc.model)
        n_paths.add(fc.n_paths)

    n = len(records)
    return {
        "n": n,
        "models": sorted(models),
        "horizons": sorted(horizons),
        "n_paths": sorted(n_paths),
        "crps": sum(crpss) / n,
        "crps_estimator": "fair (Ferro 2014); the naive one penalises small ensembles",
        "sharpness_90": sum(sharps) / n,
        "pinball": {f"q{int(q * 100):02d}": sum(v) / n for q, v in pinballs.items()},
        "coverage": {f"{int(lv * 100)}%": inside[lv] / n for lv in levels},
        "coverage_nominal": {f"{int(lv * 100)}%": lv for lv in levels},
        "calibration": _uniformity(pits),
    }


def skill(candidate: dict, reference: dict) -> dict:
    """CRPS skill score of a candidate against a null. Positive is better than the null.

    ``1 - CRPS_candidate / CRPS_reference``, which is 0 when the candidate matches the null and 1
    for a perfect forecast. Negative means the null wins, and for a price forecaster that is the
    common case rather than the surprising one.

    Refuses to compare scores computed over different horizons or different numbers of
    observations - a skill score against a null evaluated on a different sample is not a skill
    score, and this has to be a refusal rather than a warning because the number it would return
    looks entirely reasonable.
    """
    if candidate["horizons"] != reference["horizons"]:
        raise ForecastError(
            f"horizons differ: {candidate['horizons']} vs {reference['horizons']}")
    if candidate["n"] != reference["n"]:
        raise ForecastError(
            f"{candidate['n']} candidate forecasts against {reference['n']} reference forecasts; "
            f"the null has to be evaluated on the same sample")
    ref = reference["crps"]
    if ref <= 0:
        raise ForecastError("reference CRPS is not positive")
    s = 1 - candidate["crps"] / ref
    # A model compared with itself has skill exactly 0, which is neither beating nor losing to the
    # reference. Folding that into `beats_null=False` printed "climatology loses to climatology".
    #
    # Keyed on the score rather than on the model name: names are optional, so two differently-named
    # models would compare as distinct while two unnamed ones collided - and the thing that actually
    # makes a comparison vacuous is the two sides having produced the same numbers.
    identical = candidate is reference or candidate == reference
    return {
        "candidate": candidate["models"],
        "reference": reference["models"],
        "crps_candidate": candidate["crps"],
        "crps_reference": ref,
        "skill": s,
        "beats_null": None if identical else s > 0,
        "identical": identical,
        "n": candidate["n"],
        # Sharpness is carried alongside because a positive skill score achieved by being wider is
        # a different thing from one achieved by being right, and only the second is useful.
        "sharpness_candidate": candidate["sharpness_90"],
        "sharpness_reference": reference["sharpness_90"],
        "sharper_than_null": candidate["sharpness_90"] < reference["sharpness_90"],
    }


def directional(records: list[tuple[ForecastDistribution, float]]) -> dict:
    """Hit rate of the sign of the forecast median, and the base rate it has to beat.

    Reported with the base rate because directional accuracy on equities is the single most
    misleading statistic in this field: Indian equities close up on roughly 52% of sessions, so a
    model that always says "up" scores 52% and a headline of "54% directional accuracy" is a 2-point
    edge being presented as a 54-point one.
    """
    if not records:
        raise ForecastError("nothing to score")
    up_calls = correct = 0
    actual_up = 0
    for fc, observed in records:
        med = quantile(fc.terminal(), 0.5)
        called_up = med > fc.anchor
        was_up = observed > fc.anchor
        up_calls += called_up
        actual_up += was_up
        correct += (called_up == was_up)
    n = len(records)
    base = max(actual_up, n - actual_up) / n
    return {
        "n": n,
        "hit_rate": correct / n,
        "base_rate": base,
        "edge_over_base": correct / n - base,
        "share_called_up": up_calls / n,
        "share_actually_up": actual_up / n,
        "why": "base_rate is the hit rate of always calling the majority direction; only "
               "edge_over_base is information",
    }
