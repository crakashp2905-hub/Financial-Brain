from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.features.cash_management import (
    CashAlternative,
    CashManagementError,
    compare_cash_alternatives,
)
from financial_brain.forward_edge.data_permissions import DataPermission, DataUse, PermissionStatus


NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
HASH = "a" * 64
PERMISSIONS = tuple(DataPermission(
    source_id="AUTHORIZED_AMC", use=use, status=PermissionStatus.USE_ALLOWED,
    effective_from=NOW.date(), effective_through=None, evidence_ref=f"evidence-{use.value}", reviewer="owner",
) for use in (DataUse.RESEARCH, DataUse.PAPER_SIMULATION))


def alternative(**overrides):
    values = {
        "instrument_id": "LIQUID-1",
        "product_kind": "LIQUID_FUND",
        "annualized_yield_bps": Decimal(1500),
        "source_id": "AUTHORIZED_AMC",
        "source_ref": "scenario-snapshot",
        "source_sha256": HASH,
        "published_at": NOW - timedelta(minutes=2),
        "observed_at": NOW - timedelta(minutes=1),
        "available_at": NOW - timedelta(minutes=1),
        "settlement_lag_business_days": 1,
        "subscription_cost_bps": Decimal(2),
        "redemption_cost_bps": Decimal(3),
        "tax_rate_bps": Decimal(2000),
        "stress_loss_bps": Decimal(15),
        "liquidity_stress_bps": Decimal(5),
        "capacity_inr": Decimal(8000),
    }
    values.update(overrides)
    return CashAlternative(**values)


def test_cash_alternative_is_allocated_only_after_cost_tax_lag_and_risk_haircuts():
    result = compare_cash_alternatives(
        [alternative()], decision_time=NOW, principal_inr=10000,
        holding_period_business_days=10, max_evidence_age=timedelta(minutes=5),
        permissions=PERMISSIONS,
    )

    product = next(row for row in result["scenarios"] if row["instrument_id"] == "LIQUID-1")
    cash = next(row for row in result["scenarios"] if row["instrument_id"] == "CASH")
    assert product["eligible"] is True
    assert product["net_expected_bps"] > cash["net_expected_bps"]
    assert product["allocation_inr"] == Decimal(5000)
    assert len(product["scenario_sha256"]) == 64
    assert result["cash_reserve_inr"] == Decimal(1000)
    assert result["allocated_stress_budget_inr"] <= result["stress_loss_budget_inr"]
    assert result["status"] == "SCENARIO_ESTIMATE"


@pytest.mark.parametrize("overrides,reason", [
    ({"settlement_lag_business_days": 4}, "payout_lag_exceeds_holding_period"),
    ({"published_at": NOW - timedelta(days=4), "observed_at": NOW - timedelta(days=3),
      "available_at": NOW - timedelta(days=3)}, "evidence_stale"),
    ({"available_at": NOW + timedelta(seconds=1)}, "evidence_not_yet_available"),
    ({"tax_rate_bps": None}, "tax_assumption_missing"),
])
def test_ineligible_or_unavailable_cash_product_is_not_allocated(overrides, reason):
    result = compare_cash_alternatives(
        [alternative(**overrides)], decision_time=NOW, principal_inr=10000,
        holding_period_business_days=2, max_evidence_age=timedelta(hours=1),
            permissions=PERMISSIONS,
    )

    product = next(row for row in result["scenarios"] if row["instrument_id"] == "LIQUID-1")
    assert product["eligible"] is False
    assert product["allocation_inr"] == Decimal(0)
    assert reason in product["reasons"]


def test_high_cost_and_stress_can_make_return_worse_than_cash():
    result = compare_cash_alternatives(
        [alternative(annualized_yield_bps=Decimal(100), subscription_cost_bps=Decimal(10),
                     redemption_cost_bps=Decimal(10), stress_loss_bps=Decimal(50))],
        decision_time=NOW, principal_inr=10000, holding_period_business_days=5,
        max_evidence_age=timedelta(minutes=5),
        permissions=PERMISSIONS,
    )

    product = next(row for row in result["scenarios"] if row["instrument_id"] == "LIQUID-1")
    assert product["net_expected_bps"] < 0
    assert product["eligible"] is False
    assert "does_not_beat_cash_after_cost_and_risk" in product["reasons"]


def test_missing_tax_assumption_never_becomes_a_zero_tax_allocation():
    result = compare_cash_alternatives(
        [alternative(tax_rate_bps=None)], decision_time=NOW, principal_inr=10000,
        holding_period_business_days=10, max_evidence_age=timedelta(minutes=5),
        permissions=PERMISSIONS,
    )
    product = next(row for row in result["scenarios"] if row["instrument_id"] == "LIQUID-1")
    assert product["tax_bps"] == 0
    assert product["eligible"] is False
    assert product["allocation_inr"] == 0


def test_cash_control_remains_when_no_alternatives_are_supplied():
    result = compare_cash_alternatives(
        [], decision_time=NOW, principal_inr=1000, holding_period_business_days=1,
        max_evidence_age=timedelta(minutes=1), permissions=PERMISSIONS,
    )
    assert len(result["scenarios"]) == 1
    assert result["scenarios"][0]["instrument_id"] == "CASH"
    assert result["scenarios"][0]["allocation_inr"] == 0


def test_invalid_source_hash_or_duplicate_instrument_fails_closed():
    with pytest.raises(CashManagementError, match="source_sha256"):
        alternative(source_sha256="bad")
    with pytest.raises(CashManagementError, match="duplicate instrument_id"):
        compare_cash_alternatives(
            [alternative(), alternative()], decision_time=NOW, principal_inr=1000,
            holding_period_business_days=1, max_evidence_age=timedelta(minutes=5),
            permissions=PERMISSIONS,
        )


def test_missing_or_wrong_source_rights_blocks_candidate_allocation():
    result = compare_cash_alternatives(
        [alternative()], decision_time=NOW, principal_inr=10000,
        holding_period_business_days=10, max_evidence_age=timedelta(minutes=5),
        permissions=(PERMISSIONS[0],),
    )

    product = next(row for row in result["scenarios"] if row["instrument_id"] == "LIQUID-1")
    assert product["eligible"] is False
    assert product["allocation_inr"] == 0
    assert "data_rights_missing:PAPER_SIMULATION" in product["reasons"]
