"""Quote-backed gap exit pricing that never invents a stop-price fill."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class GapExitError(ValueError):
    """An exit quote is malformed or cannot support a defensible paper fill."""


class PositionDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitQuoteSide(StrEnum):
    BID = "BID"
    ASK = "ASK"


@dataclass(frozen=True, slots=True)
class ExecutableExitQuote:
    bid: Decimal
    ask: Decimal
    available_quantity: Decimal
    source_id: str
    source_hash: str
    event_at: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        for name in ("bid", "ask", "available_quantity"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise GapExitError(f"{name} must be decimal") from exc
            if not value.is_finite() or value <= 0:
                raise GapExitError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        if self.ask < self.bid:
            raise GapExitError("ask cannot be below bid")
        if not self.source_id.strip():
            raise GapExitError("quote source_id is required")
        if len(self.source_hash) != 64 or any(c not in "0123456789abcdef" for c in self.source_hash):
            raise GapExitError("quote source_hash must be lowercase SHA-256")
        for name in ("event_at", "received_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise GapExitError(f"{name} must be timezone-aware")
        if self.received_at.astimezone(timezone.utc) < self.event_at.astimezone(timezone.utc):
            raise GapExitError("quote cannot be received before its event time")


@dataclass(frozen=True, slots=True)
class GapExitResolution:
    resolved: bool
    quote_side: ExitQuoteSide | None
    executable_price: Decimal | None
    quantity: Decimal
    trigger_price: Decimal
    signed_slippage_bps: Decimal | None
    adverse_slippage_bps: Decimal | None
    source_id: str
    source_hash: str
    reason: str | None = None


def resolve_gap_exit(
    *,
    direction: PositionDirection,
    quote: ExecutableExitQuote | None,
    trigger_price: Decimal,
    quantity: Decimal,
    observed_at: datetime,
    max_quote_age_ms: int,
) -> GapExitResolution:
    """Resolve a long close at bid or short close at ask; return unresolved without quote proof."""
    try:
        trigger = Decimal(trigger_price)
        qty = Decimal(quantity)
    except (InvalidOperation, TypeError) as exc:
        raise GapExitError("trigger price and quantity must be decimal") from exc
    if not trigger.is_finite() or trigger <= 0 or not qty.is_finite() or qty <= 0:
        raise GapExitError("trigger price and quantity must be finite and positive")
    if max_quote_age_ms <= 0 or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise GapExitError("positive quote-age bound and aware observation time are required")
    quote_hash = quote.source_hash if quote is not None else "0" * 64
    quote_source = quote.source_id if quote is not None else "unavailable"

    def unresolved(reason: str) -> GapExitResolution:
        return GapExitResolution(False, None, None, Decimal(0), trigger, None, None,
                                 quote_source, quote_hash, reason)

    if quote is None:
        return unresolved("executable_quote_unavailable")
    age_ms = (observed_at.astimezone(timezone.utc) - quote.received_at.astimezone(timezone.utc)).total_seconds() * 1000
    if age_ms < 0 or age_ms > max_quote_age_ms:
        return unresolved("executable_quote_stale_or_from_future")
    if quote.available_quantity < qty:
        return unresolved("insufficient_executable_quote_quantity")
    side = ExitQuoteSide.BID if direction is PositionDirection.LONG else ExitQuoteSide.ASK
    price = quote.bid if side is ExitQuoteSide.BID else quote.ask
    signed = ((trigger - price) if direction is PositionDirection.LONG else (price - trigger))
    signed_bps = signed / trigger * Decimal("10000")
    adverse_bps = max(Decimal(0), signed_bps)
    return GapExitResolution(True, side, price, qty, trigger, signed_bps, adverse_bps,
                             quote.source_id, quote.source_hash)


__all__ = ["ExecutableExitQuote", "ExitQuoteSide", "GapExitError", "GapExitResolution",
           "PositionDirection", "resolve_gap_exit"]
