"""Fail-closed expected net-P&L estimates for quote-backed trade scenarios.

The output is a research estimate, never an execution authorization. Model scenarios are
kept separate from out-of-time calibration samples so a heuristic probability cannot be
presented as a calibrated forecast.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from financial_brain.costs.india import Side
from financial_brain.forward_edge.cost_schedule import CostSchedule
from .trader_signal import SignalSide, TraderSignal


class NetEVError(ValueError):
    """Expected-value inputs are incomplete, inconsistent, stale, or invalid."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal(0)
IST = ZoneInfo("Asia/Kolkata")
_LIQUIDITY_BUCKETS = {"mega", "large", "mid", "small", "micro"}


def _decimal(name: str, raw: Decimal | str | int | float, *, nonnegative: bool = False) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NetEVError(f"{name} must be decimal") from exc
    if not value.is_finite() or (nonnegative and value < 0):
        qualifier = "finite and non-negative" if nonnegative else "finite"
        raise NetEVError(f"{name} must be {qualifier}")
    return value


@dataclass(frozen=True, slots=True)
class NetEVOutcome:
    """One mutually exclusive fill or no-fill scenario at a fixed signal size."""

    name: str
    probability: Decimal
    gross_pnl_inr: Decimal
    entry_turnover_inr: Decimal
    exit_turnover_inr: Decimal
    spread_cost_inr: Decimal
    filled_quantity: Decimal
    visible_quantity: Decimal
    liquidity_bucket: str
    historical_gross_pnl_samples_inr: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise NetEVError("scenario name is required")
        for name in ("probability", "entry_turnover_inr", "exit_turnover_inr", "spread_cost_inr",
                     "filled_quantity", "visible_quantity"):
            value = _decimal(name, getattr(self, name), nonnegative=True)
            object.__setattr__(self, name, value)
        object.__setattr__(self, "gross_pnl_inr", _decimal("gross_pnl_inr", self.gross_pnl_inr))
        if self.probability > 1:
            raise NetEVError("scenario probability cannot exceed one")
        if self.filled_quantity > self.visible_quantity:
            raise NetEVError("scenario fill exceeds visible liquidity")
        if self.liquidity_bucket not in _LIQUIDITY_BUCKETS:
            raise NetEVError("scenario requires a recognized sourced liquidity bucket")
        if self.filled_quantity == 0:
            if (self.entry_turnover_inr != 0 or self.exit_turnover_inr != 0 or
                    self.spread_cost_inr != 0 or self.gross_pnl_inr != 0):
                raise NetEVError("no-fill scenario must have zero turnover and P&L")
        elif (self.entry_turnover_inr <= 0 or self.exit_turnover_inr <= 0 or
              self.visible_quantity <= 0):
            raise NetEVError("filled round-trip scenario requires entry/exit turnover and visible liquidity")
        samples = tuple(_decimal("historical gross P&L sample", x) for x in
                        self.historical_gross_pnl_samples_inr)
        object.__setattr__(self, "historical_gross_pnl_samples_inr", samples)


@dataclass(frozen=True, slots=True)
class NetEVCalibration:
    """Caller-supplied, versioned out-of-time calibration assertion and provenance."""

    calibration_id: str
    evidence_sha256: str
    out_of_time_validated: bool

    def __post_init__(self) -> None:
        if not self.calibration_id.strip():
            raise NetEVError("calibration_id is required")
        if not _SHA256.fullmatch(self.evidence_sha256):
            raise NetEVError("calibration evidence must be lowercase SHA-256")
        if type(self.out_of_time_validated) is not bool:
            raise NetEVError("out_of_time_validated must be boolean")


@dataclass(frozen=True, slots=True)
class FillScenario:
    """Decision-time quote evidence, scenario distribution and optional calibration."""

    outcomes: tuple[NetEVOutcome, ...]
    instrument_id: str
    bid: Decimal
    ask: Decimal
    quote_event_at: datetime
    quote_available_at: datetime
    max_quote_age_ms: int
    quote_source_id: str
    quote_source_sha256: str
    calibration: NetEVCalibration | None = None

    def __post_init__(self) -> None:
        if not self.outcomes or any(not isinstance(x, NetEVOutcome) for x in self.outcomes):
            raise NetEVError("at least one typed fill/no-fill outcome is required")
        if not self.instrument_id.strip():
            raise NetEVError("quote instrument_id is required")
        bid = _decimal("bid", self.bid, nonnegative=True)
        ask = _decimal("ask", self.ask, nonnegative=True)
        if bid <= 0 or ask < bid:
            raise NetEVError("an executable positive bid/ask quote is required")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        if len({x.name for x in self.outcomes}) != len(self.outcomes):
            raise NetEVError("scenario names must be unique")
        total_probability = sum((x.probability for x in self.outcomes), _ZERO)
        if total_probability != Decimal(1):
            raise NetEVError("scenario probabilities must sum exactly to one")
        if (self.quote_event_at.tzinfo is None or self.quote_event_at.utcoffset() is None or
                self.quote_available_at.tzinfo is None or self.quote_available_at.utcoffset() is None):
            raise NetEVError("quote event and availability clocks must be timezone-aware")
        if self.quote_available_at.astimezone(timezone.utc) < self.quote_event_at.astimezone(timezone.utc):
            raise NetEVError("quote cannot be available before its source event")
        if type(self.max_quote_age_ms) is not int or self.max_quote_age_ms <= 0:
            raise NetEVError("max_quote_age_ms must be a positive integer")
        if not self.quote_source_id.strip():
            raise NetEVError("quote_source_id is required")
        if not _SHA256.fullmatch(self.quote_source_sha256):
            raise NetEVError("quote_source_sha256 must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class NetEVDistributionPoint:
    scenario: str
    probability: Decimal
    net_pnl_inr: Decimal


@dataclass(frozen=True, slots=True)
class NetEVEstimate:
    expected_gross_pnl_inr: Decimal
    expected_cost_inr: Decimal
    expected_net_pnl_inr: Decimal
    net_pnl_distribution: tuple[NetEVDistributionPoint, ...]
    loss_distribution: tuple[NetEVDistributionPoint, ...]
    calibrated_sample_mean_net_pnl_inr: Decimal | None
    mean_confidence_interval_95_inr: tuple[Decimal, Decimal] | None
    confidence_interval_method: str
    calibration_status: str
    calibration_id: str | None
    calibration_evidence_sha256: str | None
    calibration_sample_count: int
    evidence_tier: str
    cost_schedule_id: str
    cost_schedule_version: str
    cost_schedule_sha256: str
    quote_source_id: str
    quote_source_sha256: str
    is_execution_authorization: bool = False


def net_ev(signal: TraderSignal, fill_scenario: FillScenario,
           cost_model: CostSchedule) -> NetEVEstimate:
    """Calculate scenario-weighted INR net EV with costs and quote timing enforced.

    Missing costs, invalid liquidity, a stale/unavailable quote, or an ineligible signal
    are errors rather than zero-cost defaults. A confidence interval is emitted only when
    a versioned out-of-time calibration assertion and at least 30 empirical observations
    are supplied; it is a clearly labeled normal-approximation interval.
    """
    if not isinstance(signal, TraderSignal):
        raise NetEVError("a typed TraderSignal is required")
    if not isinstance(fill_scenario, FillScenario):
        raise NetEVError("a typed FillScenario is required")
    if not isinstance(cost_model, CostSchedule):
        raise NetEVError("an effective-dated, source-versioned CostSchedule is required")
    if signal.side not in {SignalSide.BUY, SignalSide.SELL_SHORT}:
        raise NetEVError("WATCH/NO_TRADE signals cannot produce a trade EV")
    if fill_scenario.instrument_id != signal.instrument_id:
        raise NetEVError("quote instrument differs from signal instrument")
    decision_at = signal.decision_at.astimezone(timezone.utc)
    event_at = fill_scenario.quote_event_at.astimezone(timezone.utc)
    available_at = fill_scenario.quote_available_at.astimezone(timezone.utc)
    if available_at > decision_at or event_at > decision_at:
        raise NetEVError("quote was not actually available by the signal decision")
    if (decision_at - event_at).total_seconds() * 1000 > fill_scenario.max_quote_age_ms:
        raise NetEVError("source quote exceeds the frozen maximum age")
    trade_date = signal.decision_at.astimezone(IST).date()
    if (cost_model.effective_from > trade_date or
            (cost_model.effective_to is not None and cost_model.effective_to < trade_date)):
        raise NetEVError("cost schedule is not effective on the signal decision date")

    opening_side = Side.BUY if signal.side is SignalSide.BUY else Side.SELL
    closing_side = Side.SELL if opening_side is Side.BUY else Side.BUY
    net_outcomes: list[NetEVDistributionPoint] = []
    costs_by_name: dict[str, Decimal] = {}
    empirical_net_samples: dict[str, list[float]] = {}
    for outcome in fill_scenario.outcomes:
        if outcome.filled_quantity == 0:
            cost = _ZERO
        else:
            entry = cost_model.one_way(day=trade_date,
                                       turnover=float(outcome.entry_turnover_inr),
                                       side=opening_side, bucket=outcome.liquidity_bucket)
            exit_cost = cost_model.one_way(day=trade_date,
                                           turnover=float(outcome.exit_turnover_inr),
                                           side=closing_side, bucket=outcome.liquidity_bucket)
            cost = _decimal("modeled cost", entry["total"] + exit_cost["total"], nonnegative=True)
        cost += outcome.spread_cost_inr
        net = outcome.gross_pnl_inr - cost
        costs_by_name[outcome.name] = cost
        net_outcomes.append(NetEVDistributionPoint(outcome.name, outcome.probability, net))
        empirical_net_samples[outcome.name] = [float(sample - cost)
                                                for sample in outcome.historical_gross_pnl_samples_inr]

    expected_gross = sum((x.probability * x.gross_pnl_inr for x in fill_scenario.outcomes), _ZERO)
    expected_cost = sum((x.probability * costs_by_name[x.name] for x in fill_scenario.outcomes), _ZERO)
    expected_net = sum((x.probability * x.net_pnl_inr for x in net_outcomes), _ZERO)
    sample_count = sum(len(samples) for samples in empirical_net_samples.values())
    has_scenario_samples = all(
        outcome.probability == 0 or len(empirical_net_samples[outcome.name]) >= 2
        for outcome in fill_scenario.outcomes
    )
    calibrated = (fill_scenario.calibration is not None and
                  fill_scenario.calibration.out_of_time_validated and
                  sample_count >= 30 and has_scenario_samples)
    interval = None
    calibrated_mean = None
    method = "UNAVAILABLE_UNCALIBRATED_OR_SAMPLE_TOO_SMALL"
    if calibrated:
        weighted_mean = 0.0
        mean_variance = 0.0
        for outcome in fill_scenario.outcomes:
            samples = empirical_net_samples[outcome.name]
            if outcome.probability == 0:
                continue
            if not all(math.isfinite(x) for x in samples):
                raise NetEVError("calibration samples must be finite")
            scenario_mean = sum(samples) / len(samples)
            weighted_mean += float(outcome.probability) * scenario_mean
            scenario_variance = sum((x - scenario_mean) ** 2 for x in samples) / (len(samples) - 1)
            mean_variance += float(outcome.probability) ** 2 * scenario_variance / len(samples)
        if not math.isfinite(weighted_mean) or not math.isfinite(mean_variance):
            raise NetEVError("calibration samples must be finite")
        margin = 1.96 * math.sqrt(mean_variance)
        calibrated_mean = Decimal(str(weighted_mean))
        interval = (Decimal(str(weighted_mean - margin)), Decimal(str(weighted_mean + margin)))
        method = "NORMAL_APPROXIMATION_IID_95_CONDITIONAL_SCENARIO_PROBABILITIES"
    return NetEVEstimate(
        expected_gross_pnl_inr=expected_gross,
        expected_cost_inr=expected_cost,
        expected_net_pnl_inr=expected_net,
        net_pnl_distribution=tuple(net_outcomes),
        loss_distribution=tuple(x for x in net_outcomes if x.net_pnl_inr < 0),
        calibrated_sample_mean_net_pnl_inr=calibrated_mean,
        mean_confidence_interval_95_inr=interval,
        confidence_interval_method=method,
        calibration_status="OUT_OF_TIME_CALIBRATED" if calibrated else "UNCALIBRATED_SCENARIO",
        calibration_id=(fill_scenario.calibration.calibration_id
                        if calibrated and fill_scenario.calibration else None),
        calibration_evidence_sha256=(fill_scenario.calibration.evidence_sha256
                                     if calibrated and fill_scenario.calibration else None),
        calibration_sample_count=sample_count if calibrated else 0,
        evidence_tier=signal.evidence_tier.value,
        cost_schedule_id=cost_model.schedule_id,
        cost_schedule_version=cost_model.version,
        cost_schedule_sha256=cost_model.digest(),
        quote_source_id=fill_scenario.quote_source_id,
        quote_source_sha256=fill_scenario.quote_source_sha256,
    )


__all__ = ["FillScenario", "NetEVCalibration", "NetEVDistributionPoint", "NetEVError", "NetEVEstimate",
           "NetEVOutcome", "net_ev"]
