import numpy as np
import pytest

from financial_brain.portfolio import optimizer as O
from financial_brain.risk import degradation as D
from financial_brain.risk import killswitch as K


# ------------------------------------------------------------------ kill switch
def test_healthy_inputs_are_ok_and_say_which_checks_ran():
    v = K.evaluate(equity=[100, 101, 102], sessions_since_data=0)
    assert v["level"] == K.OK and set(v["checks_run"]) >= {"drawdown", "stale_data"}


def test_missing_inputs_are_not_checked_not_assumed_fine():
    v = K.evaluate()
    assert v["level"] == K.OK and v["checks_run"] == []


def test_soft_and_hard_drawdown_levels():
    assert K.evaluate(equity=[100, 98, 96, 94, 92])["level"] == K.REDUCE
    assert K.evaluate(equity=[100, 97, 94, 91, 88, 85])["level"] == K.HALT
    assert K.evaluate(equity=[100, 97, 94, 91, 88, 85])["exposure_scale"] == 0.0


def test_a_single_bad_day_halts_and_stale_data_halts():
    assert K.evaluate(equity=[100, 100, 94])["level"] == K.HALT
    assert K.evaluate(sessions_since_data=9)["level"] == K.HALT


def test_degraded_signal_and_cost_overrun_reduce():
    assert K.evaluate(degraded=True)["level"] == K.REDUCE
    assert K.evaluate(realised_slippage=0.0030, modelled_slippage=0.0010)["level"] == K.REDUCE


def test_trip_persists_and_a_milder_verdict_does_not_downgrade_it(tmp_path):
    p = tmp_path / "ks.json"
    K.trip(K.evaluate(equity=[100, 80]), p)
    assert K.exposure_scale(p) == 0.0
    K.trip(K.evaluate(degraded=True), p)
    assert K.load(p)["level"] == K.HALT


def test_only_a_human_with_a_reason_can_reset(tmp_path):
    p = tmp_path / "ks.json"
    K.trip(K.evaluate(equity=[100, 80]), p)
    with pytest.raises(K.KillSwitchError):
        K.reset("agent:claude", "looks fine to me, resuming", p)
    with pytest.raises(K.KillSwitchError):
        K.reset("owner", "ok", p)
    s = K.reset("owner", "data feed restored and checked by hand", p)
    assert s["level"] == K.OK and s["reset_by"] == "owner" and K.exposure_scale(p) == 1.0


# ------------------------------------------------------------------ degradation
def test_a_stable_edge_is_ok_and_a_collapsed_one_is_degraded():
    rng = np.random.default_rng(0)
    good = rng.normal(0.08, 0.05, 80)
    assert D.assess(good)["state"] == D.OK
    bad = np.r_[rng.normal(0.08, 0.05, 60), rng.normal(-0.02, 0.05, 20)]
    r = D.assess(bad)
    assert r["state"] in (D.WATCH, D.DEGRADED) and r["recent_ic"] < r["baseline_ic"]
    assert D.assess(bad)["state"] == D.DEGRADED


def test_baseline_excludes_the_window_so_a_collapse_cannot_hide_in_it():
    rng = np.random.default_rng(1)
    ic = np.r_[rng.normal(0.08, 0.02, 50), np.full(12, -0.3)]
    assert abs(D.assess(ic)["baseline_ic"] - 0.08) < 0.02


def test_too_short_a_series_is_refused():
    with pytest.raises(D.DegradationError):
        D.assess(np.zeros(10))


def test_cusum_alarms_on_a_sustained_drop_only():
    flat = D.cusum(np.random.default_rng(2).normal(0, 1, 60), baseline_mean=0, baseline_sd=1)
    drop = D.cusum(np.r_[np.zeros(20), np.full(20, -2.0)], baseline_mean=0, baseline_sd=1)
    assert flat["alarm_at"] is None and drop["alarm_at"] is not None and drop["alarm_at"] >= 20


# ------------------------------------------------------------------ optimizer
def problem(n=12, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.02, (250, n)) + rng.normal(0, 0.01, (250, 1))
    return rng.uniform(0.002, 0.02, n), O.shrunk_cov(r)


def test_constraints_hold():
    mu, S = problem()
    r = O.optimize(mu, S, max_weight=0.15)
    assert r["converged"] and (r["weights"] >= -1e-9).all() and (r["weights"] <= 0.15 + 1e-6).all()
    assert r["gross"] <= 1 + 1e-6


def test_higher_costs_mean_less_turnover_from_a_given_book():
    mu, S = problem()
    w0 = np.full(len(mu), 1 / len(mu))
    cheap = O.optimize(mu, S, w_prev=w0, cost=np.full(len(mu), 0.0005), max_weight=0.2)
    dear = O.optimize(mu, S, w_prev=w0, cost=np.full(len(mu), 0.05), max_weight=0.2)
    assert dear["turnover"] < cheap["turnover"]


def test_risk_aversion_lowers_risk():
    mu, S = problem()
    lo = O.optimize(mu, S, risk_aversion=1.0, max_weight=0.3)
    hi = O.optimize(mu, S, risk_aversion=60.0, max_weight=0.3)
    assert hi["risk"] <= lo["risk"] + 1e-9


def test_shrinkage_makes_a_wide_covariance_invertible():
    r = np.random.default_rng(3).normal(0, 0.01, (40, 60))        # more names than periods
    S = O.shrunk_cov(r)
    assert np.linalg.cond(S) < 1e6


def test_bad_shapes_are_refused():
    with pytest.raises(O.OptimizerError):
        O.optimize([0.1, 0.1], np.eye(3))
    with pytest.raises(O.OptimizerError):
        O.shrunk_cov(np.zeros((5, 3)))
