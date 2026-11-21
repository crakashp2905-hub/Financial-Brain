"""F15 - refinements of earlier partial items. Synthetic data; none of this proves alpha."""
from __future__ import annotations

import random
from decimal import Decimal as D
from pathlib import Path

import pytest

from financial_brain.forward_edge import evidence_stats as ES
from financial_brain.forward_edge import refinements as R


# ---- FE-0059 ----

def test_inventory_after_sliced_orders_is_the_sum_of_what_each_slice_filled() -> None:
    out = R.slice_inventory(2500, 1000, [1000, 1000, 300])
    assert out["slices"] == [1000, 1000, 500] and out["filled"] == 2300 and out["unfilled"] == 200 and out["complete"] is False
    assert R.slice_inventory(2500, 1000, [1000, 1000, 500])["complete"] is True
    with pytest.raises(R.RefinementError):
        R.slice_inventory(2500, 1000, [1100, 1000, 0])
    with pytest.raises(R.RefinementError):
        R.slice_inventory(2500, 1000, [1000])


# ---- FE-0062 ----

def test_legging_loss_is_only_the_adverse_move_of_the_second_leg() -> None:
    assert R.legging_loss(first_leg_fill=D("10"), second_leg_planned=D("100"), second_leg_actual=D("99"), units=50, first_is_buy=True) == D("50")
    assert R.legging_loss(first_leg_fill=D("10"), second_leg_planned=D("100"), second_leg_actual=D("101"), units=50, first_is_buy=True) == D("0")
    assert R.legging_loss(first_leg_fill=D("10"), second_leg_planned=D("100"), second_leg_actual=D("102"), units=10, first_is_buy=False) == D("20")


# ---- FE-0066 ----

def test_counterfactual_fills_bracket_the_realised_price() -> None:
    out = R.counterfactual_fills([D("101"), D("100"), D("103")], "BUY")
    assert out["best"] == D("100") and out["worst"] == D("103") and out["range"] == D("3") and out["n"] == 3
    sell = R.counterfactual_fills([D("99"), D("98"), D("100")], "SELL")
    assert sell["best"] == D("100") and sell["worst"] == D("98")
    with pytest.raises(R.RefinementError):
        R.counterfactual_fills([], "BUY")


# ---- FE-0067 ----

def test_settlement_identity_covers_cash_fees_financing_and_released_margin() -> None:
    kw = dict(trade_value=D("1000"), fees=D("3"), financing=D("1"), margin_blocked_before=D("200"),
              margin_blocked_after=D("150"), margin_released=D("50"), side="BUY")
    assert R.settlement_identity(cash_before=D("5000"), cash_after=D("3996"), **kw) is True
    assert R.settlement_identity(cash_before=D("5000"), cash_after=D("3997"), **kw) is False
    assert R.settlement_identity(cash_before=D("5000"), cash_after=D("5996"), **{**kw, "side": "SELL"}) is True
    assert R.settlement_identity(cash_before=D("5000"), cash_after=D("3996"), **{**kw, "margin_released": D("40")}) is False


# ---- FE-0093 ----

def test_two_way_clustering_reduces_to_one_way_when_one_dimension_is_unique() -> None:
    rng = random.Random(3)
    dates, names, values = [], [], []
    for d in range(8):
        shock = rng.gauss(0, 0.02)
        for i in range(6):
            dates.append(f"d{d}")
            names.append(f"n{d}-{i}")                    # every observation has its own name
            values.append(shock + rng.gauss(0, 0.002))
    assert R.two_way_cluster_se(values, dates, names) == pytest.approx(ES.cluster_se(values, dates))


def test_two_way_clustering_exceeds_naive_when_both_dates_and_names_carry_shocks() -> None:
    rng = random.Random(4)
    date_shock = {f"d{i}": rng.gauss(0, 0.02) for i in range(10)}
    name_shock = {f"n{j}": rng.gauss(0, 0.02) for j in range(10)}
    dates, names, values = [], [], []
    for d in date_shock:
        for n in name_shock:
            dates.append(d)
            names.append(n)
            values.append(date_shock[d] + name_shock[n] + rng.gauss(0, 0.002))
    assert R.two_way_cluster_se(values, dates, names) > 3 * ES.naive_se(values)
    with pytest.raises(R.RefinementError):
        R.two_way_cluster_se([1.0, 2.0], ["a", "b"], ["x", "y"])


# ---- FE-0094 ----

def _noise(seed: int, n: int = 250, mu: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, 0.01) for _ in range(n)]


def test_reality_check_does_not_reward_the_best_of_many_noise_candidates() -> None:
    cands = {f"c{i}": _noise(100 + i) for i in range(20)}
    out = R.reality_check_pvalue(cands, resamples=400, seed=1)
    assert out["candidates_counted"] == 20 and out["p_value"] > 0.10


def test_reality_check_detects_a_genuinely_strong_candidate_among_noise() -> None:
    cands = {f"c{i}": _noise(100 + i) for i in range(19)}
    cands["real"] = _noise(999, mu=0.004)
    out = R.reality_check_pvalue(cands, resamples=400, seed=1)
    assert out["best"] == "real" and out["p_value"] < 0.05


def test_reality_check_is_seeded_and_needs_data() -> None:
    cands = {"a": _noise(1), "b": _noise(2)}
    assert R.reality_check_pvalue(cands, resamples=100, seed=5) == R.reality_check_pvalue(cands, resamples=100, seed=5)
    with pytest.raises(R.RefinementError):
        R.reality_check_pvalue({"a": [0.1, 0.2]})


# ---- FE-0095 ----

def test_drawdown_interval_tail_drawdown_and_turnover() -> None:
    x = _noise(7, n=300)
    lo, hi = R.drawdown_ci(x, resamples=200, seed=2)
    assert lo <= hi <= 0.0
    assert ES.drawdown_stats(x)["max_drawdown"] <= R.tail_conditional_drawdown(x) <= 0.0
    assert R.turnover([{"A": 1.0}, {"B": 1.0}]) == 1.0
    assert R.turnover([{"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}]) == 0.0
    with pytest.raises(R.RefinementError):
        R.turnover([{"A": 1.0}])


# ---- FE-0101 ----

def test_verdict_is_withheld_between_frozen_checkpoints() -> None:
    v = {"verdict": "PROMOTION_GRADE"}
    cp = (60, 120, 250)
    between = R.verdict_at_checkpoint(61, cp, v)
    assert between == {"verdict": "NOT_EVALUATED_BETWEEN_CHECKPOINTS", "next_checkpoint": 120}
    assert R.verdict_at_checkpoint(60, cp, v) is v
    assert R.verdict_at_checkpoint(300, cp, v)["next_checkpoint"] is None


# ---- FE-0106 ----

def test_allocator_is_deterministic_capped_bounded_and_keeps_cash() -> None:
    scores = {"A": 1.0, "B": 0.5, "C": -1.0}
    vols = {"A": 0.20, "B": 0.40, "C": 0.30}
    w = R.allocate(scores, vols, target_vol=0.10, max_weight=0.40)
    assert w == R.allocate(scores, vols, target_vol=0.10, max_weight=0.40)
    assert "C" not in w and abs(sum(w.values()) - 1.0) < 1e-12 and all(v >= 0 for v in w.values())
    assert max(v for k, v in w.items() if k != "CASH") <= 0.40 and w["CASH"] > 0
    assert R.allocate({"A": -1.0}, {"A": 0.2}, target_vol=0.1, max_weight=0.5) == {"CASH": 1.0}
    with pytest.raises(R.RefinementError):
        R.allocate({"A": 1.0}, {"B": 0.2}, target_vol=0.1, max_weight=0.5)


def test_a_higher_vol_target_never_lowers_the_invested_share() -> None:
    scores, vols = {"A": 1.0, "B": 1.0}, {"A": 0.3, "B": 0.3}
    low = R.allocate(scores, vols, target_vol=0.05, max_weight=1.0)
    high = R.allocate(scores, vols, target_vol=0.20, max_weight=1.0)
    assert high["CASH"] <= low["CASH"]


# ---- FE-0076 ----

def test_completeness_report_is_written_once_and_never_overwritten(tmp_path: Path) -> None:
    p = tmp_path / "reports" / "2027-01-04.json"
    R.write_completeness_once({"session_id": "s1", "eligibility": "ELIGIBLE_LOCAL_ONLY"}, p)
    with pytest.raises(R.RefinementError, match="immutable"):
        R.write_completeness_once({"session_id": "s1", "eligibility": "VERIFIED"}, p)
    assert "ELIGIBLE_LOCAL_ONLY" in p.read_text(encoding="utf-8")
