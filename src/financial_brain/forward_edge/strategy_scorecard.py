"""Horizon-aligned cross-strategy scorecards with dependence-aware intervals (FE-0120)."""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path

from financial_brain.forward_edge.bootstrap import (
    BootstrapReport,
    DatedAlpha,
    stationary_block_bootstrap_mean,
)
from financial_brain.forward_edge.capacity import CapacityReport


class StrategyScorecardError(ValueError):
    """Invalid or insufficiently aligned comparison cohort."""


@dataclass(frozen=True)
class StrategyCohort:
    strategy_id: str
    cohort_id: str
    horizon_sessions: int
    observations: tuple[DatedAlpha, ...]
    capacity_report: CapacityReport | None

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.cohort_id.strip():
            raise StrategyScorecardError("strategy and cohort identity are required")
        if type(self.horizon_sessions) is not int or self.horizon_sessions < 1:
            raise StrategyScorecardError("horizon_sessions must be a positive integer")
        if len(self.observations) < 2:
            raise StrategyScorecardError("each scorecard series needs at least two observations")


@dataclass(frozen=True)
class ScoredStrategy:
    strategy_id: str
    bootstrap: BootstrapReport
    capacity_max_notional: float | None
    capacity_status: str


@dataclass(frozen=True)
class ScorecardGroup:
    cohort_id: str
    horizon_sessions: int
    sessions: tuple[str, ...]
    strategies: tuple[ScoredStrategy, ...]


@dataclass(frozen=True)
class StrategyScorecard:
    groups: tuple[ScorecardGroup, ...]
    report_hash: str
    promotion_inference_allowed: bool = False


def build_strategy_scorecard(*, cohorts: tuple[StrategyCohort, ...],
                             expected_block_length: float = 3.0,
                             resamples: int = 2_000,
                             confidence_level: float = 0.95) -> StrategyScorecard:
    """Compare only identical cohort, horizon and session sets; never cross-rank horizons."""
    if not cohorts:
        raise StrategyScorecardError("at least one strategy cohort is required")
    identities = [(item.strategy_id, item.cohort_id, item.horizon_sessions,
                   tuple(row.session.isoformat() for row in item.observations)) for item in cohorts]
    if len(set(identities)) != len(identities):
        raise StrategyScorecardError("duplicate strategy cohort series")
    groups: dict[tuple[str, int, tuple[str, ...]], list[StrategyCohort]] = {}
    for item in cohorts:
        dates = tuple(row.session.isoformat() for row in item.observations)
        groups.setdefault((item.cohort_id, item.horizon_sessions, dates), []).append(item)
    output = []
    for (cohort_id, horizon, sessions), values in sorted(groups.items()):
        scored = []
        for item in sorted(values, key=lambda row: row.strategy_id):
            seed = int(hashlib.sha256(item.strategy_id.encode()).hexdigest()[:8], 16)
            interval = stationary_block_bootstrap_mean(
                item.observations, expected_block_length=expected_block_length,
                resamples=resamples, confidence_level=confidence_level, seed=seed)
            capacity = item.capacity_report
            scored.append(ScoredStrategy(
                item.strategy_id, interval,
                capacity.recommended_max_notional if capacity else None,
                capacity.recommendation_status if capacity else "CAPACITY_NOT_REPORTED"))
        output.append(ScorecardGroup(cohort_id, horizon, sessions, tuple(scored)))
    payload = {"groups": [{"cohort_id": group.cohort_id,
                           "horizon_sessions": group.horizon_sessions,
                           "sessions": list(group.sessions),
                           "strategies": [{"strategy_id": row.strategy_id,
                                           "bootstrap_hash": row.bootstrap.report_hash,
                                           "capacity_max_notional": row.capacity_max_notional,
                                           "capacity_status": row.capacity_status}
                                          for row in group.strategies]}
                          for group in output], "promotion_inference_allowed": False}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return StrategyScorecard(tuple(output), digest, False)


def render_strategy_scorecard(scorecard: StrategyScorecard, output: Path) -> Path:
    """Render confidence intervals and capacity without ranking across horizon groups."""
    groups_html = []
    for group in scorecard.groups:
        rows = []
        for strategy in group.strategies:
            ci = strategy.bootstrap
            capacity = (f"{strategy.capacity_max_notional:,.2f}"
                        if strategy.capacity_max_notional is not None else "No supported size")
            rows.append("<tr><td>" + html.escape(strategy.strategy_id) + "</td><td>"
                        + f"{ci.observations}</td><td>{ci.mean_net_alpha:.6f}</td><td>"
                        + f"[{ci.ci_lower:.6f}, {ci.ci_upper:.6f}]</td><td>"
                        + html.escape(capacity) + "</td><td>"
                        + html.escape(strategy.capacity_status) + "</td></tr>")
        groups_html.append("<section><h2>" + html.escape(group.cohort_id) + " · horizon "
                           + str(group.horizon_sessions) + " session(s)</h2><p>Matched sessions: "
                           + html.escape(", ".join(group.sessions))
                           + "</p><table><thead><tr><th>Strategy</th><th>N</th><th>Mean net alpha</th>"
                           "<th>Stationary-block CI</th><th>Conditional capacity</th><th>Capacity status</th>"
                           "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></section>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Horizon aligned strategy scorecard</title><style>body{font:14px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
            "section{margin:1.2rem 0;padding:1rem;border:1px solid #ddd;border-radius:6px}"
            "table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:.5rem;border-bottom:1px solid #ddd}"
            "code{overflow-wrap:anywhere}</style><main><h1>Horizon-aligned strategy scorecard</h1>"
            "<p>Only strategies with identical cohort, horizon and sessions share a group. Confidence intervals are descriptive; "
            "they do not authorize promotion or strategy selection.</p>" + "".join(groups_html)
            + "<p>Report SHA-256: <code>" + scorecard.report_hash + "</code></p></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
