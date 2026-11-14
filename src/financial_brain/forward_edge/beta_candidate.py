"""Content-addressed beta-neutral research candidates and separate performance receipts."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Iterable

from .durable import DurableJournal


class BetaCandidateError(ValueError):
    """Invalid candidate fit, identity, or candidate-specific performance receipt."""


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric strategy and benchmark returns required")
    return float(value)


def _sha(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise BetaCandidateError(f"{label} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class BetaNeutralCandidate:
    base_strategy_sha256: str
    strategy_sha256: str
    benchmark_id: str
    beta: float
    development_data_sha256: str
    calibration_protocol_sha256: str
    development_start: str
    development_end: str
    validation_start: str
    observation_count: int

    def __post_init__(self) -> None:
        _sha(self.base_strategy_sha256, "base strategy hash")
        _sha(self.strategy_sha256, "candidate strategy hash")
        _sha(self.development_data_sha256, "development data hash")
        _sha(self.calibration_protocol_sha256, "calibration protocol hash")
        if self.strategy_sha256 == self.base_strategy_sha256:
            raise BetaCandidateError("beta-neutral candidate must have a separate strategy identity")
        if not self.benchmark_id.strip() or not math.isfinite(self.beta):
            raise BetaCandidateError("benchmark identity and finite development beta are required")
        if isinstance(self.observation_count, bool) or self.observation_count < 2:
            raise BetaCandidateError("beta fit needs at least two development observations")
        start, end, validation = map(date.fromisoformat,
                                     (self.development_start, self.development_end, self.validation_start))
        if start > end or end >= validation:
            raise BetaCandidateError("beta fit must precede the declared validation period")
        expected = {"base_strategy_sha256": self.base_strategy_sha256, "benchmark_id": self.benchmark_id,
                    "transform": "beta-neutral-residual-v1", "beta": self.beta,
                    "development_data_sha256": self.development_data_sha256,
                    "calibration_protocol_sha256": self.calibration_protocol_sha256,
                    "validation_start": self.validation_start}
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
        if digest != self.strategy_sha256:
            raise BetaCandidateError("candidate strategy identity does not match its frozen parameters")

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def fit_beta_neutral_candidate(*, base_strategy_sha256: str, benchmark_id: str,
                               observations: Iterable[dict[str, object]], validation_start: date,
                               calibration_protocol_sha256: str, min_observations: int = 30
                               ) -> BetaNeutralCandidate:
    """Estimate beta on unique DEVELOPMENT rows and derive a distinct candidate identity."""
    _sha(base_strategy_sha256, "base strategy hash")
    _sha(calibration_protocol_sha256, "calibration protocol hash")
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise BetaCandidateError("named frozen benchmark is required")
    rows = list(observations)
    if isinstance(min_observations, bool) or min_observations < 2 or len(rows) < min_observations:
        raise BetaCandidateError("beta development sample is too small")
    normalized: list[dict[str, object]] = []
    dates: list[date] = []
    ids: set[str] = set()
    paired: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise BetaCandidateError("beta estimation may consume DEVELOPMENT rows only")
        try:
            exchange_date = date.fromisoformat(str(row["exchange_date"]))
            strategy_return = _number(row["strategy_return"])
            benchmark_return = _number(row["benchmark_return"])
            decision_sha = row["decision_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise BetaCandidateError("beta row lacks dated strategy/benchmark returns and provenance") from exc
        if (exchange_date >= validation_start
                or not math.isfinite(strategy_return) or not math.isfinite(benchmark_return)):
            raise BetaCandidateError("beta rows must be finite and precede validation")
        if (not isinstance(decision_sha, str) or len(decision_sha) != 64
                or any(char not in "0123456789abcdef" for char in decision_sha)
                or decision_sha in ids):
            raise BetaCandidateError("development decision identities must be unique SHA-256 digests")
        ids.add(decision_sha)
        dates.append(exchange_date)
        paired.append((strategy_return, benchmark_return))
        normalized.append({"exchange_date": exchange_date.isoformat(), "split": "DEVELOPMENT",
                           "strategy_return": strategy_return, "benchmark_return": benchmark_return,
                           "decision_sha256": decision_sha})
    benchmark_values = [benchmark for _, benchmark in paired]
    benchmark_mean = mean(benchmark_values)
    strategy_mean = mean(strategy for strategy, _ in paired)
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark_values)
    if variance <= 1e-18:
        raise BetaCandidateError("benchmark development returns have insufficient variance to estimate beta")
    covariance = sum((benchmark - benchmark_mean) * (strategy - strategy_mean)
                     for strategy, benchmark in paired)
    beta = covariance / variance
    normalized.sort(key=lambda item: (str(item["exchange_date"]), str(item["decision_sha256"])))
    data_body = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    data_sha = hashlib.sha256(data_body.encode("utf-8")).hexdigest()
    identity = {"base_strategy_sha256": base_strategy_sha256, "benchmark_id": benchmark_id,
                "transform": "beta-neutral-residual-v1", "beta": beta,
                "development_data_sha256": data_sha,
                "calibration_protocol_sha256": calibration_protocol_sha256,
                "validation_start": validation_start.isoformat()}
    strategy_sha = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode("utf-8")).hexdigest()
    return BetaNeutralCandidate(
        base_strategy_sha256=base_strategy_sha256, strategy_sha256=strategy_sha,
        benchmark_id=benchmark_id, beta=beta, development_data_sha256=data_sha,
        calibration_protocol_sha256=calibration_protocol_sha256,
        development_start=min(dates).isoformat(), development_end=max(dates).isoformat(),
        validation_start=validation_start.isoformat(), observation_count=len(rows))


def beta_neutral_return(candidate: BetaNeutralCandidate, *, strategy_return: float,
                        benchmark_return: float) -> float:
    """Remove the fitted benchmark component from one strategy return observation."""
    if not math.isfinite(strategy_return) or not math.isfinite(benchmark_return):
        raise BetaCandidateError("candidate returns must be finite")
    return strategy_return - candidate.beta * benchmark_return


@dataclass(frozen=True)
class BetaCandidatePerformance:
    candidate_strategy_sha256: str
    candidate_sha256: str
    benchmark_id: str
    session_date: str
    split: str
    gross_return: float
    benchmark_return: float
    fees: float
    spread: float
    impact: float
    receipt_sha256: str

    def __post_init__(self) -> None:
        _sha(self.candidate_strategy_sha256, "candidate strategy hash")
        _sha(self.candidate_sha256, "candidate specification hash")
        _sha(self.receipt_sha256, "performance receipt hash")
        if self.split != "VALIDATION":
            raise BetaCandidateError("candidate performance store accepts VALIDATION receipts only")
        date.fromisoformat(self.session_date)
        if not self.benchmark_id.strip() or not all(math.isfinite(value) for value in
                                                    (self.gross_return, self.benchmark_return,
                                                     self.fees, self.spread, self.impact)):
            raise BetaCandidateError("performance record requires finite returns, costs, and benchmark")
        if min(self.fees, self.spread, self.impact) < 0:
            raise BetaCandidateError("execution costs cannot be negative")

    @property
    def net_alpha(self) -> float:
        return self.gross_return - self.benchmark_return - self.fees - self.spread - self.impact

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class BetaCandidatePerformanceRegistry:
    """Append-only validation receipts keyed to their candidate strategy identity."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def register(self, candidate: BetaNeutralCandidate, *, registered_on: date) -> str:
        if registered_on >= date.fromisoformat(candidate.validation_start):
            raise BetaCandidateError("beta-neutral candidate must be registered before validation starts")
        record = {"t": "BETA_CANDIDATE_REGISTERED", "candidate": asdict(candidate),
                  "candidate_sha256": candidate.sha256, "registered_on": registered_on.isoformat()}
        prior = next((row for row in self.journal.records()
                      if row.get("t") == record["t"]
                      and row.get("candidate_sha256") == candidate.sha256), None)
        if prior is not None:
            if prior != record:
                raise BetaCandidateError("registered candidate identity and date are immutable")
            return candidate.sha256
        self.journal.append(record)
        return candidate.sha256

    def record(self, candidate: BetaNeutralCandidate, performance: BetaCandidatePerformance) -> str:
        registration = next((row for row in self.journal.records()
                            if row.get("t") == "BETA_CANDIDATE_REGISTERED"
                            and row.get("candidate_sha256") == candidate.sha256), None)
        if registration is None or registration.get("candidate") != asdict(candidate):
            raise BetaCandidateError("candidate must be immutably registered before its performance is recorded")
        if performance.candidate_strategy_sha256 != candidate.strategy_sha256 \
                or performance.candidate_sha256 != candidate.sha256 \
                or performance.benchmark_id != candidate.benchmark_id:
            raise BetaCandidateError("performance receipt must match candidate strategy and benchmark identity")
        if date.fromisoformat(performance.session_date) < date.fromisoformat(candidate.validation_start):
            raise BetaCandidateError("candidate performance record predates its validation window")
        record = {"t": "BETA_CANDIDATE_VALIDATION", "candidate_sha256": candidate.sha256,
                  "strategy_sha256": candidate.strategy_sha256, "performance": asdict(performance),
                  "performance_sha256": performance.sha256}
        prior = next((row for row in self.journal.records()
                      if row.get("performance", {}).get("receipt_sha256") == performance.receipt_sha256), None)
        if prior is not None:
            if prior != record:
                raise BetaCandidateError("performance receipt identity is immutable")
            return str(prior["performance_sha256"])
        self.journal.append(record)
        return performance.sha256

    def performances(self, strategy_sha256: str) -> tuple[BetaCandidatePerformance, ...]:
        _sha(strategy_sha256, "candidate strategy hash")
        return tuple(BetaCandidatePerformance(**row["performance"]) for row in self.journal.records()
                     if row.get("t") == "BETA_CANDIDATE_VALIDATION"
                     and row.get("strategy_sha256") == strategy_sha256)
