"""Liquidity-linked capacity curves with explicit calibration and coverage gates (FE-0096)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime


class CapacityError(ValueError):
    """Invalid liquidity, cost calibration or capacity scenario."""


@dataclass(frozen=True)
class CapacityCostPoint:
    participation_rate: float
    total_execution_cost_bps: float
    observed_trades: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.participation_rate) or self.participation_rate <= 0:
            raise CapacityError("participation rate must be finite and positive")
        if not math.isfinite(self.total_execution_cost_bps) or self.total_execution_cost_bps < 0:
            raise CapacityError("execution cost must be finite and non-negative")
        if type(self.observed_trades) is not int or self.observed_trades < 1:
            raise CapacityError("cost point requires a positive observed trade count")


@dataclass(frozen=True)
class CapacityScenario:
    notional: float
    participation_rate: float
    cost_bps: float | None
    expected_net_alpha_bps: float | None
    within_visible_depth: bool
    supported_by_calibration: bool


@dataclass(frozen=True)
class CapacityReport:
    scenarios: tuple[CapacityScenario, ...]
    recommended_max_notional: float | None
    recommendation_status: str
    source_hash: str
    report_hash: str


@dataclass(frozen=True)
class CapacityUniverseInput:
    """One instrument's dated ADV/depth and observed market-impact calibration."""

    instrument_id: str
    gross_alpha_bps: float
    adv_notional: float
    visible_depth_notional: float
    notionals: tuple[float, ...]
    cost_curve: tuple[CapacityCostPoint, ...]
    source_hash: str
    adv_as_of: date
    depth_observed_at: datetime
    quote_coverage: float
    owner_attested_live_observations: bool = False


@dataclass(frozen=True)
class CapacityUniverseRow:
    instrument_id: str
    adv_age_calendar_days: int
    depth_age_seconds: float
    report: CapacityReport | None
    status: str


@dataclass(frozen=True)
class CapacityUniverseReport:
    as_of: datetime
    rows: tuple[CapacityUniverseRow, ...]
    report_hash: str


def evaluate_capacity(*, gross_alpha_bps: float, adv_notional: float,
                      visible_depth_notional: float, notionals: tuple[float, ...],
                      cost_curve: tuple[CapacityCostPoint, ...], source_hash: str,
                      owner_attested_live_observations: bool,
                      minimum_trades_per_point: int = 30,
                      minimum_quote_coverage: float = 0.95,
                      observed_quote_coverage: float = 0.0) -> CapacityReport:
    """Interpolate only within observed cost points; never invent costs beyond calibration.

    Gross alpha is held constant as an explicit scenario assumption. A size ceiling is
    conditional on caller-attested live calibration and visible depth, not an order approval.
    """
    for name, value in (("gross_alpha_bps", gross_alpha_bps), ("adv_notional", adv_notional),
                        ("visible_depth_notional", visible_depth_notional),
                        ("observed_quote_coverage", observed_quote_coverage),
                        ("minimum_quote_coverage", minimum_quote_coverage)):
        if not math.isfinite(value):
            raise CapacityError(f"{name} must be finite")
    if adv_notional <= 0 or visible_depth_notional < 0:
        raise CapacityError("ADV must be positive and visible depth cannot be negative")
    if not source_hash.strip():
        raise CapacityError("calibration source hash is required")
    if type(minimum_trades_per_point) is not int or minimum_trades_per_point < 1:
        raise CapacityError("minimum_trades_per_point must be positive")
    if not 0 < minimum_quote_coverage <= 1 or not 0 <= observed_quote_coverage <= 1:
        raise CapacityError("quote coverage must be in (0, 1]")
    if not notionals or any(not math.isfinite(n) or n <= 0 for n in notionals):
        raise CapacityError("candidate notionals must be finite and positive")
    if len(set(notionals)) != len(notionals):
        raise CapacityError("candidate notionals must be unique")
    points = tuple(sorted(cost_curve, key=lambda point: point.participation_rate))
    if len(points) < 2 or len({point.participation_rate for point in points}) != len(points):
        raise CapacityError("at least two distinct observed participation cost points are required")
    if any(a.total_execution_cost_bps > b.total_execution_cost_bps for a, b in zip(points, points[1:])):
        raise CapacityError("observed execution cost curve must be non-decreasing")

    coverage_ok = (observed_quote_coverage >= minimum_quote_coverage
                   and all(point.observed_trades >= minimum_trades_per_point for point in points))
    scenarios = []
    for notional in sorted(notionals):
        participation = notional / adv_notional
        cost = None
        supported = False
        if points[0].participation_rate <= participation <= points[-1].participation_rate:
            for left, right in zip(points, points[1:]):
                if left.participation_rate <= participation <= right.participation_rate:
                    weight = ((participation - left.participation_rate)
                              / (right.participation_rate - left.participation_rate))
                    cost = left.total_execution_cost_bps + weight * (
                        right.total_execution_cost_bps - left.total_execution_cost_bps)
                    supported = coverage_ok
                    break
            if participation == points[-1].participation_rate:
                cost = points[-1].total_execution_cost_bps
                supported = coverage_ok
        feasible_depth = notional <= visible_depth_notional
        net = gross_alpha_bps - cost if cost is not None else None
        scenarios.append(CapacityScenario(notional, participation, cost, net,
                                          feasible_depth, supported))
    eligible = [scenario.notional for scenario in scenarios
                if scenario.within_visible_depth and scenario.supported_by_calibration
                and scenario.expected_net_alpha_bps is not None and scenario.expected_net_alpha_bps > 0]
    recommendation = max(eligible) if eligible and owner_attested_live_observations else None
    if not owner_attested_live_observations:
        status = "NO_SIZE_RECOMMENDATION_FIXTURE_OR_UNATTESTED_CALIBRATION"
    elif not coverage_ok:
        status = "NO_SIZE_RECOMMENDATION_INSUFFICIENT_TRADE_OR_QUOTE_COVERAGE"
    elif recommendation is None:
        status = "NO_POSITIVE_NET_ALPHA_WITHIN_SUPPORTED_VISIBLE_DEPTH"
    else:
        status = "CONDITIONAL_MAX_NOTIONAL_CALLER_ATTESTED_CALIBRATION_ONLY"
    fields = {"scenarios": [{"notional": row.notional, "participation_rate": row.participation_rate,
                            "cost_bps": row.cost_bps, "expected_net_alpha_bps": row.expected_net_alpha_bps,
                            "within_visible_depth": row.within_visible_depth,
                            "supported_by_calibration": row.supported_by_calibration} for row in scenarios],
              "recommended_max_notional": recommendation, "recommendation_status": status,
              "source_hash": source_hash}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CapacityReport(tuple(scenarios), recommendation, status, source_hash, digest)


def report_capacity_universe(*, as_of: datetime, session: date,
                             instruments: tuple[CapacityUniverseInput, ...],
                             max_adv_age_calendar_days: int = 7,
                             max_depth_age_seconds: float = 2.0) -> CapacityUniverseReport:
    """Build a reproducible, fail-closed capacity report over dated instrument inputs.

    This is a calculation/reporting layer: caller-provided attestation is not itself proof
    of data entitlement or provenance. Stale ADV/depth produces a row without a capacity
    recommendation, instead of silently reusing stale liquidity.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise CapacityError("capacity report as_of must be timezone aware")
    if max_adv_age_calendar_days < 0 or not math.isfinite(max_depth_age_seconds) or max_depth_age_seconds < 0:
        raise CapacityError("capacity freshness limits must be non-negative")
    if not instruments:
        raise CapacityError("capacity report needs at least one instrument")
    ids = [item.instrument_id.strip() for item in instruments]
    if any(not instrument_id for instrument_id in ids) or len(set(ids)) != len(ids):
        raise CapacityError("instrument IDs must be non-blank and unique")

    rows: list[CapacityUniverseRow] = []
    for item in sorted(instruments, key=lambda candidate: candidate.instrument_id):
        adv_age = (session - item.adv_as_of).days
        depth_age = (as_of - item.depth_observed_at).total_seconds()
        if item.depth_observed_at.tzinfo is None or item.depth_observed_at.utcoffset() is None:
            raise CapacityError("depth observation time must be timezone aware")
        if adv_age < 0 or depth_age < 0:
            raise CapacityError("future ADV or depth observations are not allowed")
        if adv_age > max_adv_age_calendar_days:
            rows.append(CapacityUniverseRow(item.instrument_id, adv_age, depth_age, None,
                                            "NO_RECOMMENDATION_STALE_ADV"))
            continue
        if depth_age > max_depth_age_seconds:
            rows.append(CapacityUniverseRow(item.instrument_id, adv_age, depth_age, None,
                                            "NO_RECOMMENDATION_STALE_DEPTH"))
            continue
        report = evaluate_capacity(
            gross_alpha_bps=item.gross_alpha_bps, adv_notional=item.adv_notional,
            visible_depth_notional=item.visible_depth_notional, notionals=item.notionals,
            cost_curve=item.cost_curve, source_hash=item.source_hash,
            owner_attested_live_observations=item.owner_attested_live_observations,
            observed_quote_coverage=item.quote_coverage)
        rows.append(CapacityUniverseRow(item.instrument_id, adv_age, depth_age, report,
                                        report.recommendation_status))

    payload = {
        "as_of": as_of.isoformat(), "session": session.isoformat(),
        "rows": [{"instrument_id": row.instrument_id,
                  "adv_age_calendar_days": row.adv_age_calendar_days,
                  "depth_age_seconds": row.depth_age_seconds, "status": row.status,
                  "report_hash": row.report.report_hash if row.report else None}
                 for row in rows],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CapacityUniverseReport(as_of, tuple(rows), digest)
