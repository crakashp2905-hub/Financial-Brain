"""Budgeted development-only comparison for the fixed 20/40/60 session horizons."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Iterable

from .durable import DurableJournal

HORIZON_GRID = (20, 40, 60)


class HorizonSearchError(ValueError):
    """Invalid or out-of-budget development-only horizon comparison."""


def _sha(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise HorizonSearchError(f"{label} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class HorizonSearchPlan:
    search_id: str
    strategy_sha256: str
    data_sha256: str
    protocol_sha256: str
    development_start: str
    development_end: str
    confirmatory_start: str
    frozen_at_utc: str
    horizons: tuple[int, ...] = HORIZON_GRID
    search_budget: int = 3

    def __post_init__(self) -> None:
        if not self.search_id.strip():
            raise HorizonSearchError("search identity is required")
        _sha(self.strategy_sha256, "strategy hash")
        _sha(self.data_sha256, "development data hash")
        _sha(self.protocol_sha256, "search protocol hash")
        if self.horizons != HORIZON_GRID or self.search_budget != len(HORIZON_GRID):
            raise HorizonSearchError("search must predeclare exactly 20/40/60 within a three-horizon budget")
        try:
            start, end, confirmatory = map(date.fromisoformat,
                                            (self.development_start, self.development_end,
                                             self.confirmatory_start))
            frozen = datetime.fromisoformat(self.frozen_at_utc)
        except (TypeError, ValueError) as exc:
            raise HorizonSearchError("search dates and freeze timestamp must be canonical") from exc
        if start > end or end >= confirmatory:
            raise HorizonSearchError("development window must end before protected confirmatory data")
        if frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() > start:
            raise HorizonSearchError("horizon grid must be frozen before development evaluation begins")

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HorizonSearchResult:
    search_plan_sha256: str
    strategy_sha256: str
    data_sha256: str
    development_start: str
    development_end: str
    tried_horizons: tuple[int, ...]
    evaluations: tuple[tuple[int, int, float, float], ...]
    exploratory_best_horizon: int | None

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def evaluate_development_horizons(*, plan: HorizonSearchPlan,
                                  observations: Iterable[dict[str, object]]) -> HorizonSearchResult:
    """Summarize every predeclared horizon from development-only session net alpha rows."""
    rows = list(observations)
    grouped: dict[int, list[float]] = {horizon: [] for horizon in plan.horizons}
    identities: dict[int, set[str]] = {horizon: set() for horizon in plan.horizons}
    seen: set[tuple[str, int]] = set()
    dates: list[date] = []
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise HorizonSearchError("horizon comparison may read DEVELOPMENT rows only")
        try:
            day_text = str(row["exchange_date"])
            day = date.fromisoformat(day_text)
            horizon = row["horizon_sessions"]
            net_alpha = row["net_alpha"]
            identity = row["decision_sha256"]
            if (isinstance(horizon, bool) or not isinstance(horizon, int)
                    or isinstance(net_alpha, bool) or not isinstance(net_alpha, (int, float))):
                raise TypeError
            net_alpha = float(net_alpha)
        except (KeyError, TypeError, ValueError) as exc:
            raise HorizonSearchError("development row requires date, declared horizon, net alpha, and decision hash") from exc
        if (day < date.fromisoformat(plan.development_start)
                or day > date.fromisoformat(plan.development_end)
                or day >= date.fromisoformat(plan.confirmatory_start)
                or not math.isfinite(net_alpha) or net_alpha <= -1):
            raise HorizonSearchError("development observations are outside the declared window or non-finite")
        if horizon not in grouped:
            raise HorizonSearchError("observation horizon is outside the declared three-trial search budget")
        if (not isinstance(identity, str) or len(identity) != 64
                or any(char not in "0123456789abcdef" for char in identity)):
            raise HorizonSearchError("each horizon observation needs a decision SHA-256")
        key = (identity, horizon)
        if key in seen:
            raise HorizonSearchError("duplicate decision and horizon observation")
        seen.add(key)
        dates.append(day)
        identities[horizon].add(identity)
        grouped[horizon].append(net_alpha)
        normalized.append({"split": "DEVELOPMENT", "exchange_date": day.isoformat(),
                           "horizon_sessions": horizon, "net_alpha": net_alpha,
                           "decision_sha256": identity})
    if not rows or any(not grouped[horizon] for horizon in plan.horizons):
        raise HorizonSearchError("all three predeclared horizons need at least one development observation")
    if any(identities[horizon] != identities[plan.horizons[0]] for horizon in plan.horizons[1:]):
        raise HorizonSearchError("all horizons must use matched decision identities")
    normalized.sort(key=lambda row: (str(row["exchange_date"]), str(row["horizon_sessions"]),
                                     str(row["decision_sha256"])))
    observed_data_sha = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                                  allow_nan=False).encode("utf-8")).hexdigest()
    if observed_data_sha != plan.data_sha256:
        raise HorizonSearchError("development result rows do not match the plan's frozen data digest")
    evaluations = tuple((horizon, len(grouped[horizon]), mean(grouped[horizon]),
                         mean((1 + value) ** (252 / horizon) - 1 for value in grouped[horizon]))
                        for horizon in plan.horizons)
    # Report an exploratory training-window leader only; this value is not a promotion decision.
    leader = max(evaluations, key=lambda row: (row[3], -row[0]))[0]
    return HorizonSearchResult(
        search_plan_sha256=plan.sha256, strategy_sha256=plan.strategy_sha256,
        data_sha256=plan.data_sha256, development_start=min(dates).isoformat(),
        development_end=max(dates).isoformat(), tried_horizons=plan.horizons,
        evaluations=evaluations, exploratory_best_horizon=leader)


class HorizonSearchRegistry:
    """Durable append-only storage that records the plan and every tried horizon result."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def record(self, plan: HorizonSearchPlan, result: HorizonSearchResult) -> str:
        if result.search_plan_sha256 != plan.sha256 or result.tried_horizons != plan.horizons:
            raise HorizonSearchError("result must match the frozen search plan and report every trial")
        # Journal JSON materializes tuples as arrays; canonicalize before equality/idempotency checks.
        record = json.loads(json.dumps({"t": "DEVELOPMENT_HORIZON_SEARCH", "plan": asdict(plan),
                                        "plan_sha256": plan.sha256, "result": asdict(result),
                                        "result_sha256": result.sha256}, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False))
        prior = next((row for row in self.journal.records()
                      if row.get("plan_sha256") == plan.sha256), None)
        if prior is not None:
            if prior != record:
                raise HorizonSearchError("frozen search result is immutable")
            return result.sha256
        self.journal.append(record)
        return result.sha256
