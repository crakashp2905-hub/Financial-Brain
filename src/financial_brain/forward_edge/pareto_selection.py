"""Training-only Pareto comparison with an immutable decision archive."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Iterable


class ParetoSelectionError(ValueError):
    """A Pareto candidate or archive violates the training-only decision contract."""


@dataclass(frozen=True)
class TrainingCandidate:
    strategy_id: str
    code_sha256: str
    data_split: str
    training_data_sha256: str
    evaluated_through: date
    net_return: float
    max_drawdown: float
    turnover: float
    capacity_notional: float
    complexity: float

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or self.data_split != "TRAIN":
            raise ParetoSelectionError("candidate must have an id and TRAIN-only data split")
        for name, digest in (("code", self.code_sha256), ("training data", self.training_data_sha256)):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ParetoSelectionError(f"candidate {name} digest must be lowercase sha256")
        values = (self.net_return, self.max_drawdown, self.turnover, self.capacity_notional, self.complexity)
        if any(not isfinite(value) for value in values):
            raise ParetoSelectionError("candidate metrics must be finite")
        if not 0 <= self.max_drawdown <= 1 or self.turnover < 0 or self.capacity_notional < 0 or self.complexity < 0:
            raise ParetoSelectionError("drawdown must be in [0,1] and turnover/capacity/complexity non-negative")

    def objectives(self) -> tuple[float, float, float, float, float]:
        """All objectives converted to minimization: net return and capacity are maximized."""
        return (-self.net_return, self.max_drawdown, self.turnover, -self.capacity_notional, self.complexity)


@dataclass(frozen=True)
class ParetoReport:
    selected_strategy_id: str
    selection_reason: str
    training_data_sha256: str
    evaluated_through: date
    candidates: tuple[dict, ...]

    def canonical(self) -> dict:
        return {
            "schema_version": "1.0",
            "scope": "TRAIN_ONLY",
            "selected_strategy_id": self.selected_strategy_id,
            "selection_reason": self.selection_reason,
            "training_data_sha256": self.training_data_sha256,
            "evaluated_through": self.evaluated_through.isoformat(),
            "objectives": {
                "maximize": ["net_return", "capacity_notional"],
                "minimize": ["max_drawdown", "turnover", "complexity"],
            },
            "candidates": list(self.candidates),
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_pareto_report(candidates: Iterable[TrainingCandidate], *, selected_strategy_id: str,
                        selection_reason: str) -> ParetoReport:
    rows = tuple(candidates)
    if not rows or len({row.strategy_id for row in rows}) != len(rows):
        raise ParetoSelectionError("candidates must be non-empty and have unique strategy ids")
    if not selection_reason.strip():
        raise ParetoSelectionError("selection reason must be explicit")
    if len({row.training_data_sha256 for row in rows}) != 1 or len({row.evaluated_through for row in rows}) != 1:
        raise ParetoSelectionError("all candidates must share the same frozen training data and end date")
    by_id = {row.strategy_id: row for row in rows}
    if selected_strategy_id not in by_id:
        raise ParetoSelectionError("selected strategy is absent from candidate archive")
    frontier: set[str] = set()
    dominated_by: dict[str, list[str]] = {}
    for candidate in rows:
        dominators = []
        for other in rows:
            if other.strategy_id == candidate.strategy_id:
                continue
            left, right = other.objectives(), candidate.objectives()
            if all(a <= b for a, b in zip(left, right)) and any(a < b for a, b in zip(left, right)):
                dominators.append(other.strategy_id)
        if dominators:
            dominated_by[candidate.strategy_id] = sorted(dominators)
        else:
            frontier.add(candidate.strategy_id)
    if selected_strategy_id not in frontier:
        raise ParetoSelectionError("selected strategy is dominated on the declared training objectives")
    archive = []
    for row in sorted(rows, key=lambda item: item.strategy_id):
        if row.strategy_id == selected_strategy_id:
            reason = f"SELECTED: {selection_reason.strip()}"
        elif row.strategy_id in dominated_by:
            reason = "DOMINATED_BY:" + ",".join(dominated_by[row.strategy_id])
        else:
            reason = f"PARETO_ALTERNATIVE_NOT_SELECTED: {selection_reason.strip()}"
        archive.append({**asdict(row), "evaluated_through": row.evaluated_through.isoformat(),
                        "pareto_frontier": row.strategy_id in frontier, "decision_reason": reason})
    return ParetoReport(selected_strategy_id, selection_reason.strip(), rows[0].training_data_sha256,
                        rows[0].evaluated_through, tuple(archive))


def write_pareto_report(path: Path, report: ParetoReport) -> str:
    """Create an immutable JSON report; identical retries are idempotent and rewrites are refused."""
    payload = json.dumps(report.canonical(), sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != payload:
            raise ParetoSelectionError("existing Pareto report is immutable and differs from this decision") from None
    return report.sha256
