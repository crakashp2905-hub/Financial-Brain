from datetime import datetime, timezone

import pytest

from financial_brain.portfolio.capacity_optimizer import (
    CapacityCandidate,
    CapacityOptimizationError,
    optimize_capacity,
)


AS_OF = datetime(2026, 11, 25, 9, 15, tzinfo=timezone.utc)


def _candidate(candidate_id: str, **overrides) -> CapacityCandidate:
    values = {
        "candidate_id": candidate_id,
        "as_of": AS_OF,
        "price_per_share_inr": 100.0,
        "shares_per_lot": 10,
        "maximum_lots": 10,
        "expected_gross_pnl_per_share_inr": 10.0,
        "transaction_cost_per_share_inr": 0.5,
        "transaction_tax_per_share_inr": 0.2,
        "positive_gain_tax_rate": 0.2,
        "fixed_order_charge_inr": 20.0,
        "margin_per_share_inr": 30.0,
        "financing_principal_per_share_inr": 40.0,
        "financing_rate_annual": 0.1,
        "financing_days": 30,
        "settlement_cash_flows_per_share": {"2026-11-26": 100.0},
        "source_sha256": "a" * 64,
    }
    values.update(overrides)
    return CapacityCandidate(**values)


def test_optimizer_respects_whole_share_lots_cash_margin_leverage_and_all_costs():
    high_quality = _candidate("A")
    lower_quality = _candidate(
        "B", price_per_share_inr=50.0, shares_per_lot=1,
        expected_gross_pnl_per_share_inr=4.0, transaction_cost_per_share_inr=0.1,
        transaction_tax_per_share_inr=0.1, positive_gain_tax_rate=0.1,
        fixed_order_charge_inr=10.0, margin_per_share_inr=45.0,
        financing_principal_per_share_inr=30.0, financing_days=365,
        settlement_cash_flows_per_share={"2026-11-26": 50.0, "2026-11-27": 50.0},
        source_sha256="b" * 64,
    )
    result = optimize_capacity(
        candidates=(high_quality, lower_quality), as_of=AS_OF,
        cash_available_by_settlement_date_inr={"2026-11-26": 2_500.0, "2026-11-27": 2_500.0},
        margin_budget_inr=650.0, gross_leverage_limit_inr=2_500.0,
    )

    assert dict(result.lots_by_candidate) == {"A": 2}
    assert dict(result.shares_by_candidate) == {"A": 20}
    assert result.gross_notional_inr == 2_000.0
    assert result.margin_used_inr == 600.0
    assert result.settlement_cash_used_by_date_inr["2026-11-26"] == pytest.approx(
        2_074.0 + result.expected_financing_inr
    )
    assert result.settlement_cash_used_by_date_inr["2026-11-27"] == pytest.approx(
        2_074.0 + result.expected_financing_inr
    )
    assert result.expected_gross_pnl_inr == 200.0
    assert result.expected_transaction_costs_inr == 30.0
    assert result.expected_taxes_inr == 44.0
    assert result.expected_financing_inr == pytest.approx(40 * 0.1 * 30 / 365 * 20)
    assert result.expected_net_pnl_inr == pytest.approx(200 - 30 - 44 - result.expected_financing_inr)
    assert result.promotion_allowed is False
    assert len(result.allocation_sha256) == 64


def test_fixed_order_charge_prevents_unprofitable_one_lot_and_negative_alpha_stays_cash():
    candidate = _candidate(
        "A", expected_gross_pnl_per_share_inr=0.5,
        transaction_cost_per_share_inr=0.2, transaction_tax_per_share_inr=0.1,
        positive_gain_tax_rate=0.2, fixed_order_charge_inr=100.0,
    )
    result = optimize_capacity(
        candidates=(candidate,), as_of=AS_OF,
        cash_available_by_settlement_date_inr={"2026-11-26": 10_000.0},
        margin_budget_inr=10_000.0, gross_leverage_limit_inr=10_000.0,
    )
    assert dict(result.lots_by_candidate) == {}
    assert result.expected_net_pnl_inr == 0.0


def test_missing_or_invalid_source_and_uncovered_settlement_cash_are_rejected():
    with pytest.raises(CapacityOptimizationError, match="SHA-256"):
        _candidate("A", source_sha256="not-a-hash")
    with pytest.raises(CapacityOptimizationError, match="whole-share"):
        _candidate("A", shares_per_lot=0)
    with pytest.raises(CapacityOptimizationError, match="short-sale proceeds"):
        _candidate("A", settlement_cash_flows_per_share={"2026-11-26": -100.0})
    with pytest.raises(CapacityOptimizationError, match="cash availability is required"):
        optimize_capacity(
            candidates=(_candidate("A"),), as_of=AS_OF,
            cash_available_by_settlement_date_inr={"2026-11-27": 1_000.0},
            margin_budget_inr=1_000.0, gross_leverage_limit_inr=1_000.0,
        )


def test_as_of_mismatch_is_rejected():
    with pytest.raises(CapacityOptimizationError, match="allocation as-of clock"):
        optimize_capacity(
            candidates=(_candidate("A", as_of=datetime(2026, 11, 24, tzinfo=timezone.utc)),),
            as_of=AS_OF, cash_available_by_settlement_date_inr={"2026-11-26": 1_000.0},
            margin_budget_inr=1_000.0, gross_leverage_limit_inr=1_000.0,
        )
