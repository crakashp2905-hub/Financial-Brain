"""Frozen, bounded development-only strategy ablations with complete outcome logs."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from itertools import product
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

from financial_brain.forward_edge.durable import DurableJournal


AXES = ("entry_rule", "exit_rule", "holding_horizon", "portfolio_width", "turnover_limit")


class AblationPlanError(ValueError):
    """Invalid frozen ablation search or outcome set."""


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise AblationPlanError(f"{name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class AblationPlan:
    search_id: str
    strategy_sha256: str
    development_data_sha256: str
    protocol_sha256: str
    development_start: str
    development_end: str
    protected_final_start: str
    protected_final_end: str
    frozen_at_utc: str
    axes: tuple[tuple[str, tuple[object, ...]], ...]
    max_combinations: int

    def __post_init__(self) -> None:
        if not isinstance(self.search_id, str) or not self.search_id.strip():
            raise AblationPlanError("search_id is required")
        for field in ("strategy_sha256", "development_data_sha256", "protocol_sha256"):
            _sha(getattr(self, field), field)
        try:
            start, end, hold_start, hold_end = map(date.fromisoformat, (
                self.development_start, self.development_end,
                self.protected_final_start, self.protected_final_end,
            ))
            frozen = datetime.fromisoformat(self.frozen_at_utc.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AblationPlanError("search dates or freeze timestamp are invalid") from exc
        if (start > end or end >= hold_start or hold_start > hold_end
                or frozen.tzinfo is None or frozen.utcoffset() is None
                or frozen.date() > start):
            raise AblationPlanError("development/final windows or pre-search freeze are invalid")
        axes = dict(self.axes)
        if len(axes) != len(self.axes) or set(axes) != set(AXES):
            raise AblationPlanError(f"exactly these axes must be frozen: {AXES}")
        if any(not isinstance(values, tuple) or not values for values in axes.values()):
            raise AblationPlanError("each ablation axis needs a nonempty tuple")
        for name, values in axes.items():
            for value in values:
                if isinstance(value, (dict, list, set, tuple)):
                    raise AblationPlanError("axis values must be scalar")
                if isinstance(value, float) and not math.isfinite(value):
                    raise AblationPlanError("axis values must be finite")
                if name in {"entry_rule", "exit_rule"} and (not isinstance(value, str) or not value.strip()):
                    raise AblationPlanError(f"{name} values must be nonempty strings")
                if name in {"holding_horizon", "portfolio_width"} and (
                    isinstance(value, bool) or not isinstance(value, int) or value <= 0
                ):
                    raise AblationPlanError(f"{name} values must be positive integers")
                if name == "turnover_limit" and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                ):
                    raise AblationPlanError("turnover_limit values must be nonnegative numbers")
            if len(set(values)) != len(values):
                raise AblationPlanError("axis values must be unique")
        count = math.prod(len(axes[name]) for name in AXES)
        if isinstance(self.max_combinations, bool) or self.max_combinations < count:
            raise AblationPlanError("predeclared search budget is smaller than the full Cartesian grid")

    @property
    def combinations(self) -> tuple[dict[str, object], ...]:
        axes = dict(self.axes)
        return tuple(dict(zip(AXES, values)) for values in product(*(axes[name] for name in AXES)))

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AblationResult:
    plan_sha256: str
    development_data_sha256: str
    combination_count: int
    session_count: int
    evaluations: tuple[dict[str, object], ...]
    outcomes: tuple[dict[str, object], ...]
    outcome_sha256: str

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def evaluate_ablations(*, plan: AblationPlan, outcomes: Iterable[Mapping[str, object]]) -> AblationResult:
    """Require every grid cell over the same training-only session/decision schedule."""
    rows = []
    combination_keys = {json.dumps(combo, sort_keys=True, separators=(",", ":")): combo
                        for combo in plan.combinations}
    grouped: dict[str, list[float]] = {key: [] for key in combination_keys}
    identities: dict[str, set[tuple[str, str]]] = {key: set() for key in combination_keys}
    schedule: set[tuple[str, str]] | None = None
    seen: set[tuple[str, str, str]] = set()
    for row in outcomes:
        if not isinstance(row, Mapping) or row.get("split") != "TRAINING":
            raise AblationPlanError("ablations may evaluate TRAINING rows only")
        combo_raw = row.get("combination")
        if not isinstance(combo_raw, Mapping):
            raise AblationPlanError("outcome requires its full parameter combination")
        combo_key = json.dumps(dict(combo_raw), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if combo_key not in combination_keys:
            raise AblationPlanError("outcome combination is not in the frozen grid")
        try:
            day = date.fromisoformat(row["session_date"]).isoformat()
            decision_id = row["decision_sha256"]
            value = row["net_alpha"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AblationPlanError("outcome needs session date, decision hash, and net alpha") from exc
        if not (plan.development_start <= day <= plan.development_end) or day >= plan.protected_final_start:
            raise AblationPlanError("outcome is outside development data or touches the protected final window")
        if (not isinstance(decision_id, str) or len(decision_id) != 64
                or any(c not in "0123456789abcdef" for c in decision_id)):
            raise AblationPlanError("decision_sha256 must be lowercase SHA-256")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AblationPlanError("net_alpha must be finite")
        key = (combo_key, day, decision_id)
        if key in seen:
            raise AblationPlanError("duplicate combination/session/decision outcome")
        seen.add(key)
        grouped[combo_key].append(float(value))
        identities[combo_key].add((day, decision_id))
        rows.append({"split": "TRAINING", "combination": combination_keys[combo_key],
                     "session_date": day, "decision_sha256": decision_id, "net_alpha": float(value)})
    if not rows:
        raise AblationPlanError("development outcomes are required")
    for observed in identities.values():
        if not observed:
            raise AblationPlanError("every predeclared combination must have outcomes, including losing results")
        if schedule is None:
            schedule = observed
        elif observed != schedule:
            raise AblationPlanError("every combination must use the same frozen session/decision identities")
    if len(seen) != len(plan.combinations) * len(schedule or ()):
        raise AblationPlanError("the full Cartesian grid and training schedule must be evaluated")
    rows.sort(key=lambda row: (str(row["session_date"]), json.dumps(row["combination"], sort_keys=True),
                               str(row["decision_sha256"])))
    evaluations = []
    for key in sorted(combination_keys):
        values = grouped[key]
        evaluations.append({"combination": combination_keys[key], "n_sessions": len(values),
                            "mean_net_alpha": mean(values), "negative_sessions": sum(v < 0 for v in values),
                            "zero_sessions": sum(v == 0 for v in values),
                            "positive_sessions": sum(v > 0 for v in values),
                            "disposition": "TRAINING_ONLY_DIAGNOSTIC"})
    data = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()
    return AblationResult(plan.sha256, plan.development_data_sha256,
                          len(plan.combinations), len(schedule or ()), tuple(evaluations), tuple(rows), data)


class AblationJournal:
    """Append-only archive of the frozen grid and all outcomes, including losses."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def record(self, plan: AblationPlan, result: AblationResult) -> str:
        if result.plan_sha256 != plan.sha256 or result.combination_count != len(plan.combinations):
            raise AblationPlanError("result does not match the frozen plan")
        if evaluate_ablations(plan=plan, outcomes=result.outcomes) != result:
            raise AblationPlanError("result does not reconcile to its complete outcome log")
        record = json.loads(json.dumps({"t": "TRAINING_ABLATION", "plan": asdict(plan),
                                        "plan_sha256": plan.sha256, "result": asdict(result),
                                        "result_sha256": result.sha256}, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False))
        prior = next((item for item in self.journal.records()
                      if item.get("plan_sha256") == plan.sha256), None)
        if prior is not None:
            if prior != record:
                raise AblationPlanError("frozen ablation results are immutable")
            return result.sha256
        self.journal.append(record)
        return result.sha256
