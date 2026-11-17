from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.reports import (
    Limits,
    Preregistration,
    ReportError,
    StrategyRegistry,
    attribute_by,
    attribution,
    beta,
    committee,
    compare_all,
    cost_robustness,
    daily_report,
    depth_fill,
    desk_board,
    desk_stats,
    effective_n,
    ev_interval,
    evidence_bundle,
    fill_coverage,
    health,
    limit_breaches,
    manual_action,
    max_drawdown,
    no_streak_chasing,
    selection_regret,
    shortfall_bps,
    var_es,
)
from financial_brain.signals.evidence import (
    Counterfactual,
    DecisionRecord,
    EvidenceError,
    Tier,
    llm_may_order,
    promote,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def test_tiers_and_replay_never_forward():
    assert promote(Tier.FIXTURE, Tier.REPLAY, {"recorded_source_hash"}, is_replay=True) is Tier.REPLAY
    with pytest.raises(EvidenceError):
        promote(Tier.REPLAY, Tier.LOCAL_PAPER, {"recorded_source_hash", "decided_before_outcome"}, is_replay=True)
    with pytest.raises(EvidenceError):
        promote(Tier.LOCAL_PAPER, Tier.ATTESTED_FORWARD, {"recorded_source_hash", "decided_before_outcome"},
                is_replay=False)
    assert [t.name for t in Tier] == ["FIXTURE", "REPLAY", "LOCAL_PAPER", "ATTESTED_FORWARD", "BROKER_APPROVED"]


def test_decision_record_missing_explicit():
    r = DecisionRecord.build("S", T, {"mom": 0.1, "breadth": None}, expected_return=0.01, interval=(-0.01, 0.03),
                             benchmark="NIFTY 50 TRI", cost_estimate=None, risk_estimate=0.02,
                             contraindications=["earnings tomorrow"], holding_window=timedelta(days=5))
    assert not r.complete and set(r.missing) == {"breadth", "cost_estimate"}
    with pytest.raises(EvidenceError):
        DecisionRecord.build("S", T, {}, expected_return=0.05, interval=(0, 0.01), benchmark="b",
                             cost_estimate=0, risk_estimate=0, contraindications=[], holding_window=timedelta(days=1))


def test_counterfactual_never_in_portfolio():
    c = Counterfactual("S", T, "cost")
    c.observe(T + timedelta(days=1), 0.02)
    assert not c.counts_in_portfolio
    with pytest.raises(EvidenceError):
        c.observe(T, 0.0)
    assert llm_may_order() is False


def test_daily_report_and_attribution():
    r = daily_report("SWING", opening_nav=10000, flows=1000, realized=100, unrealized=-20, fees=10,
                     closed=2, open_=1, exposure=5000)
    assert r["net_pnl"] == 70 and r["net_return"] == pytest.approx(70 / 11000)
    a = attribution(forecast_error=50, market=30, timing=-10, spread=5, impact=2, fees=3, unpriced=0, total=55)
    assert a["parts"]["residual"] == pytest.approx(-5)
    assert attribute_by([{"desk": "A", "pnl": 10}, {"desk": "A", "pnl": 10, "weight": 0.5}], "desk") == {"A": 15}


def test_risk_diagnostics_and_limits():
    var, es = var_es([-10, -5, 0, 5, 10] * 4, 0.9)
    assert var == 10 and es == 10
    assert beta([0.02, -0.02, 0.04], [0.01, -0.01, 0.02]) == pytest.approx(2.0)
    b = limit_breaches(Limits(0.1, 100, 200, 50), drawdown=0.2, by_instrument={"X": 150},
                       by_underlying={"N": 100}, premium=60)
    assert b == ["drawdown", "instrument X", "premium at risk"]
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(0.25)


def test_performance_and_robustness():
    s = desk_stats([0.01, -0.005, 0.02, -0.01], 1.2, 8)
    assert s["ci"][0] < s["expectancy"] < s["ci"][1] and s["hit_rate"] == 0.5
    c = cost_robustness([1.0, 2.0], 0.5)
    assert c["2.0x"] < c["1.0x"]
    assert effective_n([0.01, 0.01, -0.01, -0.01, 0.01, 0.01] * 5, holding=5) < 30
    f = fill_coverage(10, 6, 1)
    assert f["missed_rate"] == pytest.approx(0.3)
    assert desk_board({"A": 5, "B": -3})[0] == ("B", -3)
    assert committee({"A": 0.2, "B": -0.1}) == {"A": 1.0, "B": 0.0}
    assert no_streak_chasing({"A": 0.1}, {"A": 0.3}, {"A": -0.01}) == ["A"]
    lo, hi = ev_interval([1, -1, 2, 0.5, -0.5])
    assert lo < hi


def test_execution_sim():
    q, px = depth_fill([(100.0, 100), (100.5, 100)], 15, 0.1)
    assert q == 15 and px == pytest.approx((10 * 100 + 5 * 100.5) / 15)
    assert depth_fill([(100.0, 5)], 10, 0.1) == (0, None)
    assert shortfall_bps(100, 100.1, "BUY") == pytest.approx(10)


def test_regret_registry_compare_bundle_health():
    assert selection_regret([1, 2], [3])["regret"] == 1.5
    reg = StrategyRegistry()
    p = Preregistration("h", "e", "x", 1000, "NIFTY TRI", "2026-11-01", 60)
    reg.register("swing", "v1", "code", {"a": 1}, "c1", "SWING", p)
    with pytest.raises(ReportError):
        reg.register("swing", "v1", "code2", {"a": 1}, "c1", "SWING", p)
    with pytest.raises(ReportError):
        reg.register("x", "v1", "c", {}, "c1", "SWING", Preregistration("", "e", "x", 1, "b", "d", 1))
    c = compare_all(0.12, cash=0.06, benchmark=0.10, naive=0.09, champion=0.11, momentum_beta=0.5,
                    momentum_return=0.04)
    assert c["selection_alpha"] == pytest.approx(0.0)
    b = evidence_bundle({"signals": ["S"], "nav": 1})
    assert b["attestation"] == "NONE" and len(b["sha256"]) == 64
    assert health(0.995, 100, 0, 0, True, 1, 0, 0)["status"] == "OK"
    assert health(0.9, 100, 2, 0, True, 0, 0, 1)["problems"] == ["feed", "gaps", "stale"]
    assert manual_action("APPROVE") == "PAPER_APPROVE"
    with pytest.raises(ReportError):
        manual_action("BUY_LIVE")
