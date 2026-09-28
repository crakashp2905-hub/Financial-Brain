"""Distributional forecasting: the object, the nulls, and the scores.

The tests are weighted toward the two places a forecasting subsystem goes wrong quietly. First, a
distribution that reports tails it never estimated - 20 sampled paths do not have a 5th percentile,
and returning the worst path as one is how a forecast acquires a tail from nothing. Second, scores
that depend on something other than the forecast: a CRPS estimator that moves with ensemble size, a
skill score computed against a null evaluated on a different sample, a directional hit rate quoted
without the base rate it has to beat.
"""
from __future__ import annotations

import math
import random
from datetime import date

import pytest

from financial_brain.forecasting import calibration as C
from financial_brain.forecasting import null
from financial_brain.forecasting.distribution import (
    MIN_PATHS_FOR_TAILS,
    ForecastDistribution,
    ForecastError,
    quantile,
)

AS_OF = date(2026, 1, 1)


def _prices(n=800, mu=0.0004, sigma=0.015, seed=1, start=100.0):
    rng = random.Random(seed)
    px = [start]
    for _ in range(n):
        px.append(px[-1] * (1 + rng.gauss(mu, sigma)))
    return px


def _fan(terminals, anchor=100.0, horizon=1, **kw):
    """A distribution whose paths are single steps to the given terminal prices."""
    return ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=horizon,
                               anchor=anchor, paths=[[t] * horizon for t in terminals], **kw)


# ------------------------------------------------------------------------- the object
def test_ragged_paths_are_refused_because_a_quantile_over_them_mixes_horizons():
    with pytest.raises(ForecastError, match="ragged"):
        ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=3, anchor=100.0,
                             paths=[[1.0, 2.0, 3.0], [1.0, 2.0]])


def test_the_declared_horizon_must_match_the_paths():
    with pytest.raises(ForecastError, match="horizon"):
        ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=5, anchor=100.0,
                             paths=[[1.0, 2.0]])


def test_an_empty_sample_is_not_a_forecast():
    with pytest.raises(ForecastError):
        ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=1, anchor=100.0, paths=[])


def test_a_thin_sample_reports_no_tail_rather_than_its_own_extremum():
    """The failure this guard exists for: 20 paths have no 5th percentile, and returning the worst
    of them as p05 invents a tail the model never estimated."""
    thin = _fan([100.0 + i for i in range(20)])
    q = thin.quantiles()
    assert q["p05"] is None and q["p95"] is None
    assert q["p50"] is not None, "the median is supported by 20 paths"
    assert math.isnan(thin.expected_shortfall())

    thick = _fan([100.0 + i for i in range(MIN_PATHS_FOR_TAILS + 10)])
    assert thick.quantiles()["p05"] is not None
    assert not math.isnan(thick.expected_shortfall())


def test_quantile_matches_the_type_seven_definition():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert quantile(xs, 0.0) == 1.0
    assert quantile(xs, 1.0) == 4.0
    assert quantile(xs, 0.5) == pytest.approx(2.5)
    # pos = 0.25 * 3 = 0.75, so 1 + 0.75*(2-1)
    assert quantile(xs, 0.25) == pytest.approx(1.75)


def test_path_volatility_is_not_the_spread_of_terminals():
    """Two forecasts can agree exactly on where prices end and disagree completely on how they get
    there. A position with a stop is killed by the second number, so they must not be the same
    function - a smooth fan has wide terminal spread and near-zero path volatility."""
    steps = (-2, -1, 0, 1, 2)
    smooth = ForecastDistribution(
        instrument="INE0", as_of=AS_OF, horizon=10, anchor=100.0,
        paths=[[100.0 + k * step for k in range(1, 11)] for step in steps])
    # Same start, same end, wildly different route: every interior close is displaced by +-8 and
    # the final one is left alone, so the terminal distributions are identical by construction.
    jagged = ForecastDistribution(
        instrument="INE0", as_of=AS_OF, horizon=10, anchor=100.0,
        paths=[[100.0 + k * step + (0 if k == 10 else (8 if k % 2 else -8))
                for k in range(1, 11)] for step in steps])

    assert smooth.terminal() == jagged.terminal()
    assert smooth.expected_return() == pytest.approx(jagged.expected_return())
    assert jagged.expected_volatility() > 5 * smooth.expected_volatility(), (
        f"identical terminals, {smooth.expected_volatility():.1%} vs "
        f"{jagged.expected_volatility():.1%} path volatility - these cannot be one number")


# ------------------------------------------------------------- the path-dependent question
def test_first_passage_is_not_recoverable_from_the_marginal():
    """The reason this module keeps paths. Both forecasts end at exactly the same prices; one dips
    through the stop on the way and the other does not, and only first passage can tell them apart.
    """
    direct = ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=4, anchor=100.0,
                                  paths=[[102.0, 104.0, 106.0, 108.0]])
    detour = ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=4, anchor=100.0,
                                  paths=[[94.0, 98.0, 104.0, 108.0]])
    assert direct.terminal() == detour.terminal()
    assert direct.probability_above(100.0) == detour.probability_above(100.0)

    a = direct.first_passage(target=106.0, stop=95.0)
    b = detour.first_passage(target=106.0, stop=95.0)
    assert a["p_target_first"] == 1.0 and a["p_stop_first"] == 0.0
    assert b["p_stop_first"] == 1.0 and b["p_target_first"] == 0.0


def test_a_session_crossing_both_levels_is_given_to_the_stop():
    """Unresolvable from closes, so it is resolved against the strategy. The other choice is a
    coin-flip that becomes an edge in a backtest."""
    fc = ForecastDistribution(instrument="INE0", as_of=AS_OF, horizon=1, anchor=100.0,
                              paths=[[90.0]])
    r = fc.first_passage(target=110.0, stop=95.0)
    assert r["p_stop_first"] == 1.0


def test_first_passage_probabilities_are_a_partition():
    fc = null.Climatology().forecast(instrument="INE0", as_of=AS_OF, prices=_prices(),
                                    horizon=20, n_paths=300)
    r = fc.first_passage(target=fc.anchor * 1.05, stop=fc.anchor * 0.95)
    assert (r["p_target_first"] + r["p_stop_first"] + r["p_neither"]) == pytest.approx(1.0)


def test_a_target_below_the_stop_is_refused():
    fc = _fan([100.0] * 50)
    with pytest.raises(ForecastError, match="above"):
        fc.first_passage(target=90.0, stop=95.0)


# ------------------------------------------------------------------------------ the nulls
def test_every_null_is_reproducible_from_its_seed():
    """A null that moves between runs cannot be the thing a result is measured against."""
    px = _prices()
    for f in null.nulls(seed=7).values():
        a = f.forecast(instrument="INE0", as_of=AS_OF, prices=px, horizon=20, n_paths=50)
        b = f.forecast(instrument="INE0", as_of=AS_OF, prices=px, horizon=20, n_paths=50)
        assert a.paths == b.paths, f"{f.name} is not reproducible"


def test_the_nulls_differ_from_each_other_in_the_way_they_claim_to():
    """drift carries the mean and random-walk does not, so on a history with a strong uptrend the
    drift null must forecast a higher median."""
    up = _prices(mu=0.002, sigma=0.010, seed=3)
    rw = null.RandomWalk().forecast(instrument="INE0", as_of=AS_OF, prices=up,
                                    horizon=40, n_paths=400)
    dr = null.Drift().forecast(instrument="INE0", as_of=AS_OF, prices=up,
                               horizon=40, n_paths=400)
    assert dr.expected_return() > rw.expected_return() + 0.02
    assert abs(rw.expected_return()) < 0.02, "a drift-free walk should not drift"


def test_the_block_bootstrap_keeps_volatility_clustering_that_an_iid_draw_destroys():
    """The reason climatology resamples blocks. An i.i.d. bootstrap produces paths that are too
    smooth, which makes its stop probabilities too low - so it is a null the model beats for free on
    exactly the path-dependent questions that matter.

    Built with a history in two regimes: 400 calm sessions then 400 violent ones. A block bootstrap
    reproduces stretches of each; an i.i.d. draw blends them into one middling volatility.
    """
    rng = random.Random(11)
    px = [100.0]
    for _ in range(400):
        px.append(px[-1] * (1 + rng.gauss(0, 0.004)))
    for _ in range(400):
        px.append(px[-1] * (1 + rng.gauss(0, 0.040)))

    blocks = null.Climatology(mean_block=20).forecast(
        instrument="INE0", as_of=AS_OF, prices=px, horizon=60, n_paths=400)
    iid = null.Climatology(mean_block=1).forecast(
        instrument="INE0", as_of=AS_OF, prices=px, horizon=60, n_paths=400)

    def spread_of_path_vols(fc):
        vols = []
        for p in fc.paths:
            prices = [fc.anchor] + p
            r = [prices[i + 1] / prices[i] - 1 for i in range(len(prices) - 1)]
            m = sum(r) / len(r)
            vols.append(math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)))
        mv = sum(vols) / len(vols)
        return math.sqrt(sum((v - mv) ** 2 for v in vols) / (len(vols) - 1))

    assert spread_of_path_vols(blocks) > 1.5 * spread_of_path_vols(iid), (
        "block resampling must produce paths that differ in volatility from one another; "
        "an i.i.d. draw gives every path the same blended volatility")


def test_a_null_refuses_to_speak_from_too_little_history():
    with pytest.raises(ForecastError, match="below"):
        null.RandomWalk().forecast(instrument="INE0", as_of=AS_OF,
                                   prices=[100.0] * 50, horizon=20)


# ------------------------------------------------------------------------------- the scores
def test_crps_reduces_to_absolute_error_for_a_point_mass():
    """The property that makes it comparable across models of different kinds."""
    assert C.crps([5.0] * 10, 7.0) == pytest.approx(2.0)


def test_crps_rewards_a_distribution_centred_on_the_observation():
    rng = random.Random(2)
    near = [rng.gauss(10.0, 1.0) for _ in range(200)]
    far = [rng.gauss(14.0, 1.0) for _ in range(200)]
    assert C.crps(near, 10.0) < C.crps(far, 10.0)


def test_the_naive_crps_estimator_penalises_the_smaller_ensemble():
    """The bias that would have decided a Kronos-versus-bootstrap comparison on ensemble size.

    Same underlying distribution, same observation, different ensemble sizes. Under the fair
    estimator the score is about the same; under the naive one the 20-member ensemble is handicapped
    against the 1000-member one for a reason that is not about forecasting.
    """
    rng = random.Random(5)
    small = [rng.gauss(0.0, 1.0) for _ in range(20)]
    big = [rng.gauss(0.0, 1.0) for _ in range(1000)]

    naive_gap = C.crps(small, 0.3, fair=False) - C.crps(big, 0.3, fair=False)
    fair_gap = C.crps(small, 0.3) - C.crps(big, 0.3)
    assert naive_gap > 0, "the naive estimator should score the small ensemble worse"
    assert abs(fair_gap) < naive_gap, "the fair estimator should shrink that gap"
    # And the naive score is always the worse of the two, by exactly the spread correction.
    assert C.crps(small, 0.3, fair=False) > C.crps(small, 0.3)


def test_crps_refuses_a_one_member_ensemble():
    with pytest.raises(ForecastError, match="two"):
        C.crps([1.0], 2.0)


def test_pinball_loss_is_asymmetric_in_the_direction_the_quantile_implies():
    """An upper quantile should be punished more for being too low than for being too high."""
    sample = [float(i) for i in range(101)]        # p90 = 90
    too_low = C.pinball(sample, 95.0, 0.90)       # observation above the quantile
    too_high = C.pinball(sample, 85.0, 0.90)      # observation below it
    assert too_low > 0 and too_high > 0
    assert too_low / 5.0 == pytest.approx(0.90)
    assert too_high / 5.0 == pytest.approx(0.10)


def test_the_pit_of_a_correct_forecast_is_uniform_and_of_a_narrow_one_is_not():
    rng = random.Random(9)
    truth = [rng.gauss(0.0, 1.0) for _ in range(600)]
    honest = [C.pit([rng.gauss(0.0, 1.0) for _ in range(200)], y) for y in truth]
    narrow = [C.pit([rng.gauss(0.0, 0.25) for _ in range(200)], y) for y in truth]

    h = C._uniformity(honest)
    n = C._uniformity(narrow)
    assert h["chi2"] < n["chi2"]
    assert n["edge_mass"] > 2 * n["edge_mass_expected"]
    assert "too narrow" in n["shape"]


def test_coverage_of_a_well_specified_forecast_matches_its_nominal_level():
    rng = random.Random(13)
    records = []
    for _ in range(400):
        sample = [rng.gauss(100.0, 5.0) for _ in range(200)]
        records.append((_fan(sample), rng.gauss(100.0, 5.0)))
    s = C.score(records)
    assert s["coverage"]["90%"] == pytest.approx(0.90, abs=0.05)
    assert s["coverage"]["50%"] == pytest.approx(0.50, abs=0.07)


def test_sharpness_ignores_the_observation():
    a = _fan([float(i) for i in range(100)])
    assert C.sharpness(a.terminal(), 0.90) == pytest.approx(
        quantile(a.terminal(), 0.95) - quantile(a.terminal(), 0.05))


def test_a_skill_score_against_a_null_scored_on_a_different_sample_is_refused():
    """The number it would return looks entirely reasonable, which is why this is a refusal."""
    rng = random.Random(17)
    def recs(n):
        return [(_fan([rng.gauss(100.0, 5.0) for _ in range(100)]), rng.gauss(100.0, 5.0))
                for _ in range(n)]
    a, b = C.score(recs(50)), C.score(recs(80))
    with pytest.raises(ForecastError, match="same sample"):
        C.skill(a, b)


def test_skill_is_zero_against_itself_and_positive_for_a_better_forecast():
    rng = random.Random(19)
    truths = [rng.gauss(100.0, 5.0) for _ in range(300)]
    sharp = [(_fan([rng.gauss(y, 1.0) for _ in range(100)], anchor=100.0, model="sharp"), y)
             for y in truths]
    vague = [(_fan([rng.gauss(100.0, 15.0) for _ in range(100)], anchor=100.0, model="vague"), y)
             for y in truths]
    s, v = C.score(sharp), C.score(vague)
    self_comparison = C.skill(s, s)
    assert self_comparison["skill"] == pytest.approx(0.0)
    assert self_comparison["identical"] is True
    assert self_comparison["beats_null"] is None, (
        "a model does not beat or lose to itself, and reporting False printed "
        "'climatology loses to climatology'")

    r = C.skill(s, v)
    assert r["identical"] is False
    assert r["skill"] > 0 and r["beats_null"] is True
    assert r["sharper_than_null"] is True


def test_a_wider_forecast_that_wins_on_crps_is_flagged_as_not_sharper():
    """Positive skill achieved by widening is a different thing from being right, and only the
    second is useful. The score carries both so the distinction cannot be dropped."""
    rng = random.Random(23)
    truths = [rng.gauss(100.0, 20.0) for _ in range(300)]
    wide = [(_fan([rng.gauss(100.0, 20.0) for _ in range(100)], anchor=100.0), y)
            for y in truths]
    narrow = [(_fan([rng.gauss(100.0, 2.0) for _ in range(100)], anchor=100.0), y)
              for y in truths]
    r = C.skill(C.score(wide), C.score(narrow))
    assert r["skill"] > 0, "the wide forecast is better calibrated here and should win on CRPS"
    assert r["sharper_than_null"] is False


def test_directional_accuracy_is_reported_against_the_base_rate_it_has_to_beat():
    """The single most misleading statistic in this field. A model that always says up scores the
    base rate, and 54% against a 52% base rate is a 2-point edge presented as a 54-point one."""
    rng = random.Random(29)
    records = []
    for _ in range(500):
        y = 100.0 * (1 + abs(rng.gauss(0.0, 0.02)))     # always up
        records.append((_fan([101.0] * 60, anchor=100.0), y))
    d = C.directional(records)
    assert d["hit_rate"] == pytest.approx(1.0)
    assert d["base_rate"] == pytest.approx(1.0)
    assert d["edge_over_base"] == pytest.approx(0.0), (
        "always calling up on an always-up sample is not an edge")
    assert d["share_called_up"] == 1.0


# -------------------------------------------------------------------- nulls, end to end
def test_the_scores_prefer_a_bootstrap_over_a_sharper_but_mis_specified_forecast():
    """The null bites, which is the whole reason it is here.

    A forecaster that assumes a quarter of the name's true volatility is *sharper* on every window
    and wrong about the dispersion. Averaged over many windows the bootstrap that carries the real
    dispersion has to win on CRPS - a single window proves nothing, so this walks 60 of them.
    """
    px = _prices(n=2000, mu=0.0, sigma=0.02, seed=31)
    horizon = 20
    clim_recs, thin_recs = [], []
    for k in range(60):
        cut = 900 + k * 15
        history, observed = px[:cut], px[cut + horizon - 1]
        clim = null.Climatology(seed=k).forecast(
            instrument="INE0", as_of=AS_OF, prices=history, horizon=horizon, n_paths=200)
        thin = null.RandomWalk(seed=k).forecast(
            instrument="INE0", as_of=AS_OF, prices=history, horizon=horizon, n_paths=200)
        anchor = history[-1]
        thin.paths = [[anchor + (q - anchor) * 0.25 for q in path] for path in thin.paths]
        clim_recs.append((clim, observed))
        thin_recs.append((thin, observed))

    c, t = C.score(clim_recs), C.score(thin_recs)
    assert t["sharpness_90"] < c["sharpness_90"], "the mis-specified forecast is the sharper one"
    assert t["coverage"]["90%"] < c["coverage"]["90%"], "and its intervals miss more often"

    r = C.skill(c, t)
    assert r["skill"] > 0, (
        f"the bootstrap should win on CRPS: {c['crps']:.3f} vs {t['crps']:.3f}")
    assert r["sharper_than_null"] is False, (
        "and it should win while being the wider of the two, which is exactly the case the "
        "sharpness flag exists to surface")
