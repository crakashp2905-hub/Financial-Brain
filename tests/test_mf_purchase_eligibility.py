from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from financial_brain.kernel.mf_purchase_eligibility import (
    MFPurchaseEligibilityError, resolve_purchase_eligible_nav,
)
from financial_brain.kernel.mf_purchase_payment import (
    MFPurchasePaymentMilestone, MFPurchasePaymentTimeline,
    PaymentEvidenceBasis, PaymentMilestoneKind,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain


UTC = timezone.utc
REQUESTED = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)


def _order(**changes):
    values = {
        "order_id": "mf-purchase-1", "account_id": "paper-1",
        "folio_id": "synthetic-1", "scheme_code": "123456", "plan": "DIRECT",
        "option": "GROWTH", "transaction_type": MFOrderKind.PURCHASE,
        "requested_at": REQUESTED, "amount_inr": "1000",
    }
    return MFOrder(**(values | changes))


def _acceptance(order, *, basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION):
    accepted_at = REQUESTED + timedelta(minutes=1)
    values = {
        "order_id": order.order_id, "state": MFOrderState.ACCEPTED,
        "effective_at": accepted_at, "received_at": accepted_at + timedelta(seconds=5),
        "basis": basis,
    }
    if basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        values.update(source_id="RTA", source_ref="receipt-123", source_sha256="a" * 64)
    return MFOrderAcknowledgement(**values)


def _payment(order, *, available_at=None, basis=PaymentEvidenceBasis.SIMULATED_ASSUMPTION):
    credit_at = datetime(2026, 11, 23, 10, 5, tzinfo=UTC)
    available_at = available_at or datetime(2026, 11, 23, 10, 15, tzinfo=UTC)
    def event(kind, effective_at):
        values = {
            "kind": kind, "effective_at": effective_at,
            "received_at": effective_at + timedelta(seconds=5), "basis": basis,
        }
        if basis is PaymentEvidenceBasis.OBSERVED_EVIDENCE:
            values.update(source_id="BANK", source_ref=kind.value,
                          source_sha256=("b" if kind is PaymentMilestoneKind.CREDITED_TO_SCHEME
                                         else "c") * 64)
        return MFPurchasePaymentMilestone(**values)
    return MFPurchasePaymentTimeline(order.order_id, (
        event(PaymentMilestoneKind.INITIATED, REQUESTED + timedelta(seconds=10)),
        event(PaymentMilestoneKind.CREDITED_TO_SCHEME, credit_at),
        event(PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION, available_at),
    ))


def _calendar(*, recorded_at=None):
    recorded_at = recorded_at or (REQUESTED - timedelta(days=1))
    business = BusinessCalendar(
        calendar_id="123456", domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 11, 23), source_as_of=date(2026, 11, 25),
        open_days=frozenset(date(2026, 11, day) for day in (23, 24, 25)),
        source_ref="scheme-calendar", source_sha256="d" * 64,
        recorded_at=recorded_at,
    )
    rule = SchemeTransactionRule(
        scheme_id="123456", transaction_kind=TransactionKind.PURCHASE,
        effective_from=date(2026, 11, 1), effective_to=None,
        cutoff_local=time(15), before_cutoff_lag=0, after_cutoff_lag=1,
        closed_day_lag=1, published_at=REQUESTED - timedelta(days=1),
        source_ref="scheme-rule", source_sha256="e" * 64,
    )
    return SchemeTransactionCalendar({"123456": business}, (rule,))


def test_late_fund_utilization_derives_next_nav_day_without_date_override():
    order = _order()
    result = resolve_purchase_eligible_nav(
        order, _acceptance(order), _payment(order), _calendar(),
        decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC),
    )
    # 15:15 IST funds availability is after the sourced 15:00 cutoff.
    assert result.transaction_at == datetime(2026, 11, 23, 10, 15, tzinfo=UTC)
    assert result.eligible_nav_date == date(2026, 11, 24)
    assert result.official_acceptance is False
    assert len(result.event_sha256) == 64


def test_requires_exact_accepted_order_and_all_funds_realization_milestones():
    order = _order()
    payment = MFPurchasePaymentTimeline(order.order_id, ())
    with pytest.raises(MFPurchaseEligibilityError, match="both required"):
        resolve_purchase_eligible_nav(
            order, _acceptance(order), payment, _calendar(),
            decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC),
        )
    wrong = _acceptance(_order(order_id="another-order"))
    with pytest.raises(MFPurchaseEligibilityError, match="exact order"):
        resolve_purchase_eligible_nav(
            order, wrong, _payment(order), _calendar(),
            decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC),
        )


def test_future_or_not_yet_recorded_calendar_facts_fail_closed():
    order = _order()
    with pytest.raises(MFPurchaseEligibilityError, match="not yet known"):
        resolve_purchase_eligible_nav(
            order, _acceptance(order), _payment(order), _calendar(),
            decision_at=datetime(2026, 11, 23, 10, 10, tzinfo=UTC),
        )
    late_calendar = _calendar(recorded_at=datetime(2026, 11, 23, 10, 16, tzinfo=UTC))
    with pytest.raises(MFPurchaseEligibilityError, match="calendar was not known"):
        resolve_purchase_eligible_nav(
            order, _acceptance(order), _payment(order), late_calendar,
            decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC),
        )


def test_observed_acceptance_and_payment_require_effective_paper_permissions():
    order = _order()
    observed = _acceptance(order, basis=MFOrderEvidenceBasis.OBSERVED_EVIDENCE)
    payments = _payment(order, basis=PaymentEvidenceBasis.OBSERVED_EVIDENCE)
    with pytest.raises(MFPurchaseEligibilityError, match="lacks PAPER_SIMULATION"):
        resolve_purchase_eligible_nav(
            order, observed, payments, _calendar(),
            decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC),
        )
    permissions = tuple(DataPermission(
        source_id=source, use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 23),
        effective_through=None, evidence_ref=f"{source}-review", reviewer="owner",
    ) for source in ("RTA", "BANK"))
    result = resolve_purchase_eligible_nav(
        order, observed, payments, _calendar(),
        decision_at=datetime(2026, 11, 23, 12, tzinfo=UTC), permissions=permissions,
    )
    assert result.official_acceptance is True
    assert result.acceptance_receipt_sha256 == "a" * 64
