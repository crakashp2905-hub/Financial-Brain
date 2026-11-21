"""Effective-dated instrument rules required before canonical paper execution."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class InstrumentRuleError(ValueError):
    """Instrument rule data is missing, malformed or makes an order ineligible."""


SCHEMA = "fb.instrument-rules.v1"


@dataclass(frozen=True)
class EffectiveInstrumentRule:
    effective_from: date
    tick_size: Decimal
    lot_size: int
    freeze_quantity: int
    expiry: date | None
    status: str
    session_phases: frozenset[str]


class InstrumentRuleBook:
    """Immutable JSON-safe snapshot of dated tick, lot, freeze and tradability rules."""

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise InstrumentRuleError(f"instrument rules require schema {SCHEMA}")
        for name in ("catalogue_id", "version", "source_ref"):
            if not isinstance(data.get(name), str) or not data[name].strip():
                raise InstrumentRuleError(f"instrument rule {name} is required")
        raw_instruments = data.get("instruments")
        if not isinstance(raw_instruments, dict) or not raw_instruments:
            raise InstrumentRuleError("instrument rule catalogue is empty")
        self._rules: dict[str, tuple[EffectiveInstrumentRule, ...]] = {}
        for instrument, versions in raw_instruments.items():
            if not isinstance(instrument, str) or not instrument.strip() or not isinstance(versions, list) or not versions:
                raise InstrumentRuleError("instrument IDs need non-empty rule version lists")
            parsed = tuple(self._parse(row) for row in versions)
            dates = [row.effective_from for row in parsed]
            if dates != sorted(set(dates)):
                raise InstrumentRuleError(f"{instrument} rules must have unique ordered effective dates")
            self._rules[instrument] = parsed
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse(row: Any) -> EffectiveInstrumentRule:
        if not isinstance(row, dict):
            raise InstrumentRuleError("each instrument rule version must be an object")
        try:
            effective_from = date.fromisoformat(row["effective_from"])
            tick = Decimal(str(row["tick_size"]))
            lot = row["lot_size"]
            freeze = row["freeze_quantity"]
            expiry = date.fromisoformat(row["expiry"]) if row.get("expiry") is not None else None
            status = str(row["status"]).strip().upper()
            phases_raw = row["session_phases"]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise InstrumentRuleError("instrument rule version has malformed required fields") from exc
        if (not tick.is_finite() or tick <= 0 or type(lot) is not int or lot < 1
                or type(freeze) is not int or freeze < 1):
            raise InstrumentRuleError("tick, lot and freeze rules must be positive")
        if status not in {"ACTIVE", "SUSPENDED", "EXPIRED", "DELISTED"}:
            raise InstrumentRuleError("instrument status is unsupported")
        if not isinstance(phases_raw, list) or not phases_raw:
            raise InstrumentRuleError("instrument rule must declare eligible session phases")
        phases = frozenset(str(phase).strip().upper() for phase in phases_raw)
        if "" in phases or not phases <= {"CONTINUOUS", "PREOPEN", "AUCTION", "SETTLEMENT", "CLOSED"}:
            raise InstrumentRuleError("instrument session phase is unsupported")
        return EffectiveInstrumentRule(effective_from, tick, lot, freeze, expiry, status, phases)

    def resolve(self, instrument: str, day: date) -> EffectiveInstrumentRule:
        versions = self._rules.get(instrument)
        if versions is None:
            raise InstrumentRuleError(f"no instrument rules for {instrument}")
        eligible = [rule for rule in versions if rule.effective_from <= day]
        if not eligible:
            raise InstrumentRuleError(f"no instrument rule effective for {instrument} on {day.isoformat()}")
        return eligible[-1]

    def validate_order(self, *, instrument: str, day: date, session_phase: str,
                       quantity: int, limit_price: Decimal, tick_size: Decimal) -> EffectiveInstrumentRule:
        rule = self.resolve(instrument, day)
        if rule.expiry is not None and day > rule.expiry:
            raise InstrumentRuleError("instrument expired on the requested session date")
        if rule.status != "ACTIVE":
            raise InstrumentRuleError(f"instrument status {rule.status} blocks paper orders")
        phase = session_phase.strip().upper()
        if phase not in rule.session_phases:
            raise InstrumentRuleError(f"instrument is not eligible during {phase or 'UNKNOWN'} session phase")
        if quantity < 1 or quantity % rule.lot_size:
            raise InstrumentRuleError(f"quantity must be a positive multiple of lot size {rule.lot_size}")
        if quantity > rule.freeze_quantity:
            raise InstrumentRuleError(f"quantity exceeds effective freeze limit {rule.freeze_quantity}")
        if tick_size != rule.tick_size:
            raise InstrumentRuleError("submitted tick size differs from effective instrument rule")
        if (limit_price / rule.tick_size) != (limit_price / rule.tick_size).to_integral_value():
            raise InstrumentRuleError("limit price violates effective tick size; no price repair is applied")
        return rule


def fixture_rule_book(instrument: str, *, effective_from: date = date(1900, 1, 1),
                      tick_size: str = "0.05", lot_size: int = 1, freeze_quantity: int = 1000,
                      status: str = "ACTIVE", expiry: date | None = None,
                      session_phases: tuple[str, ...] = ("CONTINUOUS",)) -> dict[str, Any]:
    """Explicit synthetic rule fixture; never suitable as production instrument-master evidence."""
    return {"schema": SCHEMA, "catalogue_id": "synthetic-test-catalogue", "version": "fixture-v1",
            "source_ref": "synthetic test fixture; not exchange rules", "instruments": {
                instrument: [{"effective_from": effective_from.isoformat(), "tick_size": tick_size,
                              "lot_size": lot_size, "freeze_quantity": freeze_quantity,
                              "expiry": expiry.isoformat() if expiry else None, "status": status,
                              "session_phases": list(session_phases)}]}}
