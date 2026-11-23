from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_sip import (
    MFSIPError, MFSIPInstallment, MFSIPSeries, MFSIPState,
    validate_installment_transition,
)
from financial_brain.kernel.units import INRNotional, MutualFundUnits, NavPerUnit


START = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


def _installment(**changes):
    values = {
        "installment_id": "sip-1-2026-11", "plan_id": "sip-plan-1",
        "account_id": "paper-account", "folio_id": "SYNTHETIC-FOLIO",
        "scheme_code": "123456", "plan": "DIRECT", "option": "GROWTH",
        "scheduled_for": date(2026, 11, 23), "amount_inr": Decimal("5000"),
    }
    return MFSIPInstallment(**(values | changes))


def test_each_installment_has_independent_payment_and_allotment_outcome():
    missed = _installment(
        installment_id="sip-plan-1-2026-10", scheduled_for=date(2026, 10, 23),
        state=MFSIPState.MISSED, missed_at=START - timedelta(days=31),
        miss_reason="mandate debit failed",
    )
    paid = _installment(
        payment_initiated_at=START, funds_available_at=START + timedelta(minutes=2),
        state=MFSIPState.FUNDS_AVAILABLE,
    )
    assert missed.state is MFSIPState.MISSED
    assert paid.state is MFSIPState.FUNDS_AVAILABLE
    assert missed.installment_id != paid.installment_id
    with pytest.raises(FrozenInstanceError):
        paid.state = MFSIPState.ALLOTTED


def test_nav_pending_requires_payment_clocks_and_does_not_invent_nav_or_units():
    pending = _installment(
        state=MFSIPState.NAV_PENDING, payment_initiated_at=START,
        funds_available_at=START + timedelta(minutes=1),
    )
    assert pending.nav_date is None
    assert pending.nav_per_unit is None
    assert pending.units_allotted is None
    with pytest.raises(MFSIPError, match="independently known payment milestones"):
        _installment(state=MFSIPState.NAV_PENDING, payment_initiated_at=START)


def test_allotment_requires_published_available_nav_and_reconciled_units():
    allotted = _installment(
        state=MFSIPState.ALLOTTED, payment_initiated_at=START,
        funds_available_at=START + timedelta(minutes=1), nav_date=date(2026, 11, 23),
        nav_per_unit=Decimal("10"), nav_published_at=START + timedelta(hours=1),
        nav_available_at=START + timedelta(hours=1, minutes=1),
        allotted_at=START + timedelta(hours=1, minutes=2), units_allotted=Decimal("500"),
    )
    assert allotted.units_allotted * allotted.nav_per_unit == allotted.amount_inr
    with pytest.raises(MFSIPError, match="units x NAV"):
        _installment(
            state=MFSIPState.ALLOTTED, payment_initiated_at=START,
            funds_available_at=START + timedelta(minutes=1), nav_date=date(2026, 11, 23),
            nav_per_unit=Decimal("10"), nav_published_at=START + timedelta(hours=1),
            nav_available_at=START + timedelta(hours=1, minutes=1),
            allotted_at=START + timedelta(hours=1, minutes=2), units_allotted=Decimal("499"),
        )


def test_sip_accepts_nominal_inr_nav_and_fractional_units_at_model_boundary():
    scheduled = _installment(amount_inr=INRNotional(Decimal("5000")))
    assert scheduled.amount_inr == Decimal("5000")
    allotted = _installment(
        amount_inr=INRNotional(Decimal("5000")), state=MFSIPState.ALLOTTED,
        payment_initiated_at=START, funds_available_at=START + timedelta(minutes=1),
        nav_date=date(2026, 11, 23), nav_per_unit=NavPerUnit(Decimal("10")),
        nav_published_at=START + timedelta(hours=1),
        nav_available_at=START + timedelta(hours=1, minutes=1),
        allotted_at=START + timedelta(hours=1, minutes=2),
        units_allotted=MutualFundUnits(Decimal("500")),
    )
    assert allotted.nav_per_unit == Decimal("10")
    assert allotted.units_allotted == Decimal("500")


def test_missed_installment_is_terminal_and_identity_cannot_change():
    missed = _installment(
        state=MFSIPState.MISSED, missed_at=START, miss_reason="insufficient mandate funds",
    )
    retry = _installment(state=MFSIPState.PAYMENT_INITIATED, payment_initiated_at=START)
    with pytest.raises(MFSIPError, match="illegal SIP transition MISSED"):
        validate_installment_transition(missed, retry)
    scheduled = _installment()
    altered = _installment(amount_inr=Decimal("6000"),
                           state=MFSIPState.PAYMENT_INITIATED, payment_initiated_at=START)
    with pytest.raises(MFSIPError, match="identity and schedule are immutable"):
        validate_installment_transition(scheduled, altered)


def test_series_advances_installments_independently_and_missed_one_stays_missed():
    october = _installment(
        installment_id="sip-plan-1-2026-10", scheduled_for=date(2026, 10, 23),
        state=MFSIPState.MISSED, missed_at=START - timedelta(days=31),
        miss_reason="mandate debit failed",
    )
    november = _installment()
    series = MFSIPSeries("sip-plan-1", (october, november))
    initiated = _installment(
        state=MFSIPState.PAYMENT_INITIATED, payment_initiated_at=START,
    )
    progressed = series.advance(initiated)
    assert progressed.installments[0].state is MFSIPState.MISSED
    assert progressed.installments[1].state is MFSIPState.PAYMENT_INITIATED
    retry = _installment(
        installment_id="sip-plan-1-2026-10", scheduled_for=date(2026, 10, 23),
        state=MFSIPState.PAYMENT_INITIATED,
        payment_initiated_at=START - timedelta(days=31),
    )
    with pytest.raises(MFSIPError, match="illegal SIP transition MISSED"):
        series.advance(retry)


@pytest.mark.parametrize("changes", [
    {"state": MFSIPState.SCHEDULED, "payment_initiated_at": START},
    {"state": MFSIPState.MISSED},
    {"state": MFSIPState.PAYMENT_INITIATED},
    {"state": MFSIPState.FUNDS_AVAILABLE, "payment_initiated_at": START,
     "funds_available_at": START - timedelta(seconds=1)},
])
def test_invalid_installment_evidence_fails_closed(changes):
    with pytest.raises(MFSIPError):
        _installment(**changes)
