"""Frozen multi-domain degradation and pause criteria for paper strategies (PA-07-18)."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any


class PauseCriteriaError(ValueError):
    """Frozen pause policy or evaluation evidence is invalid or conflicts with the ledger."""


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise PauseCriteriaError(f"{name} must be lowercase SHA-256")


def _digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PauseCriteria:
    strategy_id: str
    version: int
    benchmark_window_sessions: int
    minimum_benchmark_excess: float
    maximum_drawdown: float
    maximum_cost_to_gross_ratio: float
    minimum_source_quality: float
    maximum_quote_age_seconds: float

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or type(self.version) is not int or self.version < 1:
            raise PauseCriteriaError("strategy ID and positive integer criteria version are required")
        if type(self.benchmark_window_sessions) is not int or self.benchmark_window_sessions < 1:
            raise PauseCriteriaError("benchmark window must be positive sessions")
        values = (self.minimum_benchmark_excess, self.maximum_drawdown,
                  self.maximum_cost_to_gross_ratio, self.minimum_source_quality,
                  self.maximum_quote_age_seconds)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in values):
            raise PauseCriteriaError("pause thresholds must be finite numbers")
        if not -1 <= self.minimum_benchmark_excess <= 1:
            raise PauseCriteriaError("minimum benchmark excess must be in [-1, 1]")
        if not 0 < self.maximum_drawdown <= 1:
            raise PauseCriteriaError("maximum drawdown must be in (0, 1]")
        if self.maximum_cost_to_gross_ratio <= 0:
            raise PauseCriteriaError("maximum cost-to-gross ratio must be positive")
        if not 0 <= self.minimum_source_quality <= 1:
            raise PauseCriteriaError("minimum source quality must be in [0, 1]")
        if self.maximum_quote_age_seconds <= 0:
            raise PauseCriteriaError("maximum quote age must be positive")

    def body(self) -> dict[str, Any]:
        return asdict(self)

    def sha256(self) -> str:
        return _digest(self.body())


@dataclass(frozen=True, slots=True)
class PauseMetrics:
    benchmark_excess: float | None
    benchmark_window_sessions: int
    drawdown: float | None
    cost_to_gross_ratio: float | None
    source_quality: float | None
    quote_age_seconds: float | None
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class PauseDecision:
    strategy_id: str
    session: str
    criteria_version: int
    criteria_sha256: str
    state: str
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    decision_sha256: str


class PauseCriteriaLedger:
    """Append-only SQLite criteria/decision store with monotone-stricter versions."""

    def __init__(self, path: str | Path) -> None:
        self._con = sqlite3.connect(str(path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS pause_criteria (
            strategy_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(strategy_id, version))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS pause_decisions (
            strategy_id TEXT NOT NULL, session TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(strategy_id, session))""")
        self._con.commit()

    def register(self, criteria: PauseCriteria) -> str:
        body = criteria.body()
        digest = criteria.sha256()
        existing = self._con.execute(
            "SELECT digest, body FROM pause_criteria WHERE strategy_id=? AND version=?",
            (criteria.strategy_id, criteria.version)).fetchone()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if existing:
            if existing == (digest, encoded):
                return digest
            raise PauseCriteriaError("registered criteria version is immutable")
        prior = self._con.execute(
            "SELECT body FROM pause_criteria WHERE strategy_id=? ORDER BY version DESC LIMIT 1",
            (criteria.strategy_id,)).fetchone()
        if prior:
            previous = json.loads(prior[0])
            if criteria.version != previous["version"] + 1:
                raise PauseCriteriaError("criteria versions must be consecutive")
            relaxation = (
                criteria.benchmark_window_sessions != previous["benchmark_window_sessions"]
                or criteria.minimum_benchmark_excess < previous["minimum_benchmark_excess"]
                or criteria.maximum_drawdown > previous["maximum_drawdown"]
                or criteria.maximum_cost_to_gross_ratio > previous["maximum_cost_to_gross_ratio"]
                or criteria.minimum_source_quality < previous["minimum_source_quality"]
                or criteria.maximum_quote_age_seconds > previous["maximum_quote_age_seconds"]
            )
            if relaxation:
                raise PauseCriteriaError("new criteria cannot automatically relax a prior threshold")
        self._con.execute("INSERT INTO pause_criteria VALUES (?,?,?,?)",
                          (criteria.strategy_id, criteria.version, digest, encoded))
        self._con.commit()
        return digest

    def evaluate(self, *, criteria: PauseCriteria, session: date, metrics: PauseMetrics) -> PauseDecision:
        registered = self._con.execute(
            "SELECT digest FROM pause_criteria WHERE strategy_id=? AND version=?",
            (criteria.strategy_id, criteria.version)).fetchone()
        if registered is None or registered[0] != criteria.sha256():
            raise PauseCriteriaError("evaluation requires the exact registered frozen criteria")
        if metrics.benchmark_window_sessions != criteria.benchmark_window_sessions:
            raise PauseCriteriaError("benchmark evidence window differs from frozen criteria")
        _sha256(metrics.evidence_sha256, "metrics evidence hash")
        reasons = []
        values = {"benchmark_excess": metrics.benchmark_excess, "drawdown": metrics.drawdown,
                  "cost_to_gross_ratio": metrics.cost_to_gross_ratio,
                  "source_quality": metrics.source_quality, "quote_age_seconds": metrics.quote_age_seconds}
        for name, value in values.items():
            if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                reasons.append(f"MISSING_OR_INVALID_{name.upper()}")
        if metrics.benchmark_excess is not None and not -1 <= metrics.benchmark_excess <= 1:
            reasons.append("INVALID_BENCHMARK_EXCESS_RANGE")
        if metrics.drawdown is not None and not 0 <= metrics.drawdown <= 1:
            reasons.append("INVALID_DRAWDOWN_RANGE")
        if metrics.cost_to_gross_ratio is not None and metrics.cost_to_gross_ratio < 0:
            reasons.append("INVALID_COST_RATIO_RANGE")
        if metrics.source_quality is not None and not 0 <= metrics.source_quality <= 1:
            reasons.append("INVALID_SOURCE_QUALITY_RANGE")
        if metrics.quote_age_seconds is not None and metrics.quote_age_seconds < 0:
            reasons.append("INVALID_QUOTE_AGE_RANGE")
        if not reasons:
            if metrics.benchmark_excess is not None and metrics.benchmark_excess < criteria.minimum_benchmark_excess:
                reasons.append("BENCHMARK_EXCESS_BELOW_FROZEN_FLOOR")
            if metrics.drawdown is not None and metrics.drawdown > criteria.maximum_drawdown:
                reasons.append("DRAWDOWN_ABOVE_FROZEN_LIMIT")
            if metrics.cost_to_gross_ratio is not None and metrics.cost_to_gross_ratio > criteria.maximum_cost_to_gross_ratio:
                reasons.append("OBSERVED_COST_STRESS_ABOVE_FROZEN_LIMIT")
            if metrics.source_quality is not None and metrics.source_quality < criteria.minimum_source_quality:
                reasons.append("SOURCE_QUALITY_BELOW_FROZEN_FLOOR")
            if metrics.quote_age_seconds is not None and metrics.quote_age_seconds > criteria.maximum_quote_age_seconds:
                reasons.append("QUOTE_STALE_BEYOND_FROZEN_LIMIT")
        state = "PAUSE" if reasons else "CONTINUE"
        metric_body = {**values, "benchmark_window_sessions": metrics.benchmark_window_sessions,
                       "evidence_sha256": metrics.evidence_sha256}
        body = {"strategy_id": criteria.strategy_id, "session": session.isoformat(),
                "criteria_version": criteria.version, "criteria_sha256": criteria.sha256(),
                "state": state, "reasons": sorted(reasons), "metrics": metric_body}
        decision = PauseDecision(criteria.strategy_id, session.isoformat(), criteria.version,
                                 criteria.sha256(), state, tuple(sorted(reasons)), metric_body, _digest(body))
        return decision

    def append_decision(self, decision: PauseDecision) -> bool:
        body = {"strategy_id": decision.strategy_id, "session": decision.session,
                "criteria_version": decision.criteria_version, "criteria_sha256": decision.criteria_sha256,
                "state": decision.state, "reasons": list(decision.reasons), "metrics": decision.metrics}
        digest = _digest(body)
        if digest != decision.decision_sha256:
            raise PauseCriteriaError("pause decision digest does not match payload")
        encoded = json.dumps({**body, "decision_sha256": digest}, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest, body FROM pause_decisions WHERE strategy_id=? AND session=?",
                                  (decision.strategy_id, decision.session)).fetchone()
        if prior:
            if prior == (digest, encoded):
                return False
            raise PauseCriteriaError("pause decision is immutable; conflicting evaluation")
        self._con.execute("INSERT INTO pause_decisions VALUES (?,?,?,?)",
                          (decision.strategy_id, decision.session, digest, encoded))
        self._con.commit()
        return True

    def close(self) -> None:
        self._con.close()


__all__ = ["PauseCriteria", "PauseCriteriaError", "PauseCriteriaLedger",
           "PauseDecision", "PauseMetrics"]
