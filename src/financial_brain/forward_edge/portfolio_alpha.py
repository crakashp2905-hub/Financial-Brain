"""Daily capital-weighted, after-cost portfolio alpha accounting (FE-0081)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping


class PortfolioAlphaError(ValueError):
    """Portfolio daily inputs are incomplete or economically invalid."""


def _finite(value: float, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise PortfolioAlphaError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class PortfolioDay:
    session: date
    opening_equity: float
    instrument_gross_pnl: Mapping[str, float]
    charges: Mapping[str, float]
    financing_cost: float
    cash_interest_pnl: float
    benchmark_return: float
    gross_exposure: float
    net_exposure: float

    def __post_init__(self) -> None:
        if _finite(self.opening_equity, "opening_equity") <= 0:
            raise PortfolioAlphaError("opening equity must be positive")
        if not math.isfinite(_finite(self.benchmark_return, "benchmark_return")):
            raise PortfolioAlphaError("benchmark return must be finite")
        if _finite(self.gross_exposure, "gross_exposure") < 0:
            raise PortfolioAlphaError("gross exposure cannot be negative")
        if abs(_finite(self.net_exposure, "net_exposure")) > self.gross_exposure + 1e-8:
            raise PortfolioAlphaError("absolute net exposure cannot exceed gross exposure")
        if _finite(self.financing_cost, "financing_cost") < 0:
            raise PortfolioAlphaError("financing cost cannot be negative")
        for name, value in self.instrument_gross_pnl.items():
            if not name.strip():
                raise PortfolioAlphaError("instrument P&L keys cannot be blank")
            _finite(value, f"instrument_gross_pnl[{name}]")
        for name, value in self.charges.items():
            if not name.strip() or _finite(value, f"charges[{name}]") < 0:
                raise PortfolioAlphaError("charge names are required and charge values cannot be negative")
        _finite(self.cash_interest_pnl, "cash_interest_pnl")


@dataclass(frozen=True)
class PortfolioAlphaReport:
    days: tuple[dict[str, object], ...]
    sessions: int
    cash_only_sessions: int
    cumulative_net_return: float
    cumulative_benchmark_return: float
    cumulative_net_alpha: float
    report_hash: str

    def body(self) -> dict[str, object]:
        return {"days": list(self.days), "sessions": self.sessions,
                "cash_only_sessions": self.cash_only_sessions,
                "cumulative_net_return": self.cumulative_net_return,
                "cumulative_benchmark_return": self.cumulative_benchmark_return,
                "cumulative_net_alpha": self.cumulative_net_alpha,
                "report_hash": self.report_hash}


def compute_portfolio_alpha(days: tuple[PortfolioDay, ...], expected_sessions: tuple[date, ...]) -> PortfolioAlphaReport:
    """Calculate daily NAV returns and compounded benchmark-relative after-cost alpha.

    Every expected exchange session must be supplied, including days with no trades. The
    opening-equity denominator keeps leveraged cash/futures/options books comparable.
    """
    if not expected_sessions or len(set(expected_sessions)) != len(expected_sessions):
        raise PortfolioAlphaError("expected session calendar must be explicit and unique")
    if tuple(sorted(expected_sessions)) != expected_sessions:
        raise PortfolioAlphaError("expected sessions must be chronological")
    actual = tuple(day.session for day in days)
    if len(set(actual)) != len(actual) or tuple(sorted(actual)) != actual:
        raise PortfolioAlphaError("portfolio days must be unique and chronological")
    if actual != expected_sessions:
        missing = sorted(set(expected_sessions) - set(actual))
        extra = sorted(set(actual) - set(expected_sessions))
        raise PortfolioAlphaError(f"session series does not match expected calendar; missing={missing}, extra={extra}")

    report_rows = []
    nav_factor = benchmark_factor = 1.0
    cash_only = 0
    for day in days:
        gross_pnl = sum(_finite(value, "gross P&L") for value in day.instrument_gross_pnl.values())
        total_charges = sum(_finite(value, "charge") for value in day.charges.values())
        net_pnl = gross_pnl + day.cash_interest_pnl - total_charges - day.financing_cost
        portfolio_return = net_pnl / day.opening_equity
        if portfolio_return <= -1.0:
            raise PortfolioAlphaError("daily portfolio loss cannot exhaust or exceed opening equity")
        if day.benchmark_return <= -1.0:
            raise PortfolioAlphaError("benchmark return cannot be at or below -100%")
        nav_factor *= 1.0 + portfolio_return
        benchmark_factor *= 1.0 + day.benchmark_return
        is_cash_only = not day.instrument_gross_pnl and day.gross_exposure == 0
        cash_only += int(is_cash_only)
        report_rows.append({"session": day.session.isoformat(), "opening_equity": day.opening_equity,
                            "gross_pnl": gross_pnl, "cash_interest_pnl": day.cash_interest_pnl,
                            "charges": total_charges, "financing_cost": day.financing_cost,
                            "net_pnl": net_pnl, "portfolio_return": portfolio_return,
                            "benchmark_return": day.benchmark_return,
                            "net_alpha": portfolio_return - day.benchmark_return,
                            "gross_exposure": day.gross_exposure, "net_exposure": day.net_exposure,
                            "cash_only": is_cash_only})
    body = {"days": report_rows, "sessions": len(days), "cash_only_sessions": cash_only,
            "cumulative_net_return": nav_factor - 1.0,
            "cumulative_benchmark_return": benchmark_factor - 1.0,
            "cumulative_net_alpha": nav_factor - benchmark_factor}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return PortfolioAlphaReport(tuple(report_rows), len(days), cash_only, nav_factor - 1.0,
                                benchmark_factor - 1.0, nav_factor - benchmark_factor, digest)
