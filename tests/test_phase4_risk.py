"""The risk engine, pinned against planted answers.

Every quantity here is one a wrong implementation reports confidently. Portfolio volatility from
a non-PSD matrix comes back as the square root of a negative number; effective bets computed the
wrong way says twelve when the answer is one; a covariance estimated from fewer observations than
assets is singular and a ridge makes it look invertible. So each test plants a structure whose
answer is known and checks the code recovers it.

Two of these tests exist because the first implementation failed them.
"""
from __future__ import annotations

import math
import random

import pytest

from financial_brain.risk import covariance as cv
from financial_brain.risk import decompose as dc
from financial_brain.risk import limits as L
from financial_brain.risk import linalg


def _corr_matrix(n, fn, var=0.0004):
    return [[var if i == j else var * fn(i, j) for j in range(n)] for i in range(n)]


def _draw(n, obs, rho_fn, vol=0.02, seed=3):
    rng = random.Random(seed)
    lo = linalg.cholesky([[1.0 if i == j else rho_fn(i, j) for j in range(n)]
                          for i in range(n)])
    out = {f"X{i}": [] for i in range(n)}
    for _ in range(obs):
        y = linalg.mat_vec(lo, [rng.gauss(0, 1) for _ in range(n)])
        for i in range(n):
            out[f"X{i}"].append(y[i] * vol)
    return out, [f"X{i}" for i in range(n)]


# ------------------------------------------------------------------------- linear algebra
def test_cholesky_refuses_a_matrix_that_is_not_a_covariance():
    """A failure to factorise is a caught estimation error. The alternative is a portfolio
    variance that comes back negative and gets square-rooted."""
    with pytest.raises(linalg.NotPositiveDefinite):
        linalg.cholesky([[1.0, 2.0], [2.0, 1.0]])


def test_cholesky_reconstructs_the_matrix():
    a = _corr_matrix(5, lambda i, j: 0.3)
    lo = linalg.cholesky(a)
    n = len(a)
    for i in range(n):
        for j in range(n):
            got = sum(lo[i][k] * lo[j][k] for k in range(n))
            assert got == pytest.approx(a[i][j], abs=1e-12)


def test_solve_spd_matches_a_known_solution():
    a = _corr_matrix(4, lambda i, j: 0.25, var=1.0)
    x_true = [1.0, -2.0, 0.5, 3.0]
    b = linalg.mat_vec(a, x_true)
    for got, want in zip(linalg.solve_spd(a, b), x_true):
        assert got == pytest.approx(want, abs=1e-9)


def test_eigenvalues_of_a_constant_correlation_matrix_are_known():
    """n x n with unit diagonal and rho off-diagonal has one eigenvalue 1+(n-1)rho and n-1
    eigenvalues 1-rho. An exact answer to check Jacobi against."""
    n, rho = 6, 0.4
    vals, _ = linalg.eigen_sym([[1.0 if i == j else rho for j in range(n)]
                                for i in range(n)])
    assert vals[0] == pytest.approx(1 + (n - 1) * rho, abs=1e-9)
    for v in vals[1:]:
        assert v == pytest.approx(1 - rho, abs=1e-9)


# ----------------------------------------------------------------------------- covariance
def test_shrinkage_goes_to_the_target_when_the_target_is_true():
    """Constant-correlation data with a constant-correlation target is the case where full
    shrinkage is correct, not a failure."""
    s, o = _draw(8, 400, lambda i, j: 0.4)
    r = cv.shrink(s, o)
    assert r["delta"] > 0.9
    assert r["avg_correlation"] == pytest.approx(0.4, abs=0.06)


def test_shrinkage_keeps_the_sample_when_the_target_is_wrong():
    """Two blocks at 0.8 within and 0.0 across is badly described by one average correlation,
    so the estimated intensity must fall and the block structure must survive."""
    s, o = _draw(8, 400, lambda i, j: 0.8 if (i < 4) == (j < 4) else 0.0)
    r = cv.shrink(s, o)
    assert r["delta"] < 0.2
    c = linalg.corr_from_cov(r["cov"])
    assert c[0][1] > 0.6          # within a block
    assert abs(c[0][5]) < 0.25    # across blocks


def test_a_singular_sample_is_refused_rather_than_ridged():
    """At obs <= n the sample covariance has no estimate in it to shrink, only a rank
    deficiency that a ridge would silently make invertible."""
    for n, obs in ((8, 4), (8, 9), (40, 30)):
        s = {f"Z{i}": [random.gauss(0, 1) for _ in range(obs)] for i in range(n)}
        with pytest.raises(cv.CovarianceError):
            cv.shrink(s, [f"Z{i}" for i in range(n)])


def test_shrunk_covariance_is_always_positive_definite():
    s, o = _draw(30, 40, lambda i, j: 0.5, seed=9)
    # 40 observations for 30 assets: the sample is nearly singular, which is the case
    # shrinkage exists for.
    r = cv.shrink(s, o)
    assert linalg.is_pd(r["cov"])
    assert r["underdetermined"] is True


def test_pairwise_estimation_uses_more_data_than_the_intersection():
    """One young name must not truncate the whole book. On the real book this was the
    difference between 105 sessions and 250."""
    s, o = _draw(6, 300, lambda i, j: 0.3)
    sparse = {k: {i: v for i, v in enumerate(vals)} for k, vals in s.items()}
    # X5 only has the last 40 observations, as a recent listing would.
    sparse["X5"] = {i: v for i, v in sparse["X5"].items() if i >= 260}
    ps = cv.sample_pairwise(sparse, o)
    assert ps["min_pair_obs"] == 40           # only pairs involving X5
    assert ps["median_pair_obs"] > 200        # every other pair keeps its history
    r = cv.shrink_pairwise(sparse, o)
    assert linalg.is_pd(r["cov"])
    assert r["method"] == "pairwise_effective_n"


# ------------------------------------------------------------------------- decomposition
def test_risk_contributions_sum_to_portfolio_volatility():
    """The Euler identity. It is what makes risk shares interpretable as shares rather than as
    a heuristic allocation, and it is exact."""
    n = 7
    cov = _corr_matrix(n, lambda i, j: 0.35)
    w = [0.3, 0.2, 0.15, 0.1, 0.1, 0.1, 0.05]
    r = dc.contributions(cov, w, [f"N{i}" for i in range(n)])
    assert r["sum_risk_contributions"] == pytest.approx(r["sigma"], abs=1e-12)


@pytest.mark.parametrize("rho,expected", [(0.0, 4.0), (0.999, 1.0)])
def test_effective_bets_responds_to_correlation(rho, expected):
    """The test the first implementation failed. It computed the reciprocal Herfindahl of
    *position* risk shares, which with equal weights and vols is n whatever the correlations
    are - it returned 4.00 for four independent names and 4.00 for four identical ones, and
    12.00 for a book that was demonstrably two factors. Effective bets is a property of the
    eigenvalue spectrum."""
    n = 4
    cov = linalg.nearest_pd(_corr_matrix(n, lambda i, j: rho))
    r = dc.contributions(cov, [0.25] * n, [f"N{i}" for i in range(n)])
    assert r["effective_bets"] == pytest.approx(expected, abs=0.1)


def test_effective_positions_and_effective_bets_are_different_questions():
    """Both are needed and they are orthogonal: independent names concentrated in one position
    have many bets available and one used; correlated names spread evenly have many positions
    and one bet."""
    n = 12
    indep = _corr_matrix(n, lambda i, j: 0.0)
    even, lumpy = [1 / n] * n, [0.89] + [0.01] * 11
    a = dc.contributions(indep, even, [f"N{i}" for i in range(n)])
    b = dc.contributions(indep, lumpy, [f"N{i}" for i in range(n)])
    assert a["effective_bets"] == pytest.approx(12, abs=0.1)
    assert a["effective_positions"] == pytest.approx(12, abs=0.1)
    assert b["effective_bets"] == pytest.approx(12, abs=0.1)     # directions unchanged
    assert b["effective_positions"] < 1.5                        # only one is used

    corr = _corr_matrix(n, lambda i, j: 0.95)
    c = dc.contributions(linalg.nearest_pd(corr), even, [f"N{i}" for i in range(n)])
    assert c["effective_bets"] < 2.0                             # one bet
    assert c["effective_positions"] == pytest.approx(12, abs=0.1)  # twelve positions


def test_two_blocks_show_two_principal_components():
    n = 12
    cov = _corr_matrix(n, lambda i, j: 0.9 if (i < 6) == (j < 6) else 0.0)
    pc = dc.principal_components(cov, [f"N{i}" for i in range(n)])
    assert pc["cumulative_share"][1] > 0.85     # two directions carry nearly everything
    assert pc["components"][0]["share"] < 0.6   # neither one dominates alone


def test_diversification_ratio_is_one_for_identical_names():
    n = 4
    cov = linalg.nearest_pd(_corr_matrix(n, lambda i, j: 1.0))
    r = dc.contributions(cov, [0.25] * n, [f"N{i}" for i in range(n)])
    assert r["diversification_ratio"] == pytest.approx(1.0, abs=0.01)
    indep = dc.contributions(_corr_matrix(n, lambda i, j: 0.0), [0.25] * n,
                             [f"N{i}" for i in range(n)])
    assert indep["diversification_ratio"] == pytest.approx(2.0, abs=0.01)


def test_marginal_add_prices_the_candidate_with_the_book():
    n = 6
    cov = _corr_matrix(n, lambda i, j: 0.7 if (i < 3) == (j < 3) else 0.05)
    names = [f"N{i}" for i in range(n)]
    w = [1 / 5] * 5 + [0.0]
    m = dc.marginal_add(cov, w, names, candidate="N5", weight=0.2)
    assert m["sigma_after"] > m["sigma_before"]
    assert m["max_correlation_to_book"] == pytest.approx(0.7, abs=0.01)
    with pytest.raises(ValueError):
        dc.marginal_add(cov, w, names, candidate="NOT_PRICED", weight=0.1)


# ----------------------------------------------------------------------------- the limits
def test_a_one_factor_book_breaches_structure_limits():
    n = 12
    cov = _corr_matrix(n, lambda i, j: 0.95)
    d = dc.contributions(linalg.nearest_pd(cov), [1 / n] * n, [f"M{i}" for i in range(n)])
    v = L.check_book({"by_group": {}, "illiquid_share": 0.0, "factor_tilts": {},
                      "positions": n}, d)
    checks = {b.check for b in v.breaches}
    assert "structure.effective_bets" in checks
    assert "structure.first_factor" in checks
    assert v.verdict == "REFUSE"


def test_a_resizable_breach_reports_the_passing_weight():
    v = L.check_candidate(weight=0.15, group="G", exposure_report={"by_group": {"G": 0.10}})
    assert v.verdict == "RESIZE"
    assert v.resize_to == pytest.approx(0.05, abs=1e-9)


def test_a_correlation_breach_has_no_passing_size():
    """A name 0.95 correlated with something already held is a second helping. There is no
    smaller version of it that is a different bet."""
    v = L.check_candidate(weight=0.02,
                          marginal={"max_correlation_to_book": 0.95, "risk_share": 0.05})
    assert v.verdict == "REFUSE"
    assert v.resize_to is None


def test_a_data_gap_abstains_rather_than_refusing():
    """The distinction the gate turns on. 84% of the real book has no promoter group, so
    treating that as a limit breach refuses every candidate forever - correct and useless.
    "Unverifiable" is not "too high"."""
    v = L.check_book({"by_group": {"UNCLASSIFIED": 0.84}, "illiquid_share": 0.0,
                      "factor_tilts": {}, "positions": 25}, None)
    assert v.verdict == "ABSTAIN"
    assert [b.check for b in v.data_gaps] == ["exposure.unclassified"]
    assert v.resize_to is None


def test_expected_shortfall_is_empirical_and_reports_its_tail():
    rng = random.Random(4)
    xs = [rng.gauss(0.0005, 0.02) for _ in range(500)] + [-0.09, -0.11, -0.15]
    es = L.expected_shortfall(xs)
    assert es["es"] < es["var"] < 0          # the tail mean is worse than the quantile
    assert es["tail_n"] == pytest.approx(len(xs) * 0.05, abs=2)
    short = L.expected_shortfall([0.01] * 10)
    assert math.isnan(short["es"])           # ten points is not a tail
