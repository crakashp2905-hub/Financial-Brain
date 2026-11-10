"""Hashable experiment charter (FE-0004).

A charter freezes code hash, parameters, capital, dates, universe and benchmarks. Its canonical JSON hash is the
identity of the experiment. Any change produces a new version that links to the previous hash and starts a new
prospective clock: a revision cannot silently inherit the old observation window.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation

_SHA = re.compile(r"^[0-9a-f]{64}$")


class CharterError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentCharter:
    charter_id: str
    version: int
    strategy_id: str
    code_sha256: str
    capital_inr: str
    start_on: str
    end_on: str
    universe: tuple[str, ...]
    benchmarks: tuple[str, ...]
    horizon_sessions: int
    parameters: tuple[tuple[str, str], ...] = field(default=())
    prior_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.charter_id.strip() or not self.strategy_id.strip():
            raise CharterError("charter and strategy ids are required")
        if self.version < 1:
            raise CharterError("version starts at 1")
        if (self.version == 1) != (self.prior_sha256 is None):
            raise CharterError("version 1 has no prior; later versions must name the prior charter hash")
        if self.prior_sha256 is not None and not _SHA.match(self.prior_sha256):
            raise CharterError("prior hash must be sha256 hex")
        if not _SHA.match(self.code_sha256):
            raise CharterError("code hash must be sha256 hex")
        try:
            capital = Decimal(self.capital_inr)
        except InvalidOperation as exc:
            raise CharterError("capital must be a decimal string") from exc
        if not capital.is_finite() or capital <= 0:
            raise CharterError("capital must be positive")
        start, end = date.fromisoformat(self.start_on), date.fromisoformat(self.end_on)
        if end <= start:
            raise CharterError("end must be after start")
        if not self.universe or list(self.universe) != sorted(set(self.universe)):
            raise CharterError("universe must be non-empty, unique and sorted")
        if not self.benchmarks:
            raise CharterError("at least one frozen benchmark is required")
        if self.horizon_sessions < 1:
            raise CharterError("horizon must be at least one session")
        if list(self.parameters) != sorted(self.parameters):
            raise CharterError("parameters must be sorted by name")

    def canonical_bytes(self) -> bytes:
        body = {
            "charter_id": self.charter_id, "version": self.version, "strategy_id": self.strategy_id,
            "code_sha256": self.code_sha256, "capital_inr": str(Decimal(self.capital_inr)),
            "start_on": self.start_on, "end_on": self.end_on, "universe": list(self.universe),
            "benchmarks": list(self.benchmarks), "horizon_sessions": self.horizon_sessions,
            "parameters": [list(p) for p in self.parameters], "prior_sha256": self.prior_sha256,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def revise(self, *, new_start_on: str, **changes) -> "ExperimentCharter":
        """A modification is a new version with a new prospective clock (its start must move forward)."""
        if date.fromisoformat(new_start_on) <= date.fromisoformat(self.start_on):
            raise CharterError("a revised charter needs a later start date: its clock is new")
        if not changes:
            raise CharterError("a revision must change something")
        return replace(self, version=self.version + 1, prior_sha256=self.sha256(), start_on=new_start_on, **changes)
