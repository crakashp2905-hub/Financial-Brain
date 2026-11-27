import base64
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.risk.audited_forward_kelly import (
    AuditedForwardReturns,
    AuditedKellyError,
    AuditedKellyPolicy,
    size_from_audited_forward_returns,
)


UTC = timezone.utc
DECISION = datetime(2026, 11, 27, 10, tzinfo=UTC)


def signed_evidence(*, returns=None, **changes):
    key = Ed25519PrivateKey.generate()
    values = dict(
        strategy_id="momentum-v4",
        strategy_version_sha256="a" * 64,
        cohort_id="forward-2026q4",
        cohort_manifest_sha256="b" * 64,
        execution_costs_sha256="c" * 64,
        audit_report_sha256="d" * 64,
        auditor_id="independent-evaluator-1",
        training_through=datetime(2026, 10, 10, tzinfo=UTC),
        first_eligible_session=date(2026, 10, 21),
        forward_start=datetime(2026, 10, 21, tzinfo=UTC),
        forward_end=datetime(2026, 11, 20, tzinfo=UTC),
        audited_at=datetime(2026, 11, 25, tzinfo=UTC),
        net_period_returns=tuple(returns or (0.009 if i % 2 else 0.011 for i in range(100))),
        net_of_all_costs=True,
        includes_zero_return_periods=True,
        signature_b64="",
    )
    values.update(changes)
    evidence = AuditedForwardReturns(**values)
    signature = base64.b64encode(key.sign(evidence.signed_bytes())).decode("ascii")
    evidence = replace(evidence, signature_b64=signature)
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    return evidence, public_key


def policy(**changes):
    values = dict(
        policy_id="kelly-v1",
        frozen_at=datetime(2026, 10, 20, tzinfo=UTC),
        first_eligible_session=date(2026, 10, 21),
        minimum_periods=60,
        fractional_kelly=0.10,
        maximum_position_fraction=0.08,
        one_sided_confidence_z=1.645,
    )
    values.update(changes)
    return AuditedKellyPolicy(**values)


def test_audited_forward_net_returns_use_conservative_bound_and_hard_cap():
    evidence, public = signed_evidence()
    result = size_from_audited_forward_returns(
        evidence, policy(), decision_at=DECISION, trusted_auditor_public_key_b64=public
    )
    assert result.periods == 100
    assert result.conservative_mean_lcb > 0
    assert result.applied_position_fraction == 0.08
    assert result.applied_position_fraction <= result.maximum_position_fraction
    assert result.forward_evidence_sha256 == evidence.digest()


def test_negative_forward_net_edge_gets_no_kelly_sizing():
    evidence, public = signed_evidence(
        returns=tuple(-0.002 if i % 2 else 0.0 for i in range(100))
    )
    result = size_from_audited_forward_returns(
        evidence, policy(), decision_at=DECISION, trusted_auditor_public_key_b64=public
    )
    assert result.conservative_mean_lcb == 0
    assert result.applied_position_fraction == 0


def test_tampered_return_or_wrong_auditor_key_is_rejected():
    evidence, public = signed_evidence()
    altered = replace(evidence, net_period_returns=(0.5,) + evidence.net_period_returns[1:])
    with pytest.raises(AuditedKellyError, match="signature"):
        size_from_audited_forward_returns(
            altered, policy(), decision_at=DECISION, trusted_auditor_public_key_b64=public
        )
    _, wrong_public = signed_evidence()
    with pytest.raises(AuditedKellyError, match="signature"):
        size_from_audited_forward_returns(
            evidence, policy(), decision_at=DECISION,
            trusted_auditor_public_key_b64=wrong_public,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"net_of_all_costs": False},
        {"includes_zero_return_periods": False},
        {"training_through": datetime(2026, 11, 1, tzinfo=UTC)},
        {"audited_at": DECISION + timedelta(seconds=1)},
        {"forward_start": datetime(2026, 10, 1, tzinfo=UTC)},
    ],
)
def test_in_sample_cost_incomplete_or_future_evidence_is_rejected(changes):
    evidence, public = signed_evidence(**changes)
    with pytest.raises(AuditedKellyError):
        size_from_audited_forward_returns(
            evidence, policy(), decision_at=DECISION, trusted_auditor_public_key_b64=public
        )


def test_short_forward_history_and_unsafe_caps_are_rejected():
    evidence, public = signed_evidence(returns=(0.01,) * 20)
    with pytest.raises(AuditedKellyError, match="insufficient"):
        size_from_audited_forward_returns(
            evidence, policy(), decision_at=DECISION, trusted_auditor_public_key_b64=public
        )
    with pytest.raises(AuditedKellyError, match="fraction cap"):
        size_from_audited_forward_returns(
            signed_evidence()[0], policy(maximum_position_fraction=0.5),
            decision_at=DECISION, trusted_auditor_public_key_b64=public,
        )
