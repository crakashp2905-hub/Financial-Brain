import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import dynamic_standard as D


def _series(mu, n=126, seed=1, sd=1.0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sd, n), index=pd.date_range("2016-01-31", periods=n, freq="ME"))


def test_required_t_rises_with_weaker_prior_and_larger_family():
    a = D.required_t("A", 1)
    assert a < D.required_t("B", 1) < D.required_t("C", 1)
    assert D.required_t("A", 1) < D.required_t("A", 10) < D.required_t("A", 100)
    assert D.required_t("A", 1, "CANDIDATE_SMALL") < D.required_t("A", 1, "CANDIDATE")
    # the published values the ADR quotes
    assert a == pytest.approx(1.97, abs=0.02)
    assert D.required_t("C", 1, "CANDIDATE") == pytest.approx(3.50, abs=0.03)


def test_required_t_is_the_boundary_of_the_tier():
    for prior in "ABC":
        for fam in (1, 7, 40):
            t = D.required_t(prior, fam, "CANDIDATE_SMALL")
            assert D.tier_of(D.posterior(D.sidak_z(t + 0.01, fam), prior)) in ("CANDIDATE_SMALL", "CANDIDATE")
            assert D.tier_of(D.posterior(D.sidak_z(t - 0.01, fam), prior)) in ("INCUBATE", D.FAIL)


def test_negative_evidence_is_never_promoted():
    assert D.posterior(-3.0, "A") < D.posterior(0.0, "A") == pytest.approx(0.25)
    r = D.judge(D.Spec("x", "A"), _series(-0.3))
    assert r["tier"] == D.FAIL


def test_one_good_half_fails():
    s = _series(0.0)
    s.iloc[:72] -= s.iloc[:72].mean() + 0.05          # first half negative
    s.iloc[72:] += 1.0
    r = D.judge(D.Spec("x", "A"), s, null_pass=True)
    assert r["tier"] == D.FAIL and "not positive in both halves" in r["why"]


def test_null_caps_the_tier():
    s = _series(0.45, seed=3)
    top = D.judge(D.Spec("x", "A"), s, null_pass=True)["tier"]
    assert top == "CANDIDATE"
    assert D.judge(D.Spec("x", "A"), s, null_pass=None)["tier"] == "CANDIDATE_SMALL"
    assert D.judge(D.Spec("x", "A"), s, null_pass=False)["tier"] == "INCUBATE"


def test_family_size_lowers_the_grade():
    s = _series(0.25, seed=5)
    p1 = D.judge(D.Spec("x", "B", 1), s)["posterior"]
    p40 = D.judge(D.Spec("x", "B", 40), s)["posterior"]
    assert p40 < p1


def test_live_evidence_updates_and_demotes():
    s = _series(0.22, seed=7)
    base = D.judge(D.Spec("x", "A"), s, null_pass=True)
    good = D.judge(D.Spec("x", "A"), s, null_pass=True, live=_series(0.6, n=24, seed=8))
    bad = D.judge(D.Spec("x", "A"), s, null_pass=True, live=_series(-0.6, n=24, seed=9))
    assert good["posterior"] > base["posterior"] > bad["posterior"]
    order = ["CANDIDATE", "CANDIDATE_SMALL", "INCUBATE", D.FAIL]
    assert order.index(bad["tier"]) > order.index(base["tier"]) or base["tier"] == D.FAIL


def test_capital_fraction_respects_tier_cap_and_zero_below_small():
    r = D.judge(D.Spec("x", "A"), _series(0.5, seed=11, sd=0.02), null_pass=True)
    assert 0 < r["capital_fraction"] <= D.TIER_CAP[r["tier"]]
    weak = D.judge(D.Spec("x", "C"), _series(0.1, seed=12))
    assert weak["capital_fraction"] == 0.0


def test_halves_version_matches_series_version_roughly():
    s = _series(0.3, seed=13)
    full = D.judge(D.Spec("x", "A"), s, null_pass=True, lag=0)
    a, b = s[s.index < D.S.SPLIT], s[s.index >= D.S.SPLIT]
    h = D.judge_from_halves(D.Spec("x", "A"), D.S.tstat(a), len(a), D.S.tstat(b), len(b), null_pass=True)
    assert abs(h["t_used"] - full["t_iid"]) < 0.25


def test_false_pass_rate_is_controlled_and_search_does_not_inflate_it():
    one = D.calibrate(draws=400, family=1)
    many = D.calibrate(draws=400, family=20)
    assert one["A"]["CANDIDATE_SMALL"] < 0.05 and one["C"]["CANDIDATE_SMALL"] < 0.01
    assert many["A"]["CANDIDATE_SMALL"] < 0.05          # best of 20 nulls is not let through
    power = D.calibrate(draws=400, family=1, sharpe=0.7)
    assert power["A"]["CANDIDATE_SMALL"] > 0.4
