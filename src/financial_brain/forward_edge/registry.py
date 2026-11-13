"""Strategy registry (FE-0022 .. FE-0026, FE-0029 .. FE-0032).

* :class:`StrategySpec` is immutable and validated; missing fields fail registration (FE-0023).
* Status taxonomy: FAILED, EXPLORATORY, SHADOW_ONLY, PAPER_ELIGIBLE, FROZEN_CHALLENGER, VERIFIED, RETIRED (FE-0024).
  A FAILED strategy can only move to RETIRED; it can never become eligible, frozen or verified by a status edit.
* Any status change is a new spec version with a history entry; old versions are never rewritten (FE-0029).
* Benchmarks are frozen references that must exist before the first forward result (FE-0025).
* Shadow adapters emit signals only; order intents are allowed only for paper-eligible families (FE-0026).
* Null and placebo controllers provide measurable nulls (FE-0030). Selection on shadow results is labelled
  exploratory unless a preregistered trial exists (FE-0032). Correlated candidates are deduplicated (FE-0031).
* Explicit parameter hashes on retained failed specs cannot be registered again under a renamed ID (PE-0046).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{64}$")


class RegistryError(ValueError):
    pass


class Status(str, Enum):
    FAILED = "FAILED"
    EXPLORATORY = "EXPLORATORY"
    SHADOW_ONLY = "SHADOW_ONLY"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    FROZEN_CHALLENGER = "FROZEN_CHALLENGER"
    VERIFIED = "VERIFIED"
    RETIRED = "RETIRED"


ALLOWED: dict[Status, frozenset[Status]] = {
    Status.EXPLORATORY: frozenset({Status.SHADOW_ONLY, Status.FAILED, Status.RETIRED}),
    Status.SHADOW_ONLY: frozenset({Status.PAPER_ELIGIBLE, Status.RETIRED, Status.FAILED}),
    Status.PAPER_ELIGIBLE: frozenset({Status.FROZEN_CHALLENGER, Status.RETIRED, Status.SHADOW_ONLY}),
    Status.FROZEN_CHALLENGER: frozenset({Status.VERIFIED, Status.RETIRED}),
    Status.VERIFIED: frozenset({Status.RETIRED}),
    Status.FAILED: frozenset({Status.RETIRED}),
    Status.RETIRED: frozenset(),
}
#: Statuses that may place paper intents at all.
PAPER_CAPABLE = frozenset({Status.PAPER_ELIGIBLE, Status.FROZEN_CHALLENGER, Status.VERIFIED})
DIRECTIONS = frozenset({"LONG", "SHORT", "LONG_SHORT"})
BENCHMARK_KINDS = frozenset({"FLAT_CASH", "SECTOR_MATCHED", "INDEX", "EQUAL_RISK", "MOMENTUM_MATCHED"})


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    family: str
    version: int
    code_sha256: str
    asset_scope: tuple[str, ...]
    decision_clock: str
    direction: str
    exit_rule: str
    horizon_sessions: int
    data_requirements: tuple[str, ...]
    source_lineage: str
    status: Status
    parameter_sha256: str | None = None
    exclusion_reason: str | None = None
    benchmark_id: str | None = None
    history: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        required = {
            "strategy_id": self.strategy_id, "family": self.family, "decision_clock": self.decision_clock,
            "exit_rule": self.exit_rule, "source_lineage": self.source_lineage,
        }
        missing = sorted(k for k, v in required.items() if not str(v).strip())
        if missing:
            raise RegistryError(f"missing fields: {', '.join(missing)}")
        if self.version < 1:
            raise RegistryError("version starts at 1")
        if not _SHA.match(self.code_sha256):
            raise RegistryError("code hash must be sha256 hex")
        if self.parameter_sha256 is not None and not _SHA.match(self.parameter_sha256):
            raise RegistryError("parameter hash must be sha256 hex")
        if not self.asset_scope or not self.data_requirements:
            raise RegistryError("asset scope and data requirements are required")
        if self.direction not in DIRECTIONS:
            raise RegistryError(f"direction must be one of {sorted(DIRECTIONS)}")
        if self.horizon_sessions < 1:
            raise RegistryError("horizon must be at least one session")
        if self.status in (Status.FAILED, Status.RETIRED) and not self.exclusion_reason:
            raise RegistryError(f"{self.status.value} needs an exclusion reason")
        if self.benchmark_id is not None and self.benchmark_id.strip() == "":
            raise RegistryError("benchmark id cannot be blank")
        if self.status in PAPER_CAPABLE and not self.benchmark_id:
            raise RegistryError("paper-capable strategies need a frozen benchmark")

    @property
    def can_place_paper_intents(self) -> bool:
        return self.status in PAPER_CAPABLE


class Registry:
    def __init__(self) -> None:
        self._specs: dict[str, StrategySpec] = {}

    def register(self, spec: StrategySpec) -> StrategySpec:
        if spec.strategy_id in self._specs:
            raise RegistryError(f"{spec.strategy_id} is already registered; use transition()")
        if spec.parameter_sha256 is not None and any(
            old.parameter_sha256 == spec.parameter_sha256
            and (old.status is Status.FAILED or any("->FAILED:" in row or "FAILED->" in row
                                                    for row in old.history))
            for old in self._specs.values()
        ):
            raise RegistryError("parameter hash duplicates a retained failed trial")
        self._specs[spec.strategy_id] = spec
        return spec


    def get(self, strategy_id: str) -> StrategySpec:
        return self._specs[strategy_id]

    def effective_status(self, strategy_id: str) -> Status:
        """Unregistered candidates default to observation-only status."""
        spec = self._specs.get(strategy_id)
        return spec.status if spec is not None else Status.SHADOW_ONLY

    def can_place_paper_intents(self, strategy_id: str) -> bool:
        spec = self._specs.get(strategy_id)
        return spec is not None and spec.can_place_paper_intents

    def __len__(self) -> int:
        return len(self._specs)

    def transition(self, strategy_id: str, new: Status, *, reason: str, human_approved: bool = False,
                   custody_verified: bool = False) -> StrategySpec:
        spec = self._specs[strategy_id]
        if new not in ALLOWED[spec.status]:
            raise RegistryError(f"{spec.status.value} -> {new.value} is not allowed for {strategy_id}")
        if new is Status.PAPER_ELIGIBLE and not human_approved:
            raise RegistryError("enrolment for paper needs a human approval")
        if new is Status.VERIFIED and not custody_verified:
            raise RegistryError("VERIFIED needs independently verified forward evidence")
        if new in (Status.FAILED, Status.RETIRED) and not reason.strip():
            raise RegistryError("a status with an exclusion needs a reason")
        nxt = replace(
            spec, status=new, version=spec.version + 1,
            exclusion_reason=reason if new in (Status.FAILED, Status.RETIRED) else spec.exclusion_reason,
            history=spec.history + (f"v{spec.version}:{spec.status.value}->{new.value}:{reason}",),
        )
        self._specs[strategy_id] = nxt
        return nxt


def parameter_sha256(parameters: dict) -> str:
    """Canonical parameter identity for detecting a renamed retry of a failed strategy."""
    if not isinstance(parameters, dict):
        raise RegistryError("strategy parameters must be an object")
    try:
        body = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RegistryError("strategy parameters must be finite canonical JSON") from exc
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Benchmark:
    benchmark_id: str
    kind: str
    definition_sha256: str
    frozen_on: date

    def __post_init__(self) -> None:
        if self.kind not in BENCHMARK_KINDS:
            raise RegistryError(f"benchmark kind must be one of {sorted(BENCHMARK_KINDS)}")
        if not _SHA.match(self.definition_sha256):
            raise RegistryError("benchmark definition must be hashed")


def assert_benchmark_frozen_before(benchmark: Benchmark, first_forward_result_on: date) -> None:
    if benchmark.frozen_on >= first_forward_result_on:
        raise RegistryError("benchmark must be frozen before the first forward result")


# ---- shadow adapters (FE-0026) ----

@dataclass(frozen=True)
class ShadowSignal:
    strategy_id: str
    as_of: str
    signal: str  # LONG, SHORT or NONE. There is no quantity field: shadow never sizes an order.


def shadow_signal(spec: StrategySpec, as_of: str, raw_signal: str) -> ShadowSignal:
    if raw_signal not in ("LONG", "SHORT", "NONE"):
        raise RegistryError("shadow signal must be LONG, SHORT or NONE")
    return ShadowSignal(spec.strategy_id, as_of, raw_signal)


def order_intent_allowed(spec: StrategySpec | None, registry: Registry | None = None) -> bool:
    """Only the exact registered paper-capable spec may produce a paper intent."""
    if spec is None or registry is None:
        return False
    try:
        registered = registry.get(spec.strategy_id)
    except KeyError:
        return False
    return registered == spec and registered.can_place_paper_intents


# ---- nulls and placebos (FE-0030) ----

def null_portfolio(days: list[str]) -> dict[str, list[str]]:
    """The no-trade control: every day, nothing held."""
    return {d: [] for d in days}


def placebo_portfolio(universe: list[str], days: list[str], *, n: int, seed: int) -> dict[str, list[str]]:
    """Seeded random holdings of the same size: a measurable placebo for any selection rule."""
    if n < 0 or n > len(universe):
        raise RegistryError("placebo size must fit the universe")
    rng = random.Random(seed)
    return {d: sorted(rng.sample(sorted(universe), n)) for d in days}


# ---- selection and multiplicity (FE-0032, FE-0031) ----

def selection_label(preregistered_trial: bool) -> str:
    return "CONFIRMATORY_CANDIDATE" if preregistered_trial else "EXPLORATORY_ONLY"


def dedupe_correlated(returns: dict[str, list[float]], *, threshold: float = 0.9) -> tuple[list[str], list[str]]:
    """Greedy deduplication by absolute Pearson correlation; returns (kept, dropped), in input order."""
    if not 0 < threshold <= 1:
        raise RegistryError("threshold must be in (0, 1]")
    kept: list[str] = []
    dropped: list[str] = []
    for name in sorted(returns):
        series = returns[name]
        if any(_abs_corr(series, returns[k]) >= threshold for k in kept):
            dropped.append(name)
        else:
            kept.append(name)
    return kept, dropped


def _abs_corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return abs(cov / (va * vb) ** 0.5)


# ---- inventory (FE-0022, file-level) ----

INVENTORY_GLOBS = ("src/financial_brain/strategies/*.py", "src/financial_brain/paper/*.py",
                   "src/financial_brain/kernel/strategy_*.py")


def inventory(repo_root: Path) -> list[dict]:
    rows = []
    for pattern in INVENTORY_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            rows.append({"path": path.relative_to(repo_root).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return rows
