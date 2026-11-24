"""Pre-decision historical exit-path replay for stop, target, time and gap-risk estimates.

This module estimates conditional scenario frequencies from fully matured historical paths.
It is not a forecast calibration claim and does not authorize orders. Ambiguous OHLC bars
resolve adversely; gaps through a stop use the opening price.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .exit_policy import ExitPolicy, ExitRuleAction
from .trader_signal import (
    ExitTrigger, TradePlan, TraderSignal, validate_trade_plan_signal,
)


class ExitScenarioError(ValueError):
    """Exit replay data or policy is incomplete, malformed, or leaks future data."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal(0)


def _decimal(name: str, value: Decimal | str | int | float) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExitScenarioError(f"{name} must be decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExitScenarioError(f"{name} must be finite and positive")
    return parsed


@dataclass(frozen=True, slots=True)
class ExitReplayBar:
    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ExitScenarioError("bar timestamp must be timezone-aware")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ExitScenarioError("bar OHLC prices are internally inconsistent")


@dataclass(frozen=True, slots=True)
class ExitPathSample:
    sample_id: str
    instrument_id: str
    entry_at: datetime
    entry_price: Decimal
    completed_at: datetime
    bars: tuple[ExitReplayBar, ...]
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.sample_id.strip() or not self.instrument_id.strip():
            raise ExitScenarioError("sample and instrument identities are required")
        if (self.entry_at.tzinfo is None or self.entry_at.utcoffset() is None or
                self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None):
            raise ExitScenarioError("sample entry/completion clocks must be timezone-aware")
        if self.entry_at.astimezone(timezone.utc) >= self.completed_at.astimezone(timezone.utc):
            raise ExitScenarioError("sample completion must follow entry")
        object.__setattr__(self, "entry_price", _decimal("entry_price", self.entry_price))
        if not self.bars or any(not isinstance(bar, ExitReplayBar) for bar in self.bars):
            raise ExitScenarioError("sample needs typed replay bars")
        times = [bar.at.astimezone(timezone.utc) for bar in self.bars]
        if (times != sorted(times) or any(a >= b for a, b in zip(times, times[1:])) or
                times[0] <= self.entry_at.astimezone(timezone.utc)):
            raise ExitScenarioError("replay bars must be strictly after entry and chronologically ordered")
        if times[-1] > self.completed_at.astimezone(timezone.utc):
            raise ExitScenarioError("sample completion precedes a replay bar")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ExitScenarioError("sample source_sha256 must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class PathExitResult:
    sample_id: str
    final_exit: str
    target_was_executed: bool
    stop_was_executed: bool
    gap_stop: bool
    ambiguous_bar_count: int
    gross_pnl_inr: Decimal
    gap_loss_inr: Decimal


@dataclass(frozen=True, slots=True)
class ExitProbabilityEstimate:
    sample_count: int
    stop_exit_probability: Decimal
    target_exit_probability: Decimal
    time_exit_probability: Decimal
    gap_stop_probability: Decimal
    stop_exit_ci95: tuple[Decimal, Decimal] | None
    target_exit_ci95: tuple[Decimal, Decimal] | None
    time_exit_ci95: tuple[Decimal, Decimal] | None
    gap_stop_ci95: tuple[Decimal, Decimal] | None
    mean_gross_pnl_inr: Decimal
    mean_gap_loss_inr: Decimal
    ambiguous_bar_count: int
    outcomes: tuple[PathExitResult, ...]
    policy_sha256: str
    sample_evidence_sha256: str
    probability_label: str
    confidence_interval_method: str


def _wilson(successes: int, total: int) -> tuple[Decimal, Decimal] | None:
    if total < 30:
        return None
    z = 1.96
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return Decimal(str(max(0.0, center - margin))), Decimal(str(min(1.0, center + margin)))


def _exit_policy(policy: ExitPolicy, signal: TraderSignal, plan: TradePlan):
    if (policy.strategy_id, policy.strategy_version) != (signal.strategy_id, signal.strategy_version):
        raise ExitScenarioError("exit policy strategy identity/version differs from signal")
    stops = [r for r in policy.rules if r.trigger is ExitTrigger.HARD_STOP]
    targets = [r for r in policy.rules if r.trigger is ExitTrigger.TAKE_PROFIT]
    times = [r for r in policy.rules if r.action is ExitRuleAction.TIME_EXIT]
    if len(stops) != 1 or len(targets) != 1 or len(times) != 1:
        raise ExitScenarioError("replay currently requires one stop, one target and one time-exit rule")
    stop, target, time_rule = stops[0], targets[0], times[0]
    if (stop.trigger_price is None or target.trigger_price is None or
            stop.action is not ExitRuleAction.FULL_EXIT or
            target.action not in {ExitRuleAction.FULL_EXIT, ExitRuleAction.SCALE_OUT}):
        raise ExitScenarioError("policy needs a full hard stop and a full or partial take-profit price")
    if time_rule.after_sessions != plan.max_holding_sessions:
        raise ExitScenarioError("time-exit horizon must match the frozen trade plan")
    if stop.trigger_price != plan.stop_price or target.trigger_price != plan.target_price:
        raise ExitScenarioError("exit policy stop/target differs from the frozen trade plan")
    if target.action is ExitRuleAction.SCALE_OUT and target.quantity_fraction is None:
        raise ExitScenarioError("partial target requires an explicit scale-out fraction")
    return stop.trigger_price, target.trigger_price, target, plan.max_holding_sessions


def _pnl(side: str, entry: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    return ((exit_price - entry) if side == "BUY" else (entry - exit_price)) * quantity


def _replay(sample: ExitPathSample, *, plan: TradePlan,
            stop: Decimal, target: Decimal, target_rule, horizon: int) -> PathExitResult:
    if len(sample.bars) < horizon:
        raise ExitScenarioError("historical path is not mature for the frozen time-exit horizon")
    scale = ((plan.entry_low + plan.entry_high) / Decimal(2)) / sample.entry_price
    entry_reference = (plan.entry_low + plan.entry_high) / Decimal(2)
    qty = plan.quantity
    remaining = qty
    realized = _ZERO
    target_done = False
    stop_done = False
    gap_stop = False
    gap_loss = _ZERO
    ambiguous = 0
    final_exit = "TIME"
    long = plan.side == "BUY"
    last_close = (sample.bars[horizon - 1].close * scale)

    for bar_index, bar in enumerate(sample.bars[:horizon]):
        op, high, low, close = (bar.open * scale, bar.high * scale,
                                 bar.low * scale, bar.close * scale)
        # An open beyond a trigger establishes the first executable event for this bar.
        stop_gapped = op < stop if long else op > stop
        target_gapped = op >= target if long else op <= target
        if remaining > 0 and stop_gapped:
            fill = op
            realized += _pnl(plan.side, entry_reference, fill, remaining)
            gap_loss += (stop - fill) * remaining if long else (fill - stop) * remaining
            gap_stop = stop_done = True
            remaining = _ZERO
            final_exit = "GAP_STOP"
            break
        if remaining > 0 and not target_done and target_gapped:
            take = qty * (target_rule.quantity_fraction or Decimal(1))
            take = min(take, remaining)
            realized += _pnl(plan.side, entry_reference, target, take)
            target_done = True
            remaining -= take
            if remaining == 0:
                final_exit = "TARGET"
                break
        if remaining > 0:
            hit_stop = low <= stop if long else high >= stop
            hit_target = (not target_done) and (high >= target if long else low <= target)
            if hit_stop and hit_target:
                ambiguous += 1
            if hit_stop:
                fill = min(op, stop) if long else max(op, stop)
                realized += _pnl(plan.side, entry_reference, fill, remaining)
                if fill != stop:
                    gap_loss += (stop - fill) * remaining if long else (fill - stop) * remaining
                    gap_stop = True
                stop_done = True
                remaining = _ZERO
                final_exit = "STOP"
                break
            if hit_target:
                take = qty * (target_rule.quantity_fraction or Decimal(1))
                take = min(take, remaining)
                realized += _pnl(plan.side, entry_reference, target, take)
                target_done = True
                remaining -= take
                if remaining == 0:
                    final_exit = "TARGET"
                    break
        if bar_index == horizon - 1:
            final_exit = "TIME_AFTER_PARTIAL_TARGET" if target_done else "TIME"
            realized += _pnl(plan.side, entry_reference, close, remaining)
            remaining = _ZERO
            break
    if remaining > 0:
        realized += _pnl(plan.side, entry_reference, last_close, remaining)
        final_exit = "TIME_AFTER_PARTIAL_TARGET" if target_done else "TIME"
    return PathExitResult(sample.sample_id, final_exit, target_done, stop_done, gap_stop,
                          ambiguous, realized, gap_loss)


def estimate_exit_probabilities(*, signal: TraderSignal, plan: TradePlan, policy: ExitPolicy,
                                samples: Iterable[ExitPathSample]) -> ExitProbabilityEstimate:
    """Replay fully matured, prior-only paths through the frozen policy and report frequencies."""
    if not isinstance(signal, TraderSignal) or not isinstance(plan, TradePlan) or not isinstance(policy, ExitPolicy):
        raise ExitScenarioError("typed signal, trade plan and versioned exit policy are required")
    validate_trade_plan_signal(signal, plan)
    stop, target, target_rule, horizon = _exit_policy(policy, signal, plan)
    materialized = tuple(samples)
    if not materialized:
        raise ExitScenarioError("historical exit-path samples are required")
    if len({s.sample_id for s in materialized}) != len(materialized):
        raise ExitScenarioError("historical sample IDs must be unique")
    for sample in materialized:
        if sample.instrument_id != signal.instrument_id:
            raise ExitScenarioError("historical sample instrument differs from the signal")
        if sample.completed_at.astimezone(timezone.utc) > signal.as_of.astimezone(timezone.utc):
            raise ExitScenarioError("future or not-yet-matured path cannot inform the decision")
    outcomes = tuple(_replay(s, plan=plan, stop=stop, target=target,
                             target_rule=target_rule, horizon=horizon) for s in materialized)
    n = len(outcomes)
    stop_count = sum(x.stop_was_executed for x in outcomes)
    target_count = sum(x.target_was_executed for x in outcomes)
    time_count = sum(x.final_exit.startswith("TIME") for x in outcomes)
    gap_count = sum(x.gap_stop for x in outcomes)
    canonical = {
        "signal_id": signal.signal_id, "policy_sha256": policy.policy_hash,
        "samples": [{"sample_id": s.sample_id, "source_sha256": s.source_sha256,
                     "completed_at": s.completed_at.astimezone(timezone.utc).isoformat()}
                    for s in sorted(materialized, key=lambda row: row.sample_id)],
    }
    evidence_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode()).hexdigest()
    return ExitProbabilityEstimate(
        sample_count=n,
        stop_exit_probability=Decimal(stop_count) / Decimal(n),
        target_exit_probability=Decimal(target_count) / Decimal(n),
        time_exit_probability=Decimal(time_count) / Decimal(n),
        gap_stop_probability=Decimal(gap_count) / Decimal(n),
        stop_exit_ci95=_wilson(stop_count, n), target_exit_ci95=_wilson(target_count, n),
        time_exit_ci95=_wilson(time_count, n), gap_stop_ci95=_wilson(gap_count, n),
        mean_gross_pnl_inr=sum((x.gross_pnl_inr for x in outcomes), _ZERO) / Decimal(n),
        mean_gap_loss_inr=sum((x.gap_loss_inr for x in outcomes), _ZERO) / Decimal(n),
        ambiguous_bar_count=sum(x.ambiguous_bar_count for x in outcomes), outcomes=outcomes,
        policy_sha256=policy.policy_hash, sample_evidence_sha256=evidence_hash,
        probability_label="HISTORICAL_PRIOR_ONLY_FREQUENCY_NOT_CALIBRATED_FORECAST",
        confidence_interval_method="WILSON_95_ONLY_WHEN_N_GE_30",
    )


__all__ = ["ExitPathSample", "ExitProbabilityEstimate", "ExitReplayBar", "ExitScenarioError",
           "PathExitResult", "estimate_exit_probabilities"]
