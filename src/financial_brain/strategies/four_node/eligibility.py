"""Point-in-time instrument/data eligibility for Four-Node research (FB-2527)."""
from __future__ import annotations

import re
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AssetKind = Literal["INDEX", "EQUITY", "ETF"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class InstrumentSnapshot:
    """Effective-dated provider identity and session-quality evidence for one instrument."""

    instrument_id: str
    asset_kind: str
    provider_token: int
    source_id: str
    source_sha256: str
    valid_from: datetime
    valid_until: datetime | None
    as_of: datetime
    session_bars_complete: bool
    tradable: bool
    median_turnover_inr: float
    min_turnover_inr: float


@dataclass(frozen=True)
class EligibilityDecision:
    status: str
    signal_eligible: bool
    trade_eligible: bool
    instrument_id: str
    reason_codes: tuple[str, ...]
    evidence_id: str | None


def evaluate_instrument(snapshot: InstrumentSnapshot) -> EligibilityDecision:
    """Fail closed on missing point-in-time identity, session bars, liquidity or tradability."""
    if not isinstance(snapshot, InstrumentSnapshot):
        raise TypeError("typed InstrumentSnapshot evidence is required")
    if snapshot.as_of.tzinfo is None or snapshot.as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if snapshot.valid_from.tzinfo is None or snapshot.valid_from.utcoffset() is None:
        raise ValueError("valid_from must be timezone-aware")
    if snapshot.valid_until is not None and (
            snapshot.valid_until.tzinfo is None or snapshot.valid_until.utcoffset() is None):
        raise ValueError("valid_until must be timezone-aware")
    reasons: list[str] = []
    if not snapshot.instrument_id.strip() or not snapshot.source_id.strip():
        reasons.append("MISSING_INSTRUMENT_OR_SOURCE_ID")
    if not _SHA256.fullmatch(snapshot.source_sha256):
        reasons.append("INVALID_SOURCE_HASH")
    if snapshot.asset_kind not in {"INDEX", "EQUITY", "ETF"}:
        reasons.append("UNSUPPORTED_ASSET_KIND")
    now = snapshot.as_of
    if snapshot.valid_from > now or (snapshot.valid_until is not None and now >= snapshot.valid_until):
        reasons.append("INSTRUMENT_NOT_EFFECTIVE_AT_DECISION")
    if type(snapshot.provider_token) is not int or snapshot.provider_token <= 0:
        reasons.append("MISSING_PROVIDER_INSTRUMENT_ID")
    if not snapshot.session_bars_complete:
        reasons.append("SESSION_DATA_INCOMPLETE")
    if (type(snapshot.median_turnover_inr) not in {float, int}
            or type(snapshot.min_turnover_inr) not in {float, int}
            or not math.isfinite(snapshot.median_turnover_inr)
            or not math.isfinite(snapshot.min_turnover_inr)
            or snapshot.median_turnover_inr < 0 or snapshot.min_turnover_inr <= 0):
        reasons.append("INVALID_LIQUIDITY_EVIDENCE")
    elif snapshot.median_turnover_inr < snapshot.min_turnover_inr:
        reasons.append("BELOW_MINIMUM_LIQUIDITY")
    if snapshot.asset_kind in {"EQUITY", "ETF"} and not snapshot.tradable:
        reasons.append("NOT_TRADABLE_AT_DECISION")
    if reasons:
        return EligibilityDecision("REJECTED", False, False, snapshot.instrument_id,
                                   tuple(reasons), snapshot.source_id or None)
    if snapshot.asset_kind == "INDEX":
        return EligibilityDecision("REFERENCE_ONLY", True, False, snapshot.instrument_id,
                                   ("INDEX_SIGNAL_REFERENCE_NOT_TRADEABLE",), snapshot.source_id)
    return EligibilityDecision("ELIGIBLE", True, True, snapshot.instrument_id,
                               ("POINT_IN_TIME_IDENTITY_DATA_AND_LIQUIDITY_VERIFIED",),
                               snapshot.source_id)
