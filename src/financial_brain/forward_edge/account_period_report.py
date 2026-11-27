"""Account equity, cash, realized/unrealized P&L and contribution period reports."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date


class AccountPeriodReportError(ValueError):
    """Account evidence does not reconcile for a cumulative or MTD report."""


@dataclass(frozen=True)
class SessionContribution:
    strategy: str
    desk: str
    instrument: str
    realized_pnl_inr: float
    unrealized_pnl_change_inr: float
    net_pnl_inr: float
    trade_count: int


@dataclass(frozen=True)
class AccountSessionSnapshot:
    session: date
    opening_equity_inr: float
    marked_equity_inr: float
    available_cash_inr: float
    position_market_value_inr: float
    realized_pnl_inr: float
    unrealized_pnl_inr: float
    external_flow_inr: float
    contributions: tuple[SessionContribution, ...]


def _aggregate(rows: tuple[AccountSessionSnapshot, ...]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    for dimension in ("strategy", "desk", "instrument"):
        totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"realized_pnl_inr": 0.0, "unrealized_pnl_change_inr": 0.0,
                     "net_pnl_inr": 0.0, "trade_count": 0.0})
        for snapshot in rows:
            for contribution in snapshot.contributions:
                row = totals[getattr(contribution, dimension)]
                row["realized_pnl_inr"] += contribution.realized_pnl_inr
                row["unrealized_pnl_change_inr"] += contribution.unrealized_pnl_change_inr
                row["net_pnl_inr"] += contribution.net_pnl_inr
                row["trade_count"] += contribution.trade_count
        out[dimension] = {key: totals[key] for key in sorted(totals)}
    return out


def _period(rows: tuple[AccountSessionSnapshot, ...], *, label: str) -> dict[str, object]:
    first, last = rows[0], rows[-1]
    contributions = _aggregate(rows)
    net_change = last.marked_equity_inr - first.opening_equity_inr - sum(
        row.external_flow_inr for row in rows)
    realized = sum(item.realized_pnl_inr for row in rows for item in row.contributions)
    unrealized_change = sum(item.unrealized_pnl_change_inr
                             for row in rows for item in row.contributions)
    total_contribution = sum(item.net_pnl_inr for row in rows for item in row.contributions)
    return {
        "period": label,
        "start_session": first.session.isoformat(),
        "end_session": last.session.isoformat(),
        "start_equity_inr": first.opening_equity_inr,
        "ending_equity_inr": last.marked_equity_inr,
        "external_flows_inr": sum(row.external_flow_inr for row in rows),
        "net_pnl_inr": net_change,
        "net_return": net_change / first.opening_equity_inr,
        "realized_pnl_inr": realized,
        "unrealized_pnl_change_inr": unrealized_change,
        "contribution_net_pnl_inr": total_contribution,
        "equity_contribution_difference_inr": net_change - total_contribution,
        "contributions": contributions,
    }


def build_account_period_report(*, snapshots: tuple[AccountSessionSnapshot, ...],
                                reconciliation_tolerance_inr: float = 0.02) -> dict[str, object]:
    """Build MTD and cumulative reports from source-complete chronological snapshots.

    Every session must include contributions reconciled to its flow-adjusted equity
    change. Available cash and market value must reproduce marked ending equity.
    """
    if not snapshots:
        raise AccountPeriodReportError("account snapshots are required")
    if not math.isfinite(reconciliation_tolerance_inr) or reconciliation_tolerance_inr < 0:
        raise AccountPeriodReportError("reconciliation tolerance must be finite and non-negative")
    dates = tuple(row.session for row in snapshots)
    if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
        raise AccountPeriodReportError("account snapshots must be unique and chronological")
    for row in snapshots:
        numbers = (row.opening_equity_inr, row.marked_equity_inr, row.available_cash_inr,
                   row.position_market_value_inr, row.realized_pnl_inr,
                   row.unrealized_pnl_inr, row.external_flow_inr)
        if any(not math.isfinite(value) for value in numbers):
            raise AccountPeriodReportError("account snapshot values must be finite")
        if row.opening_equity_inr <= 0 or row.marked_equity_inr < 0:
            raise AccountPeriodReportError("positive opening equity and non-negative marked equity are required")
        if abs(row.available_cash_inr + row.position_market_value_inr - row.marked_equity_inr) > reconciliation_tolerance_inr:
            raise AccountPeriodReportError("cash and marked positions do not reconcile to ending equity")
        for item in row.contributions:
            values = (item.realized_pnl_inr, item.unrealized_pnl_change_inr, item.net_pnl_inr)
            if (not item.strategy.strip() or not item.desk.strip() or not item.instrument.strip()
                    or any(not math.isfinite(value) for value in values)
                    or type(item.trade_count) is not int or item.trade_count < 0
                    or abs(item.realized_pnl_inr + item.unrealized_pnl_change_inr
                           - item.net_pnl_inr) > reconciliation_tolerance_inr):
                raise AccountPeriodReportError("invalid or unreconciled strategy/desk/instrument contribution")
        expected_delta = row.marked_equity_inr - row.opening_equity_inr - row.external_flow_inr
        contrib_delta = sum(item.net_pnl_inr for item in row.contributions)
        if abs(expected_delta - contrib_delta) > reconciliation_tolerance_inr:
            raise AccountPeriodReportError("session contributions do not reconcile to flow-adjusted equity change")
    month = snapshots[-1].session.month
    year = snapshots[-1].session.year
    month_rows = tuple(row for row in snapshots if row.session.year == year and row.session.month == month)
    body: dict[str, object] = {
        "as_of_session": snapshots[-1].session.isoformat(),
        "available_cash_inr": snapshots[-1].available_cash_inr,
        "marked_equity_inr": snapshots[-1].marked_equity_inr,
        "realized_pnl_inr": snapshots[-1].realized_pnl_inr,
        "unrealized_pnl_inr": snapshots[-1].unrealized_pnl_inr,
        "cash_position_reconciled": True,
        "month_to_date": _period(month_rows, label=f"{year}-{month:02d}"),
        "cumulative": _period(snapshots, label="CUMULATIVE"),
        "reconciliation_tolerance_inr": reconciliation_tolerance_inr,
        "source_rows": len(snapshots),
    }
    body["report_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return body
