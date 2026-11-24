"""Immutable decision-time gross/cost/risk/cash comparison receipt for paper trades."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from financial_brain.costs.india import Side
from financial_brain.forward_edge.cost_schedule import CostSchedule
from .execution_feasibility import ExecutionEvidence, FeasibilityState, assess_execution_feasibility
from .entry_constraints import EntryConstraintDecision, EntryConstraintState
from .trader_signal import SignalSide, TradePlan, TraderSignal, validate_trade_plan_signal


class TradeReceiptError(ValueError):
    """Receipt inputs lack current sourced, costed, or comparable evidence."""


_IST = ZoneInfo("Asia/Kolkata")
_BUCKETS = {"mega", "large", "mid", "small", "micro"}


def _decimal(name: str, raw: Decimal | str | int) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TradeReceiptError(f"{name} must be decimal") from exc
    if not value.is_finite():
        raise TradeReceiptError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class CashBenchmark:
    benchmark_id: str
    version: str
    annual_yield: Decimal
    effective_from: date
    effective_to: date | None
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.benchmark_id, self.version, self.source_ref)):
            raise TradeReceiptError("cash benchmark identity and source are required")
        rate = _decimal("annual_yield", self.annual_yield)
        if rate < 0:
            raise TradeReceiptError("annual_yield cannot be negative")
        object.__setattr__(self, "annual_yield", rate)
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise TradeReceiptError("cash benchmark effective period is invalid")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise TradeReceiptError("cash benchmark source hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class CostLegReceipt:
    scenario: str
    side: str
    turnover_inr: Decimal
    components_inr: tuple[tuple[str, Decimal], ...]
    total_cost_inr: Decimal


@dataclass(frozen=True, slots=True)
class TradeEconomicsReceipt:
    signal_id: str
    instrument_id: str
    decision_at_utc: str
    entry_reference_price: Decimal
    target_price: Decimal
    stop_price: Decimal
    quantity: Decimal
    gross_target_pnl_inr: Decimal
    gross_stop_loss_inr: Decimal
    target_cost_legs: tuple[CostLegReceipt, CostLegReceipt]
    stop_cost_legs: tuple[CostLegReceipt, CostLegReceipt]
    spread_cost_target_inr: Decimal
    spread_cost_stop_inr: Decimal
    total_target_cost_inr: Decimal
    total_stop_cost_inr: Decimal
    net_target_pnl_inr: Decimal
    risk_at_stop_inr: Decimal
    break_even_exit_price: Decimal | None
    break_even_method: str
    cash_benchmark_id: str
    cash_benchmark_version: str
    cash_opportunity_cost_inr: Decimal
    net_target_minus_cash_inr: Decimal
    clears_cash_hurdle: bool
    cost_schedule_id: str
    cost_schedule_version: str
    cost_schedule_sha256: str
    cost_schedule_source_sha256: str
    cash_benchmark_source_sha256: str
    quote_source_id: str
    quote_source_sha256: str
    entry_constraint_receipt_sha256: str
    evidence_is_uncertain: bool
    receipt_sha256: str


def _cost_leg(*, label: str, price: Decimal, quantity: Decimal, side: Side,
              schedule: CostSchedule, day: date, bucket: str) -> CostLegReceipt:
    turnover = price * quantity
    row = schedule.one_way(day=day, turnover=float(turnover), side=side, bucket=bucket)
    names = ("stt", "exchange", "sebi", "stamp", "brokerage", "gst", "dp", "impact")
    components = tuple((name, Decimal(str(row[name]))) for name in names)
    total = Decimal(str(row["total"]))
    return CostLegReceipt(label, side.value, turnover, components, total)


def build_trade_economics_receipt(*, signal: TraderSignal, plan: TradePlan,
                                  execution: ExecutionEvidence,
                                  entry_decision: EntryConstraintDecision,
                                  cost_schedule: CostSchedule,
                                  cash_benchmark: CashBenchmark, liquidity_bucket: str,
                                  holding_calendar_days: int) -> TradeEconomicsReceipt:
    """Bind target/stop INR economics, cost-model provenance and a cash hurdle to one signal.

    Cost-model outputs use the repository India schedule. Spread is separately charged
    at half the observed full spread for each entry/exit leg. Break-even is iterated to
    convergence using the same cost schedule and marked as an estimate.
    """
    validate_trade_plan_signal(signal, plan)
    if entry_decision.state is not EntryConstraintState.ELIGIBLE:
        raise TradeReceiptError("entry constraints did not admit this quote")
    if entry_decision.signal_id != signal.signal_id:
        raise TradeReceiptError("entry receipt belongs to another signal")
    if signal.side not in {SignalSide.BUY, SignalSide.SELL_SHORT}:
        raise TradeReceiptError("only directional BUY/SELL_SHORT plans have a trade receipt")
    if execution.quote_source_id is None or execution.quote_source_hash is None:
        raise TradeReceiptError("quote source identity and hash are required")
    if execution.quote_event_at is None or execution.quote_received_at is None or execution.observed_at is None:
        raise TradeReceiptError("quote event, receipt, and observation clocks are required")
    if execution.observed_at.astimezone(_IST).date() != signal.decision_at.astimezone(_IST).date():
        raise TradeReceiptError("quote observation must be on the signal decision session")
    decision_at = signal.decision_at.astimezone(timezone.utc)
    clocks = (execution.quote_event_at, execution.quote_received_at, execution.observed_at)
    if any(clock.astimezone(timezone.utc) > decision_at for clock in clocks):
        raise TradeReceiptError("quote evidence was unavailable by signal decision time")
    if entry_decision.evaluated_at.astimezone(timezone.utc) != execution.observed_at.astimezone(timezone.utc):
        raise TradeReceiptError("entry receipt and quote observation clocks differ")
    if entry_decision.evaluated_at.astimezone(timezone.utc) != execution.observed_at.astimezone(timezone.utc):
        raise TradeReceiptError("entry receipt and quote observation clocks differ")
    if entry_decision.executable_entry_price != (
            execution.ask if signal.side is SignalSide.BUY else execution.bid):
        raise TradeReceiptError("entry receipt price differs from the source quote")
    if assess_execution_feasibility(execution).state is not FeasibilityState.ACTIONABLE:
        raise TradeReceiptError("current executable quote failed feasibility checks")
    if execution.spread_bps is None:
        raise TradeReceiptError("spread evidence is required")
    if liquidity_bucket not in _BUCKETS:
        raise TradeReceiptError("a sourced liquidity bucket is required")
    if type(holding_calendar_days) is not int or holding_calendar_days < 1:
        raise TradeReceiptError("holding_calendar_days must be a positive integer")

    day = signal.decision_at.astimezone(_IST).date()
    if day < cash_benchmark.effective_from or (cash_benchmark.effective_to and day > cash_benchmark.effective_to):
        raise TradeReceiptError("cash benchmark is not effective on the decision date")
    if day < cost_schedule.effective_from or (cost_schedule.effective_to and day > cost_schedule.effective_to):
        raise TradeReceiptError("cost schedule is not effective on the decision date")
    entry = (plan.entry_low + plan.entry_high) / Decimal(2)
    quantity = plan.quantity
    long = signal.side is SignalSide.BUY
    opening_side = Side.BUY if long else Side.SELL
    closing_side = Side.SELL if long else Side.BUY
    gross_target = (plan.target_price - entry if long else entry - plan.target_price) * quantity
    gross_stop = (entry - plan.stop_price if long else plan.stop_price - entry) * quantity
    entry_target = _cost_leg(label="entry", price=entry, quantity=quantity, side=opening_side,
                             schedule=cost_schedule, day=day, bucket=liquidity_bucket)
    target_exit = _cost_leg(label="target_exit", price=plan.target_price, quantity=quantity,
                            side=closing_side, schedule=cost_schedule, day=day, bucket=liquidity_bucket)
    entry_stop = _cost_leg(label="entry", price=entry, quantity=quantity, side=opening_side,
                           schedule=cost_schedule, day=day, bucket=liquidity_bucket)
    stop_exit = _cost_leg(label="stop_exit", price=plan.stop_price, quantity=quantity,
                          side=closing_side, schedule=cost_schedule, day=day, bucket=liquidity_bucket)
    spread_rate = execution.spread_bps / Decimal(20_000)
    target_spread = (entry + plan.target_price) * quantity * spread_rate
    stop_spread = (entry + plan.stop_price) * quantity * spread_rate
    target_cost = entry_target.total_cost_inr + target_exit.total_cost_inr + target_spread
    stop_cost = entry_stop.total_cost_inr + stop_exit.total_cost_inr + stop_spread
    net_target = gross_target - target_cost
    risk_at_stop = gross_stop + stop_cost
    cash_cost = entry * quantity * cash_benchmark.annual_yield * Decimal(holding_calendar_days) / Decimal(365)

    # Iterate the fee-dependent exit price to a stable break-even quote.
    breakeven: Decimal | None = entry
    for _ in range(12):
        current_price = breakeven
        if current_price is None:
            break
        exit_leg = _cost_leg(label="break_even_exit", price=current_price, quantity=quantity,
                             side=closing_side, schedule=cost_schedule, day=day, bucket=liquidity_bucket)
        spread = (entry + current_price) * quantity * spread_rate
        total = entry_target.total_cost_inr + exit_leg.total_cost_inr + spread
        next_price = entry + total / quantity if long else entry - total / quantity
        if next_price <= 0:
            breakeven = None
            break
        if abs(next_price - current_price) < Decimal("0.00000001"):
            breakeven = next_price
            break
        breakeven = next_price

    decision_at_utc = signal.decision_at.astimezone(timezone.utc).isoformat()
    payload = {
        "signal_id": signal.signal_id, "instrument_id": signal.instrument_id,
        "decision_at_utc": decision_at_utc,
        "entry": str(entry), "target": str(plan.target_price), "stop": str(plan.stop_price),
        "quantity": str(quantity), "gross_target": str(gross_target), "gross_stop_loss": str(gross_stop),
        "target_cost": str(target_cost), "stop_cost": str(stop_cost), "net_target": str(net_target),
        "risk_at_stop": str(risk_at_stop), "breakeven": str(breakeven) if breakeven is not None else None,
        "cash_benchmark": [cash_benchmark.benchmark_id, cash_benchmark.version, cash_benchmark.source_sha256],
        "cash_cost": str(cash_cost), "cost_schedule": [cost_schedule.schedule_id, cost_schedule.version,
                                                             cost_schedule.digest()],
        "cash_source_sha256": cash_benchmark.source_sha256,
        "liquidity_bucket": liquidity_bucket,
        "entry_constraint_receipt_sha256": entry_decision.receipt_sha256,
        "quote": [execution.quote_source_id, execution.quote_source_hash,
                  str(execution.bid), str(execution.ask), str(execution.spread_bps),
                  execution.quote_event_at.astimezone(timezone.utc).isoformat(),
                  execution.quote_received_at.astimezone(timezone.utc).isoformat(),
                  execution.observed_at.astimezone(timezone.utc).isoformat()],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return TradeEconomicsReceipt(
        signal.signal_id, signal.instrument_id, decision_at_utc, entry,
        plan.target_price, plan.stop_price, quantity, gross_target, gross_stop,
        (entry_target, target_exit), (entry_stop, stop_exit), target_spread, stop_spread,
        target_cost, stop_cost, net_target, risk_at_stop, breakeven,
        "ITERATED_COST_SCHEDULE_AND_HALF_SPREAD_ESTIMATE", cash_benchmark.benchmark_id,
        cash_benchmark.version, cash_cost, net_target - cash_cost, net_target > cash_cost,
        cost_schedule.schedule_id, cost_schedule.version, cost_schedule.digest(),
        cost_schedule.source_sha256, cash_benchmark.source_sha256, execution.quote_source_id,
        execution.quote_source_hash, entry_decision.receipt_sha256, True, digest,
    )


__all__ = ["CashBenchmark", "CostLegReceipt", "TradeEconomicsReceipt", "TradeReceiptError",
           "build_trade_economics_receipt"]
