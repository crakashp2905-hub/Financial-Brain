"""Versioned bridge from normalized candidates to legacy and kernel paper intents (FE-0055)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from financial_brain.kernel.registry import StrategySpec


class StrategyAdapterError(ValueError):
    """Signal or strategy metadata cannot safely produce an order intent."""


@dataclass(frozen=True)
class CandidateSignal:
    strategy_id: str
    strategy_version: str
    symbol: str
    side: str
    quantity: int
    decision_id: str
    decision_time_utc: str
    reference_price: Decimal
    signal_payload: dict[str, Any]
    quantity_unit: str = "SHARES"


@dataclass(frozen=True)
class CanonicalIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    strategy_sha256: str
    symbol: str
    side: str
    quantity: int
    quantity_unit: str
    decision_id: str
    decision_time_utc: str
    reference_price: Decimal
    payload_sha256: str
    adapter_version: str = "fb-paper-adapter.v1"

    def as_dict(self) -> dict[str, Any]:
        return {"intent_id": self.intent_id, "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version, "strategy_sha256": self.strategy_sha256,
                "symbol": self.symbol, "side": self.side, "quantity": self.quantity,
                "quantity_unit": self.quantity_unit,
                "decision_id": self.decision_id, "decision_time_utc": self.decision_time_utc,
                "reference_price": str(self.reference_price), "payload_sha256": self.payload_sha256,
                "adapter_version": self.adapter_version}

    def kernel_oms(self, *, account: str, reserve: Decimal) -> dict[str, Any]:
        """Map to the current kernel OMS call shape; does not submit or route an order."""
        if not account.strip() or reserve < 0:
            raise StrategyAdapterError("OMS account and non-negative reserve required")
        return {"client_id": self.intent_id, "account": account, "side": self.side,
                "quantity": self.quantity, "quantity_unit": self.quantity_unit,
                "limit": self.reference_price, "reserve": reserve}

    def legacy_paper(self) -> dict[str, Any]:
        """Map into legacy paper-friendly fields without changing its execution behavior."""
        return {"id": self.intent_id, "strategy": f"{self.strategy_id}@{self.strategy_version}",
                "symbol": self.symbol, "side": self.side, "qty": self.quantity,
                "quantity_unit": self.quantity_unit,
                "decision_id": self.decision_id, "decision_time_utc": self.decision_time_utc,
                "reference_price": self.reference_price}


def adapt_candidate(signal: CandidateSignal, spec: StrategySpec) -> CanonicalIntent:
    if signal.strategy_id != spec.strategy_id or signal.strategy_version != spec.version:
        raise StrategyAdapterError("candidate strategy identity does not match frozen StrategySpec")
    if (signal.side not in {"BUY", "SELL"} or type(signal.quantity) is not int or signal.quantity < 1
            or signal.reference_price <= 0):
        raise StrategyAdapterError("candidate side, quantity or price is invalid")
    if signal.quantity_unit not in {"SHARES", "ETF_UNITS", "OPTION_CONTRACTS", "FUTURES_CONTRACTS"}:
        raise StrategyAdapterError("candidate quantity unit is unsupported")
    if not signal.symbol.strip() or not signal.decision_id.strip() or not signal.decision_time_utc.strip():
        raise StrategyAdapterError("candidate symbol and decision identity are required")
    try:
        spec.validate_forward()
    except ValueError as exc:
        raise StrategyAdapterError(f"strategy spec is not forward-valid: {exc}") from exc
    payload = json.dumps(signal.signal_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    identity = {"strategy": spec.hash(), "symbol": signal.symbol, "side": signal.side,
                "quantity": signal.quantity, "decision_id": signal.decision_id,
                "quantity_unit": signal.quantity_unit,
                "decision_time_utc": signal.decision_time_utc, "payload_sha256": payload_sha}
    intent_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CanonicalIntent(intent_id, spec.strategy_id, spec.version, spec.hash(), signal.symbol,
                           signal.side, signal.quantity, signal.quantity_unit, signal.decision_id,
                           signal.decision_time_utc,
                           signal.reference_price, payload_sha)
