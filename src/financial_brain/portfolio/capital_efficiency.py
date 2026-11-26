"""Capital-efficiency metrics from a frozen canonical portfolio-ledger interval."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .canonical_ledger import CanonicalPortfolioLedger, EventKind


class CapitalEfficiencyError(ValueError):
    """Capital-efficiency interval lacks a defensible denominator or source events."""


@dataclass(frozen=True)
class CapitalEfficiencyReport:
    net_pnl_inr: Decimal
    risk_budget_inr: Decimal
    net_rupees_per_rupee_risk: Decimal
    maximum_concurrent_cash_use_inr: Decimal
    annualized_turnover_inr: Decimal
    deployed_capital_return: Decimal
    total_capital_return: Decimal
    total_capital_inr: Decimal
    deployed_capital_inr: Decimal
    interval_days: int
    source_event_count: int
    source_sha256: str
    report_sha256: str


def _money(raw: object, name: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise CapitalEfficiencyError(f"{name} must be a decimal amount") from exc
    if not value.is_finite():
        raise CapitalEfficiencyError(f"{name} must be finite")
    return value


def report_capital_efficiency(*, ledger: CanonicalPortfolioLedger, account_id: str,
                              start: datetime, end: datetime, risk_budget_inr: Decimal | str | int,
                              minimum_deployed_capital_inr: Decimal | str | int = 1) -> CapitalEfficiencyReport:
    """Measure risk-normalized P&L, peak cash use, turnover and two capital returns.

    Opening-balance CASH_FLOW metadata must provide ``capital_type`` as TOTAL or DEPLOYED.
    Net P&L uses fills marked to interval-end marks plus fee/cash events; unresolved positions
    or missing terminal marks fail closed. No returns are annualized for intervals under a day.
    """
    if (not account_id.strip() or any(value.tzinfo is None or value.utcoffset() is None
                                      for value in (start, end)) or end <= start):
        raise CapitalEfficiencyError("account and timezone-aware positive interval are required")
    risk = _money(risk_budget_inr, "risk budget")
    min_deployed = _money(minimum_deployed_capital_inr, "minimum deployed capital")
    if risk <= 0 or min_deployed <= 0:
        raise CapitalEfficiencyError("risk and minimum deployed capital must be positive")
    rows = ledger.events(account_id, as_of=end.astimezone(timezone.utc).isoformat())
    start_iso = start.astimezone(timezone.utc).isoformat()
    rows = [row for row in rows if isinstance(row.get("occurred_at"), str)
            and start_iso <= str(row["occurred_at"])]
    if not rows:
        raise CapitalEfficiencyError("no canonical ledger events in the requested interval")
    total_capital = deployed_capital = Decimal(0)
    openings = set()
    for row in rows:
        if row["event_kind"] != EventKind.CASH_FLOW.value:
            continue
        metadata = row.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("cash_flow_type") == "OPENING_BALANCE":
            capital_type = metadata.get("capital_type")
            if capital_type not in {"TOTAL", "DEPLOYED"} or capital_type in openings:
                raise CapitalEfficiencyError("one explicit TOTAL and DEPLOYED opening-capital event are required")
            openings.add(capital_type)
            amount = _money(row.get("cash_delta"), "opening capital")
            if amount <= 0:
                raise CapitalEfficiencyError("opening capital must be positive")
            if capital_type == "TOTAL":
                total_capital = amount
            else:
                deployed_capital = amount
    if openings != {"TOTAL", "DEPLOYED"} or deployed_capital < min_deployed or total_capital < deployed_capital:
        raise CapitalEfficiencyError("valid total and deployed opening capital denominators are required")

    positions: dict[tuple[str, str], Decimal] = {}
    cost_basis: dict[tuple[str, str], Decimal] = {}
    marks: dict[tuple[str, str], Decimal] = {}
    cash_balance = peak_cash_use = turnover = fees = other_cash = Decimal(0)
    realized = Decimal(0)
    unresolved = False
    fills = 0
    for row in rows:
        kind = row["event_kind"]
        if kind == EventKind.UNRESOLVED_POSITION.value:
            unresolved = True
        elif kind == EventKind.FILL.value:
            fills += 1
            key = (str(row["asset_class"]), str(row["instrument_id"]))
            qty, price, mult = (_money(row[name], name) for name in ("quantity", "price", "multiplier"))
            amount = qty * price * mult
            turnover += amount
            current = positions.get(key, Decimal(0))
            basis = cost_basis.get(key, Decimal(0))
            if row["side"] == "BUY":
                positions[key] = current + qty
                cost_basis[key] = basis + amount
                cash_balance -= amount
            else:
                if qty > current:
                    raise CapitalEfficiencyError("sell fill exceeds long inventory; short cash use is unsupported")
                average = basis / current if current else Decimal(0)
                realized += amount - average * qty
                positions[key] = current - qty
                cost_basis[key] = basis - average * qty
                cash_balance += amount
            peak_cash_use = max(peak_cash_use, deployed_capital - cash_balance)
        elif kind == EventKind.MARK.value:
            key = (str(row["asset_class"]), str(row["instrument_id"]))
            marks[key] = _money(row["price"], "mark")
        elif kind == EventKind.FEE.value:
            fee_delta = _money(row["cash_delta"], "fee")
            fees += -fee_delta
            cash_balance += fee_delta
            peak_cash_use = max(peak_cash_use, deployed_capital - cash_balance)
        elif kind == EventKind.CASH_FLOW.value:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict) and metadata.get("cash_flow_type") == "OPENING_BALANCE":
                if metadata.get("capital_type") == "DEPLOYED":
                    cash_balance += _money(row.get("cash_delta"), "deployed opening capital")
            else:
                flow = _money(row.get("cash_delta"), "cash flow")
                other_cash += flow
                cash_balance += flow
                peak_cash_use = max(peak_cash_use, deployed_capital - cash_balance)
    if unresolved:
        raise CapitalEfficiencyError("unresolved positions make net P&L incomplete")
    unrealized = Decimal(0)
    for key, quantity in positions.items():
        if quantity == 0:
            continue
        if key not in marks:
            raise CapitalEfficiencyError(f"open position {key} lacks an interval-end mark")
        unrealized += quantity * (marks[key] - cost_basis[key] / quantity)
    net_pnl = realized + unrealized - fees + other_cash
    deployed_return = net_pnl / deployed_capital
    total_return = net_pnl / total_capital
    days = max(1, (end.astimezone(timezone.utc).date() - start.astimezone(timezone.utc).date()).days)
    annualized = turnover * Decimal(365) / Decimal(days)
    source_body = [{"source_system": row["source_system"], "source_event_id": row["source_event_id"],
                    "source_sha256": row["source_sha256"]} for row in rows]
    source_hash = hashlib.sha256(json.dumps(source_body, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    fields = {"net_pnl_inr": str(net_pnl), "risk_budget_inr": str(risk),
              "net_rupees_per_rupee_risk": str(net_pnl / risk),
              "maximum_concurrent_cash_use_inr": str(peak_cash_use),
              "annualized_turnover_inr": str(annualized),
              "deployed_capital_return": str(deployed_return), "total_capital_return": str(total_return),
              "total_capital_inr": str(total_capital), "deployed_capital_inr": str(deployed_capital),
              "interval_days": days, "source_event_count": len(rows), "source_sha256": source_hash}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CapitalEfficiencyReport(net_pnl, risk, net_pnl / risk, peak_cash_use, annualized,
                                   deployed_return, total_return, total_capital, deployed_capital,
                                   days, len(rows), source_hash, digest)
