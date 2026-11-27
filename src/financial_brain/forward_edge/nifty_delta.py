"""Cross-book Nifty-direction delta exposure cap using dated beta mappings."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from financial_brain.risk.signed_policy import SignedRiskPolicy, validate_policy


class NiftyDeltaError(ValueError):
    """Missing/stale exposure mapping or aggregate direction limit breach."""


@dataclass(frozen=True, slots=True)
class NiftyDeltaPosition:
    position_id: str
    strategy_id: str
    instrument_id: str
    index_family: str
    signed_quantity: int
    contract_multiplier: float
    underlying_price_inr: float
    delta_per_unit: float
    nifty_beta: float
    beta_as_of: datetime
    beta_source_sha256: str


@dataclass(frozen=True, slots=True)
class NiftyDeltaAssessment:
    as_of: datetime
    cap_inr: float
    net_delta_inr: float
    gross_delta_inr: float
    position_deltas: tuple[tuple[str, str, float], ...]
    within_cap: bool


def assess_nifty_delta(
    positions: tuple[NiftyDeltaPosition, ...],
    *,
    as_of: datetime,
    policy: SignedRiskPolicy,
    trusted_owner_public_key_b64: str,
    max_beta_age: timedelta = timedelta(days=45),
) -> NiftyDeltaAssessment:
    """Aggregate signed INR delta across every index/sector/strategy in one snapshot.

    Positions should encode shares/contracts with signed quantity. Options use signed
    per-unit option delta; equities/futures typically use 1.0. Every beta mapping must
    be known at or before ``as_of`` and carry source-hash provenance.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise NiftyDeltaError("as_of must be timezone-aware")
    validate_policy(policy, trusted_owner_public_key_b64=trusted_owner_public_key_b64, at=as_of)
    if max_beta_age < timedelta(0):
        raise NiftyDeltaError("beta freshness must be nonnegative")
    seen: set[str] = set()
    deltas: list[tuple[str, str, float]] = []
    for position in positions:
        if (not position.position_id or position.position_id in seen
                or not position.strategy_id or not position.instrument_id or not position.index_family):
            raise NiftyDeltaError("position and strategy identities must be present and unique")
        seen.add(position.position_id)
        if (position.beta_as_of.tzinfo is None or position.beta_as_of.utcoffset() is None
                or position.beta_as_of > as_of or as_of - position.beta_as_of > max_beta_age):
            raise NiftyDeltaError("Nifty beta mapping is missing, future-dated or stale")
        if not re.fullmatch(r"[0-9a-f]{64}", position.beta_source_sha256):
            raise NiftyDeltaError("Nifty beta source hash is required")
        numeric = (position.contract_multiplier, position.underlying_price_inr,
                   position.delta_per_unit, position.nifty_beta)
        if (position.signed_quantity == 0 or not all(isfinite(value) for value in numeric)
                or position.contract_multiplier <= 0 or position.underlying_price_inr <= 0
                or not -1 <= position.delta_per_unit <= 1):
            raise NiftyDeltaError("position quantities, prices, multipliers or delta are invalid")
        delta_inr = (position.signed_quantity * position.contract_multiplier
                     * position.underlying_price_inr * position.delta_per_unit * position.nifty_beta)
        deltas.append((position.position_id, position.strategy_id, delta_inr))
    net = sum(value for _, _, value in deltas)
    gross = sum(abs(value) for _, _, value in deltas)
    cap = policy.max_nifty_direction_delta_inr
    return NiftyDeltaAssessment(as_of, cap, net, gross, tuple(deltas), abs(net) <= cap)


def require_within_nifty_delta_cap(assessment: NiftyDeltaAssessment) -> None:
    if not assessment.within_cap:
        raise NiftyDeltaError(
            f"aggregate Nifty-direction delta INR {assessment.net_delta_inr:.2f} exceeds "
            f"absolute cap {assessment.cap_inr:.2f}")
