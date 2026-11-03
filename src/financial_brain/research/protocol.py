"""Canonical, hashable protocol definition for paper research cohorts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ProtocolSpec:
    strategy: str
    strategy_version: str
    schedule: tuple[str, ...]
    benchmark: str
    cost_model: str
    risk_limits: str
    capital_inr: str
    entry_rule: str
    mark_rule: str

    def canonical(self) -> bytes:
        if not self.strategy or not self.strategy_version or not self.schedule:
            raise ValueError("protocol identity and schedule are required")
        for day in self.schedule:
            if not isinstance(day, str) or len(day) != 10:
                raise ValueError("schedule contains an invalid session")
            try:
                parsed = date.fromisoformat(day)
            except ValueError as exc:
                raise ValueError("schedule contains an invalid session") from exc
            if parsed.isoformat() != day:
                raise ValueError("schedule contains an invalid session")
        if not all(isinstance(value, str) and value for value in (
                self.benchmark, self.cost_model, self.risk_limits, self.capital_inr,
                self.entry_rule, self.mark_rule)):
            raise ValueError("protocol fields must be non-empty")
        payload = {
            "strategy": self.strategy, "strategy_version": self.strategy_version,
            "schedule": sorted(set(self.schedule)), "benchmark": self.benchmark,
            "cost_model": self.cost_model, "risk_limits": self.risk_limits,
            "capital_inr": self.capital_inr, "entry_rule": self.entry_rule,
            "mark_rule": self.mark_rule,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()
