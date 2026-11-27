"""Owner-signed hard risk limits; verified policy bytes are immutable and auditable."""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class SignedRiskPolicyError(ValueError):
    """Risk policy is invalid or its owner signature cannot be verified."""


@dataclass(frozen=True, slots=True)
class SignedRiskPolicy:
    policy_id: str
    version: str
    owner_id: str
    effective_at: datetime
    expires_at: datetime
    initial_capital_inr: float
    risk_per_trade_inr: float
    daily_max_drawdown_fraction: float
    weekly_max_drawdown_fraction: float
    monthly_max_drawdown_fraction: float
    max_contract_exposure: int
    max_slippage_bps: float
    signature_b64: str

    def unsigned_body(self) -> dict:
        body = asdict(self)
        body.pop("signature_b64")
        body["effective_at"] = self.effective_at.isoformat()
        body["expires_at"] = self.expires_at.isoformat()
        return {"schema": "fb.signed_risk_policy.v1", **body}

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.unsigned_body(), sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")


def validate_policy(policy: SignedRiskPolicy, *, trusted_owner_public_key_b64: str,
                    at: datetime) -> None:
    """Verify hard caps, validity interval and detached Ed25519 owner signature."""
    if not policy.policy_id.strip() or not policy.version.strip() or not policy.owner_id.strip():
        raise SignedRiskPolicyError("policy identity and owner are required")
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (policy.effective_at, policy.expires_at, at)):
        raise SignedRiskPolicyError("policy validity timestamps must be timezone-aware")
    if policy.effective_at >= policy.expires_at or not policy.effective_at <= at < policy.expires_at:
        raise SignedRiskPolicyError("policy is not currently effective")
    amounts = (policy.initial_capital_inr, policy.risk_per_trade_inr,
               policy.daily_max_drawdown_fraction, policy.weekly_max_drawdown_fraction,
               policy.monthly_max_drawdown_fraction, policy.max_slippage_bps)
    if not all(isfinite(value) for value in amounts):
        raise SignedRiskPolicyError("risk limits must be finite")
    if (policy.initial_capital_inr <= 0 or policy.risk_per_trade_inr <= 0
            or policy.risk_per_trade_inr > policy.initial_capital_inr
            or policy.max_contract_exposure <= 0 or policy.max_slippage_bps <= 0
            or any(not 0 < value < 1 for value in amounts[2:5])):
        raise SignedRiskPolicyError("capital and limits are outside valid hard-bound ranges")
    try:
        public_key = base64.b64decode(trusted_owner_public_key_b64, validate=True)
        signature = base64.b64decode(policy.signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, policy.canonical_bytes())
    except (ValueError, InvalidSignature) as exc:
        raise SignedRiskPolicyError("trusted owner signature is invalid") from exc
