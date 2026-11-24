from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_switch import (
    MFSwitchBasis, MFSwitchError, MFSwitchEvent, MFSwitchEventKind,
    MFSwitchLifecycle, MFSwitchRequest,
)
from financial_brain.kernel.units import INRNotional, MutualFundUnits, NavPerUnit


START = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


def _request(**changes):
    values = {
        "switch_id": "switch-1", "account_id": "paper-account",
        "folio_id": "SYNTHETIC-FOLIO", "source_scheme": "111111",
        "source_plan": "DIRECT", "source_option": "GROWTH", "source_isin": "INF111111111",
        "target_scheme": "222222", "target_plan": "DIRECT", "target_option": "GROWTH",
        "target_isin": "INF222222222", "requested_units": Decimal("12.5"),
        "requested_at": START,
    }
    return MFSwitchRequest(**(values | changes))


def _event(kind, seconds, amount, **changes):
    values = {
        "event_id": kind.value.lower(), "kind": kind,
        "effective_at": START + timedelta(seconds=seconds),
        "basis": MFSwitchBasis.SIMULATED_ASSUMPTION,
        "amount_inr": Decimal(amount),
    }
    return MFSwitchEvent(**(values | changes))


def test_switch_pair_waits_for_available_proceeds_before_redeploying_capital():
    lifecycle = MFSwitchLifecycle(_request())
    value = _event(MFSwitchEventKind.SWITCH_OUT_VALUE_CONFIRMED, 10, "10000")
    proceeds = _event(MFSwitchEventKind.SWITCH_OUT_PROCEEDS_AVAILABLE, 20, "10000")
    funding = _event(MFSwitchEventKind.SWITCH_IN_FUNDS_APPLIED, 30, "7500")
    allotment = _event(
        MFSwitchEventKind.SWITCH_IN_ALLOTMENT_CONFIRMED, 40, "7500",
        units=Decimal("500"), nav_per_unit=Decimal("15"),
    )
    lifecycle = lifecycle.append(value)
    assert lifecycle.proceeds_available_inr == 0
    with pytest.raises(MFSwitchError, match="out-value, proceeds, funding"):
        lifecycle.append(funding)
    lifecycle = lifecycle.append(proceeds)
    assert lifecycle.unapplied_proceeds_inr == Decimal("10000")
    lifecycle = lifecycle.append(funding)
    assert lifecycle.unapplied_proceeds_inr == Decimal("2500")
    lifecycle = lifecycle.append(allotment)
    assert lifecycle.stage == MFSwitchEventKind.SWITCH_IN_ALLOTMENT_CONFIRMED.value
    assert len(lifecycle.state_sha256) == 64
    with pytest.raises(FrozenInstanceError):
        lifecycle.events = ()


def test_switch_accepts_nominal_mf_units_and_inr_nav_values():
    request = _request(requested_units=MutualFundUnits(Decimal("12.5")))
    event = _event(
        MFSwitchEventKind.SWITCH_IN_ALLOTMENT_CONFIRMED, 10, "9000",
        amount_inr=INRNotional(Decimal("9000")),
        units=MutualFundUnits(Decimal("100")), nav_per_unit=NavPerUnit(Decimal("90")),
    )
    assert request.requested_units == Decimal("12.5")
    assert event.amount_inr == Decimal("9000")
    assert event.units == Decimal("100")
    assert event.nav_per_unit == Decimal("90")


def test_switch_in_cannot_reuse_proceeds_in_same_timestamp_or_before_availability():
    lifecycle = MFSwitchLifecycle(_request()).append(
        _event(MFSwitchEventKind.SWITCH_OUT_VALUE_CONFIRMED, 10, "5000")
    ).append(_event(MFSwitchEventKind.SWITCH_OUT_PROCEEDS_AVAILABLE, 20, "5000"))
    with pytest.raises(MFSwitchError, match="strict timestamp order"):
        lifecycle.append(_event(MFSwitchEventKind.SWITCH_IN_FUNDS_APPLIED, 20, "5000"))


@pytest.mark.parametrize("events, message", [
    ([_event(MFSwitchEventKind.SWITCH_OUT_VALUE_CONFIRMED, 10, "100"),
      _event(MFSwitchEventKind.SWITCH_OUT_PROCEEDS_AVAILABLE, 20, "101")], "match confirmed net"),
    ([_event(MFSwitchEventKind.SWITCH_OUT_VALUE_CONFIRMED, 10, "100"),
      _event(MFSwitchEventKind.SWITCH_OUT_PROCEEDS_AVAILABLE, 20, "100"),
      _event(MFSwitchEventKind.SWITCH_IN_FUNDS_APPLIED, 30, "101")], "exceeds available"),
])
def test_switch_lifecycle_reconciles_amounts(events, message):
    lifecycle = MFSwitchLifecycle(_request())
    for event in events[:-1]:
        lifecycle = lifecycle.append(event)
    with pytest.raises(MFSwitchError, match=message):
        lifecycle.append(events[-1])


def test_allotment_must_reconcile_to_applied_amount():
    with pytest.raises(MFSwitchError, match="units x NAV"):
        _event(
            MFSwitchEventKind.SWITCH_IN_ALLOTMENT_CONFIRMED, 10, "100",
            units=Decimal("5"), nav_per_unit=Decimal("19"),
        )


def test_switch_request_requires_distinct_schemes_and_aware_request_clock():
    with pytest.raises(MFSwitchError, match="identities must differ"):
        _request(target_scheme="111111", target_isin="INF111111111")
    with pytest.raises(MFSwitchError, match="timezone-aware"):
        _request(requested_at=datetime(2026, 11, 23, 5))
