"""Frozen baseline-control contract for comparable strategy decisions.

This validates the design identity before results are compared. It does not manufacture missing
cash rates, index histories, exposure estimates, random portfolios, or transaction-cost evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Mapping


class BaselineControlError(ValueError):
    """Baseline set is incomplete or not comparable to the strategy evaluation."""


class BaselineKind(StrEnum):
    CASH = "CASH"
    MATCHED_INDEX_BUY_HOLD = "MATCHED_INDEX_BUY_HOLD"
    EQUAL_WEIGHT_UNIVERSE = "EQUAL_WEIGHT_UNIVERSE"
    EQUAL_TURNOVER_RANDOM = "EQUAL_TURNOVER_RANDOM"
    EXPOSURE_MATCHED = "EXPOSURE_MATCHED"
    COST_MATCHED_NULL = "COST_MATCHED_NULL"


REQUIRED_CONTROLS = frozenset(BaselineKind)


def _sha(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise BaselineControlError(f"{label} must be a lowercase SHA-256")


@dataclass(frozen=True)
class StrategyEvaluationIdentity:
    experiment_id: str
    universe_sha256: str
    calendar_sha256: str
    evaluation_window: tuple[str, str]
    cost_schedule_sha256: str
    exposure_profile_sha256: str
    turnover_fraction: float

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise BaselineControlError("experiment ID is required")
        for value, label in ((self.universe_sha256, "universe"), (self.calendar_sha256, "calendar"),
                             (self.cost_schedule_sha256, "cost schedule"),
                             (self.exposure_profile_sha256, "exposure profile")):
            _sha(value, label)
        if len(self.evaluation_window) != 2 or not all(self.evaluation_window):
            raise BaselineControlError("evaluation window needs inclusive start/end labels")
        if self.evaluation_window[0] > self.evaluation_window[1]:
            raise BaselineControlError("evaluation window is reversed")
        if not math.isfinite(self.turnover_fraction) or self.turnover_fraction < 0:
            raise BaselineControlError("turnover fraction must be finite and nonnegative")


@dataclass(frozen=True)
class BaselineControl:
    kind: BaselineKind
    series_id: str
    universe_sha256: str
    calendar_sha256: str
    evaluation_window: tuple[str, str]
    cost_schedule_sha256: str
    exposure_profile_sha256: str
    turnover_fraction: float
    construction_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BaselineKind) or not self.series_id.strip():
            raise BaselineControlError("typed baseline kind and series ID are required")
        for value, label in ((self.universe_sha256, "universe"), (self.calendar_sha256, "calendar"),
                             (self.cost_schedule_sha256, "cost schedule"),
                             (self.exposure_profile_sha256, "exposure profile"),
                             (self.construction_sha256, "construction"), (self.evidence_sha256, "evidence")):
            _sha(value, label)
        if not math.isfinite(self.turnover_fraction) or self.turnover_fraction < 0:
            raise BaselineControlError("turnover fraction must be finite and nonnegative")


@dataclass(frozen=True)
class BaselineReturnSeries:
    """Period-indexed net returns plus the frozen source and construction identities."""

    kind: BaselineKind
    series_id: str
    net_returns: tuple[tuple[str, float], ...]
    source_sha256: str
    construction_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BaselineKind) or not self.series_id.strip():
            raise BaselineControlError("baseline return series requires a typed kind and series ID")
        _sha(self.source_sha256, "baseline return source")
        _sha(self.construction_sha256, "baseline construction")
        labels = [label for label, _ in self.net_returns]
        if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise BaselineControlError("baseline net returns require nonempty period labels")
        if labels != sorted(set(labels)):
            raise BaselineControlError("baseline return periods must be unique and chronological")
        if any(not math.isfinite(value) or value <= -1 for _, value in self.net_returns):
            raise BaselineControlError("baseline net returns must be finite and above -100%")

    def sha256(self) -> str:
        payload = {
            "kind": self.kind.value,
            "series_id": self.series_id,
            "net_returns": [[label, value] for label, value in self.net_returns],
            "source_sha256": self.source_sha256,
            "construction_sha256": self.construction_sha256,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenBaselineSet:
    experiment: StrategyEvaluationIdentity
    controls: tuple[BaselineControl, ...]

    def __post_init__(self) -> None:
        kinds = [c.kind for c in self.controls]
        if len(kinds) != len(set(kinds)) or set(kinds) != REQUIRED_CONTROLS:
            missing = sorted(k.value for k in REQUIRED_CONTROLS - set(kinds))
            extra = sorted(k.value for k in set(kinds) - REQUIRED_CONTROLS)
            raise BaselineControlError(f"baseline controls must be exactly the six required kinds; missing={missing}, extra={extra}")
        for control in self.controls:
            if control.universe_sha256 != self.experiment.universe_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different universe")
            if control.calendar_sha256 != self.experiment.calendar_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different calendar")
            if control.evaluation_window != self.experiment.evaluation_window:
                raise BaselineControlError(f"{control.kind.value} uses a different evaluation window")
            if control.cost_schedule_sha256 != self.experiment.cost_schedule_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different cost schedule identity")
        by_kind = {c.kind: c for c in self.controls}
        if by_kind[BaselineKind.EXPOSURE_MATCHED].exposure_profile_sha256 != self.experiment.exposure_profile_sha256:
            raise BaselineControlError("exposure-matched baseline does not match strategy exposures")
        if not math.isclose(by_kind[BaselineKind.EQUAL_TURNOVER_RANDOM].turnover_fraction,
                            self.experiment.turnover_fraction, rel_tol=0.0, abs_tol=1e-9):
            raise BaselineControlError("random baseline turnover does not match strategy turnover")

    def sha256(self) -> str:
        rows = [{"kind": c.kind.value, "series_id": c.series_id,
                 "universe_sha256": c.universe_sha256, "calendar_sha256": c.calendar_sha256,
                 "evaluation_window": list(c.evaluation_window), "cost_schedule_sha256": c.cost_schedule_sha256,
                 "exposure_profile_sha256": c.exposure_profile_sha256,
                 "turnover_fraction": c.turnover_fraction, "construction_sha256": c.construction_sha256,
                 "evidence_sha256": c.evidence_sha256} for c in sorted(self.controls, key=lambda x: x.kind.value)]
        payload = {"experiment_id": self.experiment.experiment_id, "controls": rows}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()

    def compare(self, *, strategy_net_returns: tuple[tuple[str, float], ...],
                strategy_source_sha256: str,
                series: Mapping[BaselineKind, BaselineReturnSeries]) -> dict[str, object]:
        """Compare one frozen strategy result with all six identity-matched net controls.

        This validates and reports supplied return series; it does not calculate index,
        exposure, random-book, or transaction-cost returns from market data.
        """
        _sha(strategy_source_sha256, "strategy result source")
        strategy = _validate_points(strategy_net_returns, "strategy")
        if set(series) != REQUIRED_CONTROLS:
            missing = sorted(kind.value for kind in REQUIRED_CONTROLS - set(series))
            extra = sorted(kind.value for kind in set(series) - REQUIRED_CONTROLS)
            raise BaselineControlError(f"comparison requires all six controls; missing={missing}, extra={extra}")
        strategy_labels = tuple(label for label, _ in strategy)
        strategy_values = tuple(value for _, value in strategy)
        controls_by_kind = {control.kind: control for control in self.controls}
        strategy_summary = _return_summary(strategy_values)
        comparisons: dict[str, object] = {}
        for kind in sorted(REQUIRED_CONTROLS, key=lambda item: item.value):
            observed = series[kind]
            frozen = controls_by_kind[kind]
            if observed.kind is not kind or observed.series_id != frozen.series_id:
                raise BaselineControlError(f"{kind.value} series identity differs from its frozen control")
            if observed.source_sha256 != frozen.evidence_sha256:
                raise BaselineControlError(f"{kind.value} source hash differs from its frozen evidence")
            if observed.construction_sha256 != frozen.construction_sha256:
                raise BaselineControlError(f"{kind.value} construction differs from its frozen control")
            points = _validate_points(observed.net_returns, kind.value)
            if tuple(label for label, _ in points) != strategy_labels:
                raise BaselineControlError(f"{kind.value} periods do not exactly match the strategy")
            summary = _return_summary(tuple(value for _, value in points))
            comparisons[kind.value] = {
                "series_sha256": observed.sha256(),
                **summary,
                "mean_net_excess": strategy_summary["mean_net_return"] - summary["mean_net_return"],
                "cumulative_net_excess": strategy_summary["cumulative_net_return"] - summary["cumulative_net_return"],
            }
        return {
            "schema": "fb.frozen_baseline_comparison.v1",
            "experiment_id": self.experiment.experiment_id,
            "baseline_set_sha256": self.sha256(),
            "strategy_source_sha256": strategy_source_sha256,
            "periods": list(strategy_labels),
            "strategy": {"net_series_sha256": _points_sha256(strategy), **strategy_summary},
            "controls": comparisons,
            "interpretation": "Descriptive aligned net-return comparison; not an alpha certification or profitability claim.",
        }


def _validate_points(points: tuple[tuple[str, float], ...], label: str) -> tuple[tuple[str, float], ...]:
    if not points:
        raise BaselineControlError(f"{label} return series is empty")
    labels = [period for period, _ in points]
    if any(not isinstance(period, str) or not period.strip() for period in labels):
        raise BaselineControlError(f"{label} return periods require nonempty labels")
    if labels != sorted(set(labels)):
        raise BaselineControlError(f"{label} return periods must be unique and chronological")
    if any(not math.isfinite(value) or value <= -1 for _, value in points):
        raise BaselineControlError(f"{label} net returns must be finite and above -100%")
    return points


def _points_sha256(points: tuple[tuple[str, float], ...]) -> str:
    raw = json.dumps([[period, value] for period, value in points], separators=(",", ":"),
                     allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _return_summary(values: tuple[float, ...]) -> dict[str, float | int]:
    cumulative = math.prod(1.0 + value for value in values) - 1.0
    return {"period_count": len(values), "mean_net_return": sum(values) / len(values),
            "cumulative_net_return": cumulative}


__all__ = ["BaselineControl", "BaselineControlError", "BaselineKind", "BaselineReturnSeries", "FrozenBaselineSet",
           "StrategyEvaluationIdentity"]
