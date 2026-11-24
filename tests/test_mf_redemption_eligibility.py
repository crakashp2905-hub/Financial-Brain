from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.forward_edge.data_permissions import DataPermission, DataUse, PermissionStatus
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from financial_brain.kernel.mf_redemption_eligibility import (
    MFRedemptionEligibilityError, resolve_redemption_eligible_nav,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain


UTC = timezone.utc
REQUESTED = datetime(2026, 11, 23, 8, 55, tzinfo=UTC)


def _order(**changes):
    values = {
        "order_id": "mf-redemption-1", "account_id": "paper-1",
        "folio_id": "synthetic-1", "scheme_code": "123456", "plan": "DIRECT",
        "option": "GROWTH", "transaction_type": MFOrderKind.REDEMPTION,
        "requested_at": REQUESTED, "units": "2.5",
    }
    return MFOrder(**(values | changes))


def _acceptance(order, *, basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION, effective_at=None):
    effective_at = effective_at or REQUESTED + timedelta(minutes=1)
    values = {
        "order_id": order.order_id, "state": MFOrderState.ACCEPTED,
        "effective_at": effective_at, "received_at": effective_at + timedelta(seconds=5),
        "basis": basis,
    }
    if basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        values.update(source_id="RTA", source_ref="receipt-123", source_sha256="a" * 64)
    return MFOrderAcknowledgement(**values)


def _calendar(*, recorded_at=None, rule_published_at=None):
    recorded_at = recorded_at or (REQUESTED - timedelta(days=1))
    rule_published_at = rule_published_at or (REQUESTED - timedelta(days=1))
    business = BusinessCalendar(
        calendar_id="123456", domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 11, 23), source_as_of=date(2026, 11, 25),
        open_days=frozenset(date(2026, 11, day) for day in (23, 24, 25)),
        source_ref="scheme-calendar", source_sha256="d" * 64, recorded_at=recorded_at,
    )
    rule = SchemeTransactionRule(
        scheme_id="123456", transaction_kind=TransactionKind.REDEMPTION,
        effective_from=date(2026, 11, 1), effective_to=None,
        cutoff_local=time(14), before_cutoff_lag=0, after_cutoff_lag=1,
        closed_day_lag=1, published_at=rule_published_at,
        source_ref="redemption-rule", source_sha256="e" * 64,
    )
    return SchemeTransactionCalendar({"123456": business}, (rule,))


def test_redemption_nav_date_uses_sourced_redemption_cutoff():
    order = _order()
    result = resolve_redemption_eligible_nav(
        order, _acceptance(order), _calendar(),
        decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
    )
    # 14:56 IST is past the sourced 14:00 redemption cutoff.
    assert result.transaction_at == datetime(2026, 11, 23, 8, 56, tzinfo=UTC)
    assert result.eligible_nav_date == date(2026, 11, 24)
    assert result.official_acceptance is False
    assert len(result.event_sha256) == 64


def test_rejects_wrong_order_type_unaccepted_order_and_future_receipt():
    order = _order()
    with pytest.raises(MFRedemptionEligibilityError, match="REDEMPTION or SWITCH_OUT order"):
        resolve_redemption_eligible_nav(
            _order(transaction_type=MFOrderKind.PURCHASE), _acceptance(order), _calendar(),
            decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )
    with pytest.raises(MFRedemptionEligibilityError, match="exact order"):
        resolve_redemption_eligible_nav(
            order, _acceptance(_order(order_id="other")), _calendar(),
            decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )
    with pytest.raises(MFRedemptionEligibilityError, match="not known"):
        resolve_redemption_eligible_nav(
            order, _acceptance(order, effective_at=datetime(2026, 11, 23, 10, 30, tzinfo=UTC)),
            _calendar(), decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )


def test_calendar_and_rule_must_be_known_at_acceptance():
    order = _order()
    accepted = _acceptance(order)
    with pytest.raises(MFRedemptionEligibilityError, match="calendar was not known"):
        resolve_redemption_eligible_nav(
            order, accepted, _calendar(recorded_at=accepted.effective_at + timedelta(seconds=1)),
            decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )
    with pytest.raises(MFRedemptionEligibilityError, match="no unique published"):
        resolve_redemption_eligible_nav(
            order, accepted, _calendar(rule_published_at=accepted.effective_at + timedelta(seconds=1)),
            decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )


def test_observed_acceptance_requires_effective_paper_permission():
    order = _order()
    accepted = _acceptance(order, basis=MFOrderEvidenceBasis.OBSERVED_EVIDENCE)
    with pytest.raises(MFRedemptionEligibilityError, match="lacks PAPER_SIMULATION"):
        resolve_redemption_eligible_nav(
            order, accepted, _calendar(), decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        )
    permission = DataPermission(
        source_id="RTA", use=DataUse.PAPER_SIMULATION, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 11, 23), effective_through=None,
        evidence_ref="rta-review", reviewer="owner",
    )
    result = resolve_redemption_eligible_nav(
        order, accepted, _calendar(), decision_at=datetime(2026, 11, 23, 10, tzinfo=UTC),
        permissions=(permission,),
    )
    assert result.official_acceptance is True
