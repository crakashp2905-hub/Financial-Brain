"""Discrete paper capacity sizing with sourced costs, tax, margin and settlement limits.

All schedules are supplied by the caller and must be bound to a source hash. The model
is a research proposal: it does not fetch broker/exchange schedules or create orders.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Mapping

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CapacityOptimizationError(ValueError):
    """Invalid candidate, source schedule or portfolio resource constraint."""


def _hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CapacityOptimizationError(f"{label} must be a lowercase SHA-256")


def _finite(value: float, label: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise CapacityOptimizationError(f"{label} must be finite numeric data")
    return float(value)


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapacityOptimizationError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CapacityCandidate:
    """One buy-side candidate, priced in INR per share with integer share lots.

    `settlement_cash_flows_per_share` uses positive values for base-contract cash debits
    and negative values for credits on each ISO settlement date, before the cost fields
    below. To reserve cash conservatively, variable fees and taxes are charged on the first
    settlement date, fixed fees there when an order is activated, and financing on the last
    settlement date. Tax rates and amounts are caller-supplied account/jurisdiction
    assumptions, never hard-coded defaults.
    """

    candidate_id: str
    as_of: datetime
    price_per_share_inr: float
    shares_per_lot: int
    maximum_lots: int
    expected_gross_pnl_per_share_inr: float
    transaction_cost_per_share_inr: float
    transaction_tax_per_share_inr: float
    positive_gain_tax_rate: float
    fixed_order_charge_inr: float
    margin_per_share_inr: float
    financing_principal_per_share_inr: float
    financing_rate_annual: float
    financing_days: int
    settlement_cash_flows_per_share: Mapping[str, float]
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise CapacityOptimizationError("candidate ID is required")
        object.__setattr__(self, "as_of", _utc(self.as_of, "candidate as_of"))
        if isinstance(self.shares_per_lot, bool) or not isinstance(self.shares_per_lot, int) or self.shares_per_lot < 1:
            raise CapacityOptimizationError("shares_per_lot must be a positive whole-share count")
        if isinstance(self.maximum_lots, bool) or not isinstance(self.maximum_lots, int) or self.maximum_lots < 1:
            raise CapacityOptimizationError("maximum_lots must be a positive integer bound")
        if isinstance(self.financing_days, bool) or not isinstance(self.financing_days, int) or self.financing_days < 0:
            raise CapacityOptimizationError("financing_days must be a non-negative integer")
        nonnegative = (
            ("price", self.price_per_share_inr),
            ("transaction cost", self.transaction_cost_per_share_inr),
            ("transaction tax", self.transaction_tax_per_share_inr),
            ("fixed order charge", self.fixed_order_charge_inr),
            ("margin", self.margin_per_share_inr),
            ("financing principal", self.financing_principal_per_share_inr),
            ("financing rate", self.financing_rate_annual),
        )
        for label, value in nonnegative:
            if _finite(value, label) < 0:
                raise CapacityOptimizationError(f"{label} must be non-negative")
        if self.price_per_share_inr <= 0:
            raise CapacityOptimizationError("share price must be positive")
        gross = _finite(self.expected_gross_pnl_per_share_inr, "expected gross P&L")
        if gross <= -self.price_per_share_inr:
            raise CapacityOptimizationError("expected loss cannot exceed the share purchase price")
        tax_rate = _finite(self.positive_gain_tax_rate, "positive-gain tax rate")
        if not 0 <= tax_rate < 1:
            raise CapacityOptimizationError("positive-gain tax rate must be in [0, 1)")
        if not isinstance(self.settlement_cash_flows_per_share, Mapping) or not self.settlement_cash_flows_per_share:
            raise CapacityOptimizationError("at least one sourced settlement cash-flow date is required")
        flows: dict[str, float] = {}
        for raw_day, raw_flow in self.settlement_cash_flows_per_share.items():
            try:
                day = date.fromisoformat(raw_day).isoformat()
            except (TypeError, ValueError) as exc:
                raise CapacityOptimizationError("settlement dates must use ISO YYYY-MM-DD") from exc
            if day != raw_day:
                raise CapacityOptimizationError("settlement dates must be canonical ISO YYYY-MM-DD")
            if date.fromisoformat(day) < self.as_of.date():
                raise CapacityOptimizationError("settlement dates cannot precede the candidate as-of date")
            flows[day] = _finite(raw_flow, f"settlement flow {day}")
        cumulative_flow = 0.0
        for day in sorted(flows):
            cumulative_flow += flows[day]
            if cumulative_flow < -1e-9:
                raise CapacityOptimizationError("long-only settlement cash flows cannot require short-sale proceeds")
        if sum(flows.values()) <= 0:
            raise CapacityOptimizationError("long-only candidate must require positive net settlement cash")
        _hash(self.source_sha256, "candidate schedule source hash")
        object.__setattr__(self, "settlement_cash_flows_per_share", MappingProxyType(dict(sorted(flows.items()))))

    @property
    def financing_cost_per_share_inr(self) -> float:
        return (self.financing_principal_per_share_inr * self.financing_rate_annual
                * self.financing_days / 365.0)

    @property
    def gain_tax_per_share_inr(self) -> float:
        return max(0.0, self.expected_gross_pnl_per_share_inr) * self.positive_gain_tax_rate

    @property
    def expected_net_pnl_per_share_inr(self) -> float:
        return (self.expected_gross_pnl_per_share_inr - self.transaction_cost_per_share_inr
                - self.transaction_tax_per_share_inr - self.gain_tax_per_share_inr
                - self.financing_cost_per_share_inr)


@dataclass(frozen=True, slots=True)
class CapacityAllocation:
    shares_by_candidate: Mapping[str, int]
    lots_by_candidate: Mapping[str, int]
    expected_gross_pnl_inr: float
    expected_transaction_costs_inr: float
    expected_taxes_inr: float
    expected_financing_inr: float
    expected_net_pnl_inr: float
    gross_notional_inr: float
    margin_used_inr: float
    settlement_cash_used_by_date_inr: Mapping[str, float]
    schedule_set_sha256: str
    allocation_sha256: str
    promotion_allowed: bool = False


def optimize_capacity(*, candidates: tuple[CapacityCandidate, ...], as_of: datetime,
                      cash_available_by_settlement_date_inr: Mapping[str, float],
                      margin_budget_inr: float, gross_leverage_limit_inr: float
                      ) -> CapacityAllocation:
    """Maximize expected net INR P&L under whole-share, cash-day, margin and leverage caps.

    Each candidate has an integer lot variable and a binary order-activation variable so
    fixed per-order charges are counted exactly once. Cash constraints apply cumulatively
    over settlement dates; all candidate inputs must be frozen at or before `as_of`.
    """
    now = _utc(as_of, "as_of")
    if not candidates or len({row.candidate_id for row in candidates}) != len(candidates):
        raise CapacityOptimizationError("candidate set must be non-empty with unique IDs")
    ordered = tuple(sorted(candidates, key=lambda row: row.candidate_id))
    if any(row.as_of != now for row in ordered):
        raise CapacityOptimizationError("candidate assumptions must share the allocation as-of clock")
    if not isinstance(cash_available_by_settlement_date_inr, Mapping) or not cash_available_by_settlement_date_inr:
        raise CapacityOptimizationError("cash availability must cover settlement dates")
    cash: dict[str, float] = {}
    for raw_day, amount in cash_available_by_settlement_date_inr.items():
        try:
            day = date.fromisoformat(raw_day).isoformat()
        except (TypeError, ValueError) as exc:
            raise CapacityOptimizationError("cash dates must use ISO YYYY-MM-DD") from exc
        cash[day] = _finite(amount, f"cash availability {day}")
        if cash[day] < 0:
            raise CapacityOptimizationError("cash availability cannot be negative")
    all_days = sorted({day for row in ordered for day in row.settlement_cash_flows_per_share})
    if not set(all_days) <= set(cash):
        raise CapacityOptimizationError("cash availability is required for every sourced settlement date")
    margin_budget = _finite(margin_budget_inr, "margin budget")
    leverage_limit = _finite(gross_leverage_limit_inr, "gross leverage limit")
    if margin_budget < 0 or leverage_limit < 0:
        raise CapacityOptimizationError("margin and leverage budgets cannot be negative")

    count = len(ordered)
    # Variables are [lot quantities..., active-order binaries...].
    objective = np.zeros(2 * count)
    integrality = np.ones(2 * count, dtype=int)
    lower = np.zeros(2 * count)
    upper = np.zeros(2 * count)
    rows: list[np.ndarray] = []
    row_lower: list[float] = []
    row_upper: list[float] = []
    for index, candidate in enumerate(ordered):
        share_count = candidate.shares_per_lot
        net_lot = candidate.expected_net_pnl_per_share_inr * share_count
        # Fixed charge is represented on the binary activation variable.
        objective[index] = -net_lot
        objective[count + index] = candidate.fixed_order_charge_inr
        upper[index] = candidate.maximum_lots
        upper[count + index] = 1
        link_upper = np.zeros(2 * count)
        link_upper[index] = 1
        link_upper[count + index] = -candidate.maximum_lots
        rows.append(link_upper)
        row_lower.append(-np.inf)
        row_upper.append(0.0)
        link_lower = np.zeros(2 * count)
        link_lower[index] = -1
        link_lower[count + index] = 1
        rows.append(link_lower)
        row_lower.append(-np.inf)
        row_upper.append(0.0)
    leverage = np.zeros(2 * count)
    margin = np.zeros(2 * count)
    for index, candidate in enumerate(ordered):
        lots_shares = candidate.shares_per_lot
        leverage[index] = candidate.price_per_share_inr * lots_shares
        margin[index] = candidate.margin_per_share_inr * lots_shares
    rows.extend((leverage, margin))
    row_lower.extend((-np.inf, -np.inf))
    row_upper.extend((leverage_limit, margin_budget))
    # Settlement budgets are peak cumulative cash uses, not just trade-date cash.
    for settlement_day in all_days:
        settlement = np.zeros(2 * count)
        for index, candidate in enumerate(ordered):
            flow = sum(value for day, value in candidate.settlement_cash_flows_per_share.items()
                       if day <= settlement_day)
            first_day = min(candidate.settlement_cash_flows_per_share)
            last_day = max(candidate.settlement_cash_flows_per_share)
            variable_cost_and_tax = (candidate.transaction_cost_per_share_inr
                                     + candidate.transaction_tax_per_share_inr
                                     + candidate.gain_tax_per_share_inr)
            financing = candidate.financing_cost_per_share_inr
            if settlement_day >= first_day:
                flow += variable_cost_and_tax
            if settlement_day >= last_day:
                flow += financing
            settlement[index] = flow * candidate.shares_per_lot
            if settlement_day >= first_day:
                settlement[count + index] = candidate.fixed_order_charge_inr
        rows.append(settlement)
        row_lower.append(-np.inf)
        row_upper.append(cash[settlement_day])

    result = milp(c=objective, integrality=integrality,
                  bounds=Bounds(lower, upper),
                  constraints=LinearConstraint(np.asarray(rows), np.asarray(row_lower), np.asarray(row_upper)),
                  options={"presolve": True})
    if not result.success or result.x is None:
        raise CapacityOptimizationError(f"capacity allocation infeasible: {result.message}")
    lots = {row.candidate_id: int(round(result.x[index])) for index, row in enumerate(ordered)
            if round(result.x[index]) > 0}
    shares = {row.candidate_id: lots[row.candidate_id] * row.shares_per_lot for row in ordered
              if row.candidate_id in lots}
    gross_pnl = sum(shares.get(row.candidate_id, 0) * row.expected_gross_pnl_per_share_inr for row in ordered)
    taxes = sum(shares.get(row.candidate_id, 0)
                * (row.transaction_tax_per_share_inr + row.gain_tax_per_share_inr) for row in ordered)
    variable_costs = sum(shares.get(row.candidate_id, 0) * row.transaction_cost_per_share_inr for row in ordered)
    fixed_costs = sum(row.fixed_order_charge_inr for row in ordered if row.candidate_id in lots)
    financing = sum(shares.get(row.candidate_id, 0) * row.financing_cost_per_share_inr for row in ordered)
    gross_notional = sum(shares.get(row.candidate_id, 0) * row.price_per_share_inr for row in ordered)
    margin_used = sum(shares.get(row.candidate_id, 0) * row.margin_per_share_inr for row in ordered)
    settlement_used = {
        day: sum(
            shares.get(row.candidate_id, 0)
            * (sum(value for flow_day, value in row.settlement_cash_flows_per_share.items() if flow_day <= day)
               + (row.transaction_cost_per_share_inr + row.transaction_tax_per_share_inr
                  + row.gain_tax_per_share_inr if day >= min(row.settlement_cash_flows_per_share) else 0.0)
               + (row.financing_cost_per_share_inr if day >= max(row.settlement_cash_flows_per_share) else 0.0))
            + (row.fixed_order_charge_inr if row.candidate_id in lots
               and day >= min(row.settlement_cash_flows_per_share) else 0.0)
            for row in ordered
        )
        for day in all_days
    }
    net = gross_pnl - variable_costs - fixed_costs - taxes - financing
    schedule_panel = [{"candidate_id": row.candidate_id, "source_sha256": row.source_sha256,
                      "as_of": row.as_of.isoformat(), "lots": lots.get(row.candidate_id, 0),
                      "shares_per_lot": row.shares_per_lot,
                      "expected_net_pnl_per_share_inr": row.expected_net_pnl_per_share_inr,
                      "settlement_cash_flows_per_share": dict(row.settlement_cash_flows_per_share)}
                     for row in ordered]
    panel_digest = hashlib.sha256(json.dumps(schedule_panel, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode()).hexdigest()
    report = {"lots": lots, "shares": shares, "gross_pnl": gross_pnl, "costs": variable_costs + fixed_costs,
              "taxes": taxes, "financing": financing, "net_pnl": net, "gross_notional": gross_notional,
              "margin_used": margin_used, "settlement_cash_used": settlement_used,
              "schedule_set_sha256": panel_digest, "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return CapacityAllocation(MappingProxyType(shares), MappingProxyType(lots), gross_pnl,
                              variable_costs + fixed_costs, taxes, financing, net, gross_notional,
                              margin_used, MappingProxyType(settlement_used), panel_digest, digest)


__all__ = ["CapacityAllocation", "CapacityCandidate", "CapacityOptimizationError", "optimize_capacity"]
