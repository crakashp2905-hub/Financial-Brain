"""F09 - portfolio decisions and risk (FE-0104 .. FE-0113). Offline."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest
import numpy as np

from financial_brain.forward_edge import portfolio_risk as P
from financial_brain.forward_edge.approvals import Approval

NOW = datetime(2027, 1, 4, 5, 0, tzinfo=timezone.utc)
SHA = "e" * 64


# ---- FE-0104 ----

def test_allocation_must_sum_to_one_with_cash_and_has_a_stable_hash() -> None:
    a = P.Allocation((("M1", D("0.5")), ("ORB", D("0.2")), ("CASH", D("0.3"))))
    assert a.sha256() == P.Allocation((("CASH", D("0.3")), ("ORB", D("0.2")), ("M1", D("0.5")))).sha256()
    with pytest.raises(P.RiskError, match="sum"):
        P.Allocation((("M1", D("0.5")), ("CASH", D("0.4"))))
    with pytest.raises(P.RiskError, match="CASH"):
        P.Allocation((("M1", D("0.5")), ("ORB", D("0.5"))))


def test_a_losing_book_stays_in_the_combined_result() -> None:
    a = P.Allocation((("M1", D("0.5")), ("ORB", D("0.2")), ("CASH", D("0.3"))))
    total = P.combined_result(a, {"M1": D("0.02"), "ORB": D("-0.05")})
    assert total == D("0.010") + D("-0.010")
    with pytest.raises(P.RiskError, match="every funded book"):
        P.combined_result(a, {"M1": D("0.02")})


# ---- FE-0105 ----

LIMITS = P.Limits(max_gross=D("100"), max_net=D("100"), max_single_name=D("60"), max_sector=D("80"))
SECTORS = {"A": "IT", "B": "IT", "C": "BANK"}


def test_two_intents_that_are_each_fine_cannot_jointly_breach_a_limit() -> None:
    one = P.check_batch(LIMITS, [{"symbol": "A", "side": "BUY", "notional": "50"}], {}, SECTORS)
    assert one[0]["decision"] == "ALLOW"
    both = P.check_batch(LIMITS, [{"symbol": "A", "side": "BUY", "notional": "50"},
                                  {"symbol": "B", "side": "BUY", "notional": "50"}], {}, SECTORS)
    assert [v["decision"] for v in both] == ["ALLOW", "NO_TRADE"] and both[1]["reasons"] == ["SECTOR"]


def test_single_name_gross_and_net_limits_report_reasons() -> None:
    v = P.check_batch(LIMITS, [{"symbol": "A", "side": "BUY", "notional": "70"}], {}, SECTORS)
    assert v[0]["reasons"] == ["SINGLE_NAME"]
    v = P.check_batch(LIMITS, [{"symbol": "C", "side": "BUY", "notional": "50"}], {"A": D("55")}, SECTORS)
    assert v[0]["decision"] == "NO_TRADE" and v[0]["reasons"] == ["GROSS", "NET"]


# ---- FE-0106 ----

def test_vol_target_cvar_and_concentration_are_deterministic_and_bounded() -> None:
    assert P.vol_target_scale(0.30, 0.15) == 0.5 and P.vol_target_scale(0.05, 0.15) == 1.0
    assert P.cvar([-0.05, -0.03, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07], 0.2) == pytest.approx(-0.04)
    assert P.concentration_penalty([1, 1, 1, 1]) == pytest.approx(0.25)
    assert P.concentration_penalty([1, 0, 0, 0]) == 1.0
    with pytest.raises(P.RiskError):
        P.vol_target_scale(0, 0.1)


# ---- FE-0107 ----

def test_cash_and_margin_cannot_be_double_allocated() -> None:
    b = P.ExposureBook(D("1000"))
    b.reserve("o1", D("600"))
    with pytest.raises(P.RiskError, match="double"):
        b.reserve("o2", D("500"))
    b.release("o1")
    b.reserve("o2", D("500"))
    assert b.unreserved() == D("500")


def test_worst_case_spread_loss_is_width_minus_credit_times_quantity() -> None:
    assert P.worst_case_spread_loss(D("50"), D("12"), 3) == D("114")
    with pytest.raises(P.RiskError):
        P.worst_case_spread_loss(D("50"), D("60"), 1)


# ---- FE-0108 ----

def _approval(actor_id="owner:akash", actor_type="human") -> Approval:
    return Approval("DISARM_RESET", "kill_switch", SHA, actor_id, actor_type, NOW - timedelta(hours=1),
                    NOW + timedelta(hours=1))


def test_kill_switch_persists_across_restart_and_agents_cannot_reset_it(tmp_path: Path) -> None:
    path = tmp_path / "ks.json"
    P.KillSwitch(path).trip("DAILY_LOSS")
    restarted = P.KillSwitch(path)
    assert restarted.tripped() is True
    with pytest.raises(P.RiskError, match="cannot approve"):
        restarted.reset(_approval("agent:risk-bot", "agent"), now=NOW, scope_sha256=SHA)
    assert P.KillSwitch(path).tripped() is True
    restarted.reset(_approval(), now=NOW, scope_sha256=SHA)
    assert P.KillSwitch(path).tripped() is False


# ---- FE-0109 ----

def test_equity_change_is_fully_explained_by_ledger_events() -> None:
    out = P.attribute([{"kind": "REALIZED", "amount": D("1000")}, {"kind": "UNREALIZED", "amount": D("-200")},
                       {"kind": "FEES", "amount": D("-50")}, {"kind": "SLIPPAGE", "amount": D("-30")},
                       {"kind": "CASH_YIELD", "amount": D("5")}])
    assert out["EQUITY_CHANGE"] == D("725")


# ---- FE-0110 ----

def test_throttle_is_versioned_and_changes_hash_when_edited() -> None:
    p1 = P.ThrottlePolicy(1, ((0.20, 0.5), (0.35, 0.25)))
    assert [p1.scale(v) for v in (0.1, 0.2, 0.4)] == [1.0, 0.5, 0.25]
    p2 = P.ThrottlePolicy(2, ((0.20, 0.6), (0.35, 0.25)))
    assert p1.sha256() != p2.sha256()


# ---- FE-0111 ----

def test_capacity_is_limited_by_adv_participation_and_visible_depth() -> None:
    assert P.capacity_notional(D("1000000"), D("40000"), D("0.05")) == D("40000")
    assert P.capacity_notional(D("1000000"), D("400000"), D("0.05")) == D("50000.00")


def test_order_capacity_requires_fresh_hashed_adv_and_depth_and_applies_safety_margin() -> None:
    args = dict(order_notional=D("40000"), adv_notional=D("1000000"), adv_as_of=date(2027, 1, 4),
                adv_source_sha256="a" * 64, depth_notional=D("80000"), depth_observed_at=NOW,
                depth_source_sha256="b" * 64, order_at=NOW, max_adv_age_days=1,
                max_depth_age=timedelta(seconds=2), participation_cap=D("0.10"),
                safety_factor=D("0.5"))
    allowed = P.assess_order_capacity(**args)
    assert allowed["decision"] == "ALLOW"
    assert allowed["allowed_notional"] == "40000.0"
    assert P.assess_order_capacity(**{**args, "order_notional": D("50001")})["reason"] == "CAPACITY_EXCEEDED"
    assert P.assess_order_capacity(**{**args, "adv_notional": None})["reason"] == "MISSING_CAPACITY_EVIDENCE"
    assert P.assess_order_capacity(**{**args, "depth_observed_at": NOW - timedelta(seconds=3)})["reason"] == \
        "STALE_OR_FUTURE_DEPTH_EVIDENCE"
    assert P.assess_order_capacity(**{**args, "adv_as_of": date(2026, 12, 1)})["reason"] == "STALE_ADV_EVIDENCE"


def test_multiple_strategy_books_share_market_and_correlated_risk_ceilings() -> None:
    books = {
        "swing": {"MARKET": D("20"), "TREND": D("60"), "VALUE": D("0")},
        "intraday": {"MARKET": D("20"), "TREND": D("0"), "VALUE": D("60")},
    }
    covariance = np.array([[0.1, 0.0, 0.0], [0.0, 1.0, 0.9], [0.0, 0.9, 1.0]])
    combined = P.aggregate_correlated_books(
        book_exposures=books, factor_names=("MARKET", "TREND", "VALUE"), covariance=covariance,
        max_market_exposure=D("60"), max_portfolio_volatility=D("100"))
    assert combined["decision"] == "NO_TRADE"
    assert combined["reasons"] == ["CORRELATED_PORTFOLIO_RISK_LIMIT"]
    assert D(combined["market_exposure"]) == D("40")

    market_limit = P.aggregate_correlated_books(
        book_exposures={key: {**value, "MARKET": D("40")} for key, value in books.items()},
        factor_names=("MARKET", "TREND", "VALUE"), covariance=np.eye(3),
        max_market_exposure=D("60"), max_portfolio_volatility=D("200"))
    assert market_limit["decision"] == "NO_TRADE"
    assert market_limit["reasons"] == ["MARKET_EXPOSURE_LIMIT"]


# ---- FE-0112 ----

@pytest.mark.parametrize("shock", [{"A": D("-0.2")}, {"A": D("-0.1"), "B": D("-0.1")}, {"A": D("0.3")}])
def test_disaster_shocks_keep_the_cash_and_equity_identity(shock) -> None:
    out = P.apply_shock(D("100000"), {"A": (100, D("500")), "B": (50, D("1000"))}, shock)
    assert out["invariant_holds"] is True


# ---- FE-0113 ----

def test_no_trade_decisions_are_explicit_with_reasons() -> None:
    assert P.decide(liquidity_ok=False, margin_known=True, margin_sufficient=True) == \
        {"decision": "NO_TRADE", "reason": "INSUFFICIENT_LIQUIDITY"}
    assert P.decide(liquidity_ok=True, margin_known=False, margin_sufficient=True)["reason"] == "UNCERTAIN_MARGIN"
    assert P.decide(liquidity_ok=True, margin_known=True, margin_sufficient=False)["reason"] == "INSUFFICIENT_MARGIN"
    assert P.decide(liquidity_ok=True, margin_known=True, margin_sufficient=True)["decision"] == "TRADE"
