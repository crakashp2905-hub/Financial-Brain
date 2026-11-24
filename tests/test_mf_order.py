from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderError, MFOrderEvidenceBasis,
    MFOrderKind, MFOrderState, platform_valid_through,
)
from financial_brain.kernel.fund_transactions import SchemeTransactionCalendar
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain


def _order(**changes):
    values = {
        "order_id": "paper-order-1", "account_id": "paper-account-1",
        "folio_id": "SYNTHETIC-FOLIO-1", "scheme_code": "123456",
        "plan": "DIRECT", "option": "GROWTH",
        "transaction_type": MFOrderKind.PURCHASE,
        "requested_at": datetime(2026, 11, 23, 5, tzinfo=timezone.utc),
        "amount_inr": Decimal("10000"),
    }
    return MFOrder(**(values | changes))


def test_order_is_immutable_and_hashes_complete_request():
    first = _order()
    assert len(first.request_sha256) == 64
    assert first.request_sha256 == _order().request_sha256
    assert first.request_sha256 != _order(amount_inr=Decimal("10001")).request_sha256
    with pytest.raises(FrozenInstanceError):
        first.amount_inr = Decimal("1")


@pytest.mark.parametrize("changes", [
    {"amount_inr": None},
    {"amount_inr": Decimal("100"), "units": Decimal("2")},
    {"amount_inr": Decimal("0")},
    {"requested_at": datetime(2026, 11, 23, 5)},
    {"folio_id": "  "},
])
def test_invalid_order_fails_closed(changes):
    with pytest.raises(MFOrderError):
        _order(**changes)


def test_simulated_acceptance_is_not_misrepresented_as_official():
    now = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    ack = MFOrderAcknowledgement(
        order_id="paper-order-1", state=MFOrderState.ACCEPTED,
        effective_at=now, received_at=now + timedelta(seconds=1),
        basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
    )
    assert not ack.is_official_acceptance


def test_observed_acceptance_requires_source_provenance_and_separate_clocks():
    now = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    ack = MFOrderAcknowledgement(
        order_id="paper-order-1", state=MFOrderState.ACCEPTED,
        effective_at=now, received_at=now + timedelta(seconds=1),
        basis=MFOrderEvidenceBasis.OBSERVED_EVIDENCE,
        source_id="PLATFORM", source_ref="receipt-1", source_sha256="a" * 64,
    )
    assert ack.is_official_acceptance
    with pytest.raises(MFOrderError, match="observed response requires"):
        MFOrderAcknowledgement(
            order_id="paper-order-1", state=MFOrderState.ACCEPTED,
            effective_at=now, received_at=now,
            basis=MFOrderEvidenceBasis.OBSERVED_EVIDENCE,
        )
    with pytest.raises(MFOrderError, match="observed before"):
        MFOrderAcknowledgement(
            order_id="paper-order-1", state=MFOrderState.ACKNOWLEDGED,
            effective_at=now + timedelta(seconds=1), received_at=now,
            basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
        )


def test_rejection_requires_a_reason():
    now = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    with pytest.raises(MFOrderError, match="rejection requires"):
        MFOrderAcknowledgement(
            order_id="paper-order-1", state=MFOrderState.REJECTED,
            effective_at=now, received_at=now,
            basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
        )


def test_platform_validity_uses_sourced_scheme_business_days():
    calendar = BusinessCalendar(
        calendar_id="123456", domain=CalendarDomain.SCHEME,
        coverage_start=datetime(2026, 11, 23).date(),
        source_as_of=datetime(2026, 11, 27).date(),
        open_days=frozenset(datetime(2026, 11, day).date() for day in (23, 25, 26, 27)),
        source_ref="calendar-snapshot", source_sha256="b" * 64,
        recorded_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
    )
    scheme_calendar = SchemeTransactionCalendar({"123456": calendar}, ())
    order = _order()
    assert platform_valid_through(order, valid_business_days=2,
                                  calendar=scheme_calendar).isoformat() == "2026-11-25"
    with pytest.raises(MFOrderError, match="positive integer"):
        platform_valid_through(order, valid_business_days=0, calendar=scheme_calendar)
