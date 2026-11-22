from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_systematic_flows import (
    MFSystematicFlowBasis, MFSystematicFlowError, MFSystematicFlowEvent,
    MFSystematicFlowEventKind, MFSystematicFlowInstruction,
    MFSystematicFlowKind, MFSystematicFlowLifecycle,
)


START = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


def _instruction(kind, **changes):
    values = {
        "instruction_id": f"{kind.value}-2026-11", "plan_id": f"{kind.value}-plan",
        "account_id": "paper-account", "folio_id": "SYNTHETIC-FOLIO",
        "kind": kind, "scheduled_for": date(2026, 11, 23),
        "source_scheme": "111111", "source_plan": "DIRECT",
        "source_option": "GROWTH", "source_isin": "INF111111111",
        "amount_inr": Decimal("10000"),
    }
    if kind is MFSystematicFlowKind.STP:
        values.update(target_scheme="222222", target_plan="DIRECT",
                      target_option="GROWTH", target_isin="INF222222222")
    return MFSystematicFlowInstruction(**(values | changes))


def _event(kind, seconds, **changes):
    values = {
        "event_id": kind.value.lower(), "kind": kind,
        "effective_at": START + timedelta(seconds=seconds),
        "basis": MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
    }
    return MFSystematicFlowEvent(**(values | changes))


def _redemption_events():
    return (
        _event(MFSystematicFlowEventKind.REDEMPTION_REQUESTED, 10),
        _event(MFSystematicFlowEventKind.VALUE_CONFIRMED, 20,
               amount_inr=Decimal("9500"), gross_inr=Decimal("10000"),
               exit_load_inr=Decimal("500"), other_charges_inr=Decimal("0")),
        _event(MFSystematicFlowEventKind.PAYOUT_AVAILABLE, 30,
               amount_inr=Decimal("9500")),
    )


def test_swp_books_redemption_load_and_delays_cash_until_payout():
    lifecycle = MFSystematicFlowLifecycle(_instruction(MFSystematicFlowKind.SWP))
    for event in _redemption_events():
        lifecycle = lifecycle.append(event)
    assert lifecycle.stage == MFSystematicFlowEventKind.PAYOUT_AVAILABLE.value
    assert lifecycle.available_to_redeploy_inr == Decimal("9500")
    assert len(lifecycle.state_sha256) == 64
    with pytest.raises(MFSystematicFlowError, match="ordering"):
        lifecycle.append(_event(MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 40,
                                amount_inr=Decimal("1")))


def test_stp_purchase_waits_for_settled_proceeds_and_cannot_overspend():
    lifecycle = MFSystematicFlowLifecycle(_instruction(MFSystematicFlowKind.STP))
    for event in _redemption_events():
        lifecycle = lifecycle.append(event)
    assert lifecycle.available_to_redeploy_inr == Decimal("9500")
    funding = _event(MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 40,
                     amount_inr=Decimal("9000"))
    lifecycle = lifecycle.append(funding)
    assert lifecycle.available_to_redeploy_inr == Decimal("500")
    allotment = _event(MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED, 50,
                       amount_inr=Decimal("9000"), nav_per_unit=Decimal("90"),
                       units=Decimal("100"))
    assert lifecycle.append(allotment).stage == "ALLOTMENT_CONFIRMED"
    with pytest.raises(MFSystematicFlowError, match="more than settled proceeds"):
        MFSystematicFlowLifecycle(_instruction(MFSystematicFlowKind.STP),
            _redemption_events() + (
                _event(MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 40,
                       amount_inr=Decimal("9501")),
            ))


def test_stp_rejects_same_time_redeployment_and_value_must_include_loads():
    request, value, _ = _redemption_events()
    with pytest.raises(MFSystematicFlowError, match="gross less charges"):
        _event(MFSystematicFlowEventKind.VALUE_CONFIRMED, 20,
               amount_inr=Decimal("9900"), gross_inr=Decimal("10000"),
               exit_load_inr=Decimal("500"), other_charges_inr=Decimal(0))
    lifecycle = MFSystematicFlowLifecycle(_instruction(MFSystematicFlowKind.STP),
                                           (request, value,
                                            _event(MFSystematicFlowEventKind.PAYOUT_AVAILABLE, 30,
                                                   amount_inr=Decimal("9500"))))
    with pytest.raises(MFSystematicFlowError, match="asynchronous"):
        lifecycle.append(_event(MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 30,
                                amount_inr=Decimal("100")))


def test_each_missed_periodic_instruction_is_terminal_and_stp_requires_target_identity():
    missed = _event(MFSystematicFlowEventKind.MISSED, 10, reason="no eligible NAV")
    lifecycle = MFSystematicFlowLifecycle(_instruction(MFSystematicFlowKind.SWP), (missed,))
    with pytest.raises(MFSystematicFlowError, match="ordering"):
        lifecycle.append(_event(MFSystematicFlowEventKind.REDEMPTION_REQUESTED, 20))
    with pytest.raises(MFSystematicFlowError, match="target scheme identity"):
        _instruction(MFSystematicFlowKind.STP, target_isin=None)
