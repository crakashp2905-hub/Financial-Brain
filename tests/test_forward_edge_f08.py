"""F08 - statistical confirmation and evidence tiers (FE-0091 .. FE-0103). Synthetic series; no test proves alpha."""
from __future__ import annotations

import random

import pytest

from financial_brain.forward_edge import evidence_stats as E
from financial_brain.forward_edge.evidence_stats import ClaimTier


def _ar1(n: int, phi: float, seed: int, mu: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    x, out = 0.0, []
    for _ in range(n):
        x = phi * x + rng.gauss(0, 0.01)
        out.append(mu + x)
    return out


# ---- FE-0091 ----

def test_daily_returns_use_previous_close_and_remove_external_flows() -> None:
    r = E.daily_returns([100.0, 110.0, 121.0], [0.0, 0.0])
    assert r == pytest.approx([0.10, 0.10])
    with_flow = E.daily_returns([100.0, 160.0], [50.0])        # 50 of the 60 gain is a deposit
    assert with_flow == pytest.approx([0.10])
    assert E.daily_returns([100.0, 100.0, 100.0]) == [0.0, 0.0]   # cash days are in the series
    with pytest.raises(E.StatsError):
        E.daily_returns([100.0])


# ---- FE-0092 / FE-0093 ----

def test_hac_standard_error_exceeds_the_naive_one_for_autocorrelated_returns() -> None:
    x = _ar1(500, 0.6, seed=1)
    assert E.newey_west_se(x, 10) > 1.4 * E.naive_se(x)


def test_stationary_bootstrap_interval_is_wider_under_dependence() -> None:
    dep = _ar1(300, 0.7, seed=2)
    iid = _ar1(300, 0.0, seed=2)
    lo_d, hi_d = E.stationary_bootstrap_ci(dep, resamples=600, mean_block=10, seed=3)
    lo_i, hi_i = E.stationary_bootstrap_ci(iid, resamples=600, mean_block=10, seed=3)
    assert (hi_d - lo_d) > (hi_i - lo_i)
    assert E.stationary_bootstrap_ci(dep, resamples=300, seed=4) == E.stationary_bootstrap_ci(dep, resamples=300, seed=4)


def test_clustering_by_date_prevents_counting_trades_as_independent() -> None:
    rng = random.Random(7)
    values, clusters = [], []
    for day in range(10):
        common = rng.gauss(0, 0.02)            # all trades on a day share the day's shock
        for _ in range(20):
            values.append(common + rng.gauss(0, 0.002))
            clusters.append(f"d{day}")
    assert E.cluster_se(values, clusters) > 3 * E.naive_se(values)
    with pytest.raises(E.StatsError):
        E.cluster_se([1.0, 2.0], ["a", "a"])


# ---- FE-0094 ----

def test_holm_adjustment_accounts_for_every_candidate_tested() -> None:
    adj = E.holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    assert all(adj[k] >= p for k, p in {"a": 0.01, "b": 0.04, "c": 0.03}.items())


# ---- FE-0095 / FE-0096 ----

def test_drawdown_profit_factor_and_worst_day() -> None:
    s = E.drawdown_stats([0.10, -0.20, 0.05])
    assert s["max_drawdown"] == pytest.approx(-0.20)
    assert s["worst_day"] == -0.20 and s["profit_factor"] == pytest.approx(0.15 / 0.20)


def test_capacity_degrades_return_as_participation_grows() -> None:
    r = [E.capacity_adjusted_return(0.01, p, 0.02) for p in (0.0, 0.01, 0.04, 0.25)]
    assert r == sorted(r, reverse=True) and r[0] == 0.01 and r[-1] <= 0.0
    with pytest.raises(E.StatsError):
        E.capacity_adjusted_return(0.01, -1, 0.02)


# ---- FE-0097 ----

def test_required_sessions_scale_with_noise_and_target_precision() -> None:
    assert E.required_sessions(0.01, 0.001) == 385
    assert E.required_sessions(0.01, 0.001) > E.required_sessions(0.01, 0.002)
    assert E.required_sessions(0.002, 0.0005) < E.required_sessions(0.01, 0.0005)


# ---- FE-0098 / FE-0099 / FE-0100 / FE-0101 / FE-0102 / FE-0103 + verdict ----

GOOD_KW = dict(eligible_independent_sessions=120, tier=ClaimTier.INDEPENDENT_FORWARD,
               net_by_cost_multiplier={1.0: 0.004, 1.5: 0.003, 2.0: 0.002}, best_block=0.35,
               sample_design_predeclared=True, falsification_status="NO_FALSIFICATION_FOUND")


def test_a_clear_effect_with_everything_in_order_is_promotion_grade() -> None:
    x = _ar1(250, 0.1, seed=11, mu=0.004)
    v = E.promotion_verdict(daily_net_alpha=x, **GOOD_KW)
    assert v["verdict"] == "PROMOTION_GRADE" and v["lower_bound"] > 0


def test_positive_mean_with_interval_including_zero_is_not_promoted() -> None:
    raw = _ar1(250, 0.1, seed=12)
    centre = sum(raw) / len(raw)
    x = [r - centre + 0.0003 for r in raw]          # sample mean is exactly +0.0003, well inside the noise
    assert sum(x) / len(x) == pytest.approx(0.0003)
    v = E.promotion_verdict(daily_net_alpha=x, **GOOD_KW)
    assert v["verdict"] != "PROMOTION_GRADE" and "LOWER_BOUND_NOT_POSITIVE" in v["reasons"]
    assert v["mean"] > 0 and v["lower_bound"] < 0


@pytest.mark.parametrize(
    "override, reason",
    [
        ({"tier": ClaimTier.LOCAL_PAPER}, "TIER_NOT_INDEPENDENT_FORWARD"),
        ({"eligible_independent_sessions": 59}, "BELOW_SESSION_FLOOR"),
        ({"sample_design_predeclared": False}, "SAMPLE_DESIGN_NOT_PREDECLARED"),
        ({"best_block": 0.9}, "REGIME_CONCENTRATION"),
        ({"net_by_cost_multiplier": {1.0: 0.004, 1.5: 0.001, 2.0: -0.001}}, "FAILS_COST_STRESS"),
        ({"falsification_status": "INSUFFICIENT_EVIDENCE"}, "FALSIFICATION_INSUFFICIENT_EVIDENCE"),
    ],
)
def test_each_gate_independently_blocks_promotion(override: dict, reason: str) -> None:
    x = _ar1(250, 0.1, seed=11, mu=0.004)
    v = E.promotion_verdict(daily_net_alpha=x, **{**GOOD_KW, **override})
    assert v["verdict"] != "PROMOTION_GRADE" and reason in v["reasons"]


def test_a_losing_strategy_fails_rather_than_staying_insufficient() -> None:
    x = _ar1(250, 0.1, seed=13, mu=-0.002)
    assert E.promotion_verdict(daily_net_alpha=x, **GOOD_KW)["verdict"] == "FAIL"


def test_peeking_between_checkpoints_cannot_speed_promotion_but_harm_can_stop() -> None:
    p = E.StoppingPolicy((60, 120, 250), harm_floor=-0.15)
    assert [p.evaluation_allowed(n) for n in (30, 60, 61, 120)] == [False, True, False, True]
    assert p.stop_for_harm(-0.16) and not p.stop_for_harm(-0.05)
    with pytest.raises(E.StatsError):
        E.StoppingPolicy((120, 60), -0.1)


def test_one_lucky_period_cannot_pass_regime_robustness() -> None:
    lucky = [0.0] * 90 + [0.05] * 10
    spread = [0.0005] * 100
    assert E.best_block_share(lucky, 5) == pytest.approx(1.0)
    assert E.best_block_share(spread, 5) == pytest.approx(0.2)


def test_every_reported_row_carries_exactly_one_claim_tier() -> None:
    assert E.label_rows([{"x": 1, "tier": ClaimTier.SHADOW}])
    with pytest.raises(E.StatsError):
        E.label_rows([{"x": 1}])
    with pytest.raises(E.StatsError):
        E.label_rows([{"x": 1, "tier": "LOCAL_PAPER"}])


def test_missing_falsification_evidence_is_insufficient_not_a_pass_or_a_fail() -> None:
    assert E.falsification_report({})["status"] == "INSUFFICIENT_EVIDENCE"
    full = {c: True for c in E.FALSIFICATION_CHECKS}
    assert E.falsification_report(full)["status"] == "NO_FALSIFICATION_FOUND"
    assert E.falsification_report({**full, "lookahead": False})["status"] == "FAIL"
