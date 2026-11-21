from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.mf_purchase_payment import (
    MFPurchasePaymentError, MFPurchasePaymentMilestone,
    MFPurchasePaymentTimeline, PaymentEvidenceBasis, PaymentMilestoneKind,
)


def _milestone(kind, at, **changes):
    values = {
        "kind": kind, "effective_at": at, "received_at": at + timedelta(seconds=1),
        "basis": PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
    }
    return MFPurchasePaymentMilestone(**(values | changes))


def test_purchase_payment_keeps_three_clocks_separate_and_missing_unknown():
    initiated = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    timeline = MFPurchasePaymentTimeline(
        "order-1", (_milestone(PaymentMilestoneKind.INITIATED, initiated),)
    )
    assert timeline.at(PaymentMilestoneKind.INITIATED).effective_at == initiated
    assert timeline.at(PaymentMilestoneKind.CREDITED_TO_SCHEME) is None
    assert timeline.at(PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION) is None
    assert len(timeline.event_sha256) == 64
    with pytest.raises(FrozenInstanceError):
        timeline.order_id = "other"


def test_distinct_milestones_allow_partial_evidence_without_imputation():
    start = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    credit = start + timedelta(hours=2)
    available = credit + timedelta(hours=1)
    timeline = MFPurchasePaymentTimeline("order-2", (
        _milestone(PaymentMilestoneKind.INITIATED, start),
        _milestone(PaymentMilestoneKind.CREDITED_TO_SCHEME, credit),
        _milestone(PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION, available),
    ))
    assert timeline.at(PaymentMilestoneKind.CREDITED_TO_SCHEME).effective_at == credit
    assert timeline.at(PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION).effective_at == available


def test_observed_milestone_requires_independent_provenance():
    at = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    with pytest.raises(MFPurchasePaymentError, match="source provenance"):
        _milestone(PaymentMilestoneKind.CREDITED_TO_SCHEME, at,
                   basis=PaymentEvidenceBasis.OBSERVED_EVIDENCE)
    observed = _milestone(
        PaymentMilestoneKind.CREDITED_TO_SCHEME, at,
        basis=PaymentEvidenceBasis.OBSERVED_EVIDENCE, source_id="BANK",
        source_ref="statement-row-1", source_sha256="c" * 64,
    )
    assert observed.source_id == "BANK"


def test_inconsistent_duplicate_or_inverted_milestones_fail_closed():
    start = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)
    later = start + timedelta(minutes=1)
    with pytest.raises(MFPurchasePaymentError, match="cannot occur after"):
        MFPurchasePaymentTimeline("order-3", (
            _milestone(PaymentMilestoneKind.INITIATED, later),
            _milestone(PaymentMilestoneKind.CREDITED_TO_SCHEME, start),
        ))
    event = _milestone(PaymentMilestoneKind.INITIATED, start)
    with pytest.raises(MFPurchasePaymentError, match="only once"):
        MFPurchasePaymentTimeline("order-4", (event, event))
