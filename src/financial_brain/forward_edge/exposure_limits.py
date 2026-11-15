"""Atomic portfolio exposure checks and reservations for concurrent paper intents (FE-0105)."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Mapping


class ExposureLimitError(PermissionError):
    """Exposure limit would be breached or an intent event conflicts."""


@dataclass(frozen=True)
class ExposureLimits:
    max_gross: float
    max_abs_net: float
    max_single_name_gross: float
    max_sector_gross: Mapping[str, float]
    max_abs_factor: Mapping[str, float]
    max_allocated_capital: float | None = None
    max_worst_case_loss: float | None = None

    def __post_init__(self) -> None:
        values = (self.max_gross, self.max_abs_net, self.max_single_name_gross,
                  *self.max_sector_gross.values(), *self.max_abs_factor.values())
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ExposureLimitError("all exposure caps must be finite and positive")
        if not self.max_sector_gross or any(not key.strip() for key in self.max_sector_gross):
            raise ExposureLimitError("sector caps must be explicit")
        if any(not key.strip() for key in self.max_abs_factor):
            raise ExposureLimitError("factor cap names cannot be blank")
        optional_caps = (self.max_allocated_capital, self.max_worst_case_loss)
        if any(value is not None and (not math.isfinite(value) or value <= 0) for value in optional_caps):
            raise ExposureLimitError("optional capital and worst-case loss caps must be finite and positive")


@dataclass(frozen=True)
class ExposureIntent:
    intent_id: str
    instrument_id: str
    sector: str
    side: str
    notional: float
    # Signed dollar factor contributions for the full order notional.
    factor_exposure: Mapping[str, float] = field(default_factory=dict)
    capital_required: float = 0.0
    worst_case_loss: float = 0.0

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.intent_id, self.instrument_id, self.sector)):
            raise ExposureLimitError("intent, instrument and sector IDs are required")
        if self.side not in {"BUY", "SELL"}:
            raise ExposureLimitError("intent side must be BUY or SELL")
        if not math.isfinite(self.notional) or self.notional <= 0:
            raise ExposureLimitError("intent notional must be finite and positive")
        if any(not key.strip() or not math.isfinite(value) for key, value in self.factor_exposure.items()):
            raise ExposureLimitError("factor exposures require names and finite values")
        if any(not math.isfinite(value) or value < 0
               for value in (self.capital_required, self.worst_case_loss)):
            raise ExposureLimitError("capital and worst-case loss requirements must be non-negative")

    @property
    def sign(self) -> float:
        return 1.0 if self.side == "BUY" else -1.0


class ExposureReservationBook:
    """Lock-serialized exposure accounting; fills convert reserved risk into current risk."""

    def __init__(self, limits: ExposureLimits) -> None:
        self.limits = limits
        self._lock = threading.RLock()
        self._current_gross = 0.0
        self._current_net = 0.0
        self._current_capital = 0.0
        self._current_worst_case_loss = 0.0
        self._name_gross: dict[str, float] = {}
        self._sector_gross: dict[str, float] = {}
        self._factor_net: dict[str, float] = {}
        self._pending: dict[str, tuple[ExposureIntent, float]] = {}
        self._seen: dict[str, ExposureIntent] = {}
        self._fills: dict[str, tuple[str, float]] = {}

    def _aggregates(self) -> tuple[float, float, dict[str, float], dict[str, float], dict[str, float], float, float]:
        gross = self._current_gross
        net = self._current_net
        names = dict(self._name_gross)
        sectors = dict(self._sector_gross)
        factors = dict(self._factor_net)
        capital = self._current_capital
        worst_case_loss = self._current_worst_case_loss
        for intent, remaining in self._pending.values():
            gross += remaining
            net += remaining * intent.sign
            names[intent.instrument_id] = names.get(intent.instrument_id, 0.0) + remaining
            sectors[intent.sector] = sectors.get(intent.sector, 0.0) + remaining
            fraction = remaining / intent.notional
            capital += intent.capital_required * fraction
            worst_case_loss += intent.worst_case_loss * fraction
            for factor, full_exposure in intent.factor_exposure.items():
                exposure = full_exposure * fraction
                factors[factor] = factors.get(factor, 0.0) + exposure
        return gross, net, names, sectors, factors, capital, worst_case_loss

    def reserve(self, intent: ExposureIntent) -> bool:
        """Atomically reserve full notional; an identical replay is idempotent."""
        with self._lock:
            if intent.intent_id in self._seen:
                if self._seen[intent.intent_id] != intent:
                    raise ExposureLimitError("intent ID reused with changed exposure content")
                return False
            if intent.sector not in self.limits.max_sector_gross:
                raise ExposureLimitError("sector has no configured exposure cap")
            unknown_factors = set(intent.factor_exposure) - set(self.limits.max_abs_factor)
            if unknown_factors:
                raise ExposureLimitError(f"factors have no configured caps: {sorted(unknown_factors)}")
            gross, net, names, sectors, factors, capital, worst_case_loss = self._aggregates()
            gross += intent.notional
            net += intent.sign * intent.notional
            names[intent.instrument_id] = names.get(intent.instrument_id, 0.0) + intent.notional
            sectors[intent.sector] = sectors.get(intent.sector, 0.0) + intent.notional
            for factor, exposure in intent.factor_exposure.items():
                factors[factor] = factors.get(factor, 0.0) + exposure
            capital += intent.capital_required
            worst_case_loss += intent.worst_case_loss
            if gross > self.limits.max_gross:
                raise ExposureLimitError("portfolio gross exposure limit exceeded")
            if abs(net) > self.limits.max_abs_net:
                raise ExposureLimitError("portfolio absolute net exposure limit exceeded")
            if names[intent.instrument_id] > self.limits.max_single_name_gross:
                raise ExposureLimitError("single-name gross exposure limit exceeded")
            if sectors[intent.sector] > self.limits.max_sector_gross[intent.sector]:
                raise ExposureLimitError("sector gross exposure limit exceeded")
            for factor, value in factors.items():
                cap = self.limits.max_abs_factor.get(factor)
                if cap is not None and abs(value) > cap:
                    raise ExposureLimitError(f"factor exposure limit exceeded: {factor}")
            if (self.limits.max_allocated_capital is not None
                    and capital > self.limits.max_allocated_capital):
                raise ExposureLimitError("cash/margin capital would be double allocated")
            if (self.limits.max_worst_case_loss is not None
                    and worst_case_loss > self.limits.max_worst_case_loss):
                raise ExposureLimitError("portfolio worst-case loss limit exceeded")
            self._pending[intent.intent_id] = (intent, intent.notional)
            self._seen[intent.intent_id] = intent
            return True

    def record_fill(self, intent_id: str, fill_id: str, filled_notional: float) -> bool:
        """Convert part of a reservation to actual exposure without changing total reserved risk."""
        if not fill_id.strip():
            raise ExposureLimitError("fill_id is required")
        if not math.isfinite(filled_notional) or filled_notional <= 0:
            raise ExposureLimitError("fill notional must be finite and positive")
        with self._lock:
            if fill_id in self._fills:
                if self._fills[fill_id] != (intent_id, filled_notional):
                    raise ExposureLimitError("fill ID reused with changed intent or notional")
                return False
            pending = self._pending.get(intent_id)
            if pending is None:
                raise ExposureLimitError("unknown or fully resolved exposure reservation")
            intent, remaining = pending
            if filled_notional > remaining + 1e-8:
                raise ExposureLimitError("fill exceeds remaining reserved notional")
            self._current_gross += filled_notional
            self._current_net += filled_notional * intent.sign
            fraction = filled_notional / intent.notional
            self._current_capital += intent.capital_required * fraction
            self._current_worst_case_loss += intent.worst_case_loss * fraction
            self._name_gross[intent.instrument_id] = self._name_gross.get(intent.instrument_id, 0.0) + filled_notional
            self._sector_gross[intent.sector] = self._sector_gross.get(intent.sector, 0.0) + filled_notional
            for factor, full_exposure in intent.factor_exposure.items():
                contribution = full_exposure * filled_notional / intent.notional
                self._factor_net[factor] = self._factor_net.get(factor, 0.0) + contribution
            remainder = remaining - filled_notional
            if remainder <= 1e-8:
                del self._pending[intent_id]
            else:
                self._pending[intent_id] = (intent, remainder)
            self._fills[fill_id] = (intent_id, filled_notional)
            return True

    def cancel_remaining(self, intent_id: str) -> float:
        """Release unfilled reserved exposure and return the released notional."""
        with self._lock:
            pending = self._pending.pop(intent_id, None)
            if pending is None:
                raise ExposureLimitError("unknown or fully resolved exposure reservation")
            return pending[1]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            gross, net, names, sectors, factors, capital, worst_case_loss = self._aggregates()
            return {"gross": gross, "net": net, "name_gross": dict(sorted(names.items())),
                    "sector_gross": dict(sorted(sectors.items())),
                    "factor_net": dict(sorted(factors.items())),
                    "allocated_capital": capital, "worst_case_loss": worst_case_loss,
                    "pending_intents": sorted(self._pending)}
