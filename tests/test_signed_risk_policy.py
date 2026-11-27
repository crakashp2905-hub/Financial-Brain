import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.risk.signed_policy import (
    SignedRiskPolicy, SignedRiskPolicyError, validate_policy,
)


NOW = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)


def signed_policy():
    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    policy = SignedRiskPolicy(
        "owner-risk", "v1", "owner-1", NOW - timedelta(days=1), NOW + timedelta(days=30),
        1_000_000, 5_000, 0.02, 0.05, 0.10, 10, 25, "")
    signature = base64.b64encode(private.sign(policy.canonical_bytes())).decode()
    return replace(policy, signature_b64=signature), public_b64


def test_owner_signature_verifies_policy_capital_risk_drawdown_exposure_and_slippage():
    policy, public_key = signed_policy()
    validate_policy(policy, trusted_owner_public_key_b64=public_key, at=NOW)


def test_modified_or_untrusted_policy_is_rejected():
    policy, public_key = signed_policy()
    changed = replace(policy, risk_per_trade_inr=10_000)
    with pytest.raises(SignedRiskPolicyError, match="signature"):
        validate_policy(changed, trusted_owner_public_key_b64=public_key, at=NOW)
    _, other_key = signed_policy()
    with pytest.raises(SignedRiskPolicyError, match="signature"):
        validate_policy(policy, trusted_owner_public_key_b64=other_key, at=NOW)


def test_expired_and_internally_invalid_policy_fails_closed():
    policy, public_key = signed_policy()
    with pytest.raises(SignedRiskPolicyError, match="effective"):
        validate_policy(policy, trusted_owner_public_key_b64=public_key,
                        at=policy.expires_at)
    impossible = replace(policy, risk_per_trade_inr=2_000_000)
    with pytest.raises(SignedRiskPolicyError, match="hard-bound"):
        validate_policy(impossible, trusted_owner_public_key_b64=public_key, at=NOW)
