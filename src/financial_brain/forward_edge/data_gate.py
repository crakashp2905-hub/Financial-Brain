"""Deterministic market-data eligibility gate for forward paper decisions (FE-0042)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.data_permissions import DataPermission, DataUse


class GateReason(StrEnum):
    QUOTE_MISSING = "QUOTE_MISSING"
    QUOTE_STALE = "QUOTE_STALE"
    QUOTE_CLOCK_INVALID = "QUOTE_CLOCK_INVALID"
    BID_ASK_INVALID = "BID_ASK_INVALID"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    ZERO_VOLUME = "ZERO_VOLUME"
    CIRCUIT_LIMIT = "CIRCUIT_LIMIT"
    FREEZE_LIMIT = "FREEZE_LIMIT"
    AUCTION_DATA_MISSING = "AUCTION_DATA_MISSING"
    NOT_CONTINUOUS_SESSION = "NOT_CONTINUOUS_SESSION"
    INVALID_REQUEST = "INVALID_REQUEST"
    RIGHTS_UNKNOWN = "RIGHTS_UNKNOWN"
    RIGHTS_DENIED = "RIGHTS_DENIED"


@dataclass(frozen=True)
class DataGateResult:
    decision: str
    reasons: tuple[GateReason, ...]

    @property
    def eligible(self) -> bool:
        return self.decision == "TRADE"


def evaluate_market_data(*, decision_time: datetime, quote_time: datetime | None,
                         bid: Decimal | None, ask: Decimal | None,
                         max_age_ms: int, max_spread_bps: Decimal,
                         volume: int | None, is_circuit_limited: bool = False,
                         quantity: int | None = None, freeze_limit: int | None = None,
                         session_phase: str = "CONTINUOUS", auction_required: bool = False,
                         auction_data_present: bool = True, source_id: str | None = None,
                         entitlement: DataPermission | None = None,
                         data_use: DataUse = DataUse.PAPER_SIMULATION) -> DataGateResult:
    """Evaluate known inputs. Missing/invalid inputs abstain with stable reason codes."""
    reasons: set[GateReason] = set()
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        reasons.add(GateReason.QUOTE_CLOCK_INVALID)
    if max_age_ms < 0 or max_spread_bps < 0 or (volume is not None and volume < 0):
        reasons.add(GateReason.INVALID_REQUEST)
    if entitlement is None or source_id is None:
        reasons.add(GateReason.RIGHTS_UNKNOWN)
    elif (entitlement.status.value == "DENIED" and entitlement.source_id == source_id
          and entitlement.use is data_use):
        reasons.add(GateReason.RIGHTS_DENIED)
    elif not entitlement.allows(source_id=source_id, use=data_use,
                                on=decision_time.astimezone(ZoneInfo("Asia/Kolkata")).date()):
        reasons.add(GateReason.RIGHTS_UNKNOWN)
    if quote_time is None or bid is None or ask is None:
        reasons.add(GateReason.QUOTE_MISSING)
    elif quote_time.tzinfo is None or quote_time.utcoffset() is None:
        reasons.add(GateReason.QUOTE_CLOCK_INVALID)
    elif decision_time.tzinfo is not None and decision_time.utcoffset() is not None:
        age_ms = (decision_time - quote_time).total_seconds() * 1000
        if age_ms < 0:
            reasons.add(GateReason.QUOTE_CLOCK_INVALID)
        elif age_ms > max_age_ms:
            reasons.add(GateReason.QUOTE_STALE)
    if bid is not None and ask is not None:
        if bid <= 0 or ask < bid:
            reasons.add(GateReason.BID_ASK_INVALID)
        elif bid > 0 and ask > 0 and max_spread_bps >= 0:
            mid = (bid + ask) / Decimal(2)
            if mid > 0 and (ask - bid) / mid * Decimal(10000) > max_spread_bps:
                reasons.add(GateReason.SPREAD_TOO_WIDE)
    if volume is None:
        reasons.add(GateReason.ZERO_VOLUME)
    elif volume == 0:
        reasons.add(GateReason.ZERO_VOLUME)
    if is_circuit_limited:
        reasons.add(GateReason.CIRCUIT_LIMIT)
    if quantity is not None and (quantity < 1 or freeze_limit is None or freeze_limit < 1 or quantity > freeze_limit):
        reasons.add(GateReason.FREEZE_LIMIT)
    if session_phase != "CONTINUOUS":
        reasons.add(GateReason.NOT_CONTINUOUS_SESSION)
    if auction_required and not auction_data_present:
        reasons.add(GateReason.AUCTION_DATA_MISSING)
    ordered = tuple(sorted(reasons, key=lambda r: r.value))
    return DataGateResult("NO_TRADE" if ordered else "TRADE", ordered)
