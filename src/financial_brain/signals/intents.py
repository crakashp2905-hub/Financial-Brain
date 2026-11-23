"""Typed paper execution intents across exchange and mutual-fund workflows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class IntentType(StrEnum):
    EXCHANGE_LIMIT = "EXCHANGE_LIMIT"
    EXCHANGE_MARKETABLE_SIMULATION = "EXCHANGE_MARKETABLE_SIMULATION"
    MF_SUBSCRIBE = "MF_SUBSCRIBE"
    MF_REDEEM = "MF_REDEEM"
    MF_SWITCH = "MF_SWITCH"
    MF_SIP = "MF_SIP"
    NO_TRADE = "NO_TRADE"


class IntentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    intent_id: str
    signal_id: str
    intent_type: IntentType
    created_at: datetime
    instrument_id: str | None = None
    units_or_quantity: float | None = None
    limit_price: float | None = None
    target_scheme_code: str | None = None
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.intent_id.strip() or not self.signal_id.strip():
            raise IntentError("intent_id and signal_id are required")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise IntentError("created_at must be timezone-aware")
        if not self.paper_only:
            raise IntentError("this contract is paper-only; live order routing is unsupported")
        if self.intent_type == IntentType.NO_TRADE:
            if any(value is not None for value in
                   (self.instrument_id, self.units_or_quantity, self.limit_price,
                    self.target_scheme_code)):
                raise IntentError("NO_TRADE cannot carry order fields")
            return
        if (self.units_or_quantity is None or not isfinite(self.units_or_quantity)
                or self.units_or_quantity <= 0):
            raise IntentError("positive units_or_quantity is required")
        if self.intent_type in {IntentType.EXCHANGE_LIMIT,
                                IntentType.EXCHANGE_MARKETABLE_SIMULATION}:
            if not self.instrument_id:
                raise IntentError("exchange intents require instrument_id")
            if (self.intent_type == IntentType.EXCHANGE_LIMIT and
                    (self.limit_price is None or not isfinite(self.limit_price)
                     or self.limit_price <= 0)):
                raise IntentError("EXCHANGE_LIMIT requires a positive limit_price")
            if self.intent_type == IntentType.EXCHANGE_MARKETABLE_SIMULATION and self.limit_price is not None:
                raise IntentError("marketable simulation uses quote-derived prices, not a submitted limit")
        elif self.intent_type == IntentType.MF_SWITCH:
            if not self.instrument_id or not self.target_scheme_code:
                raise IntentError("MF_SWITCH requires source instrument and target_scheme_code")
        elif self.intent_type in {IntentType.MF_SUBSCRIBE, IntentType.MF_REDEEM,
                                  IntentType.MF_SIP} and not self.instrument_id:
            raise IntentError("mutual-fund intent requires scheme instrument_id")
