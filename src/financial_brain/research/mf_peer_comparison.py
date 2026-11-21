"""Same-category, equal-risk descriptive comparisons for MF strategies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class MfPeerComparisonError(ValueError):
    """Peer observations are not comparable under the declared contract."""


def _number(value: Decimal | str | int, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfPeerComparisonError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise MfPeerComparisonError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class MfPeerPeriod:
    scheme_code: str
    category_id: str
    benchmark_tri: str
    benchmark_effective_from: date
    benchmark_effective_to: date | None
    period_start: date
    period_end: date
    total_return_percent: Decimal | str | int
    annualized_volatility_percent: Decimal | str | int
    source_sha256: str
    known_at: datetime

    def __post_init__(self) -> None:
        for name in ("scheme_code", "category_id", "benchmark_tri"):
            if not getattr(self, name).strip():
                raise MfPeerComparisonError(f"{name} is required")
        if self.period_end <= self.period_start:
            raise MfPeerComparisonError("period_end must follow period_start")
        if self.benchmark_effective_from > self.period_start or (
            self.benchmark_effective_to is not None
            and self.benchmark_effective_to < self.period_end
        ):
            raise MfPeerComparisonError("benchmark TRI version does not cover the full period")
        ret = _number(self.total_return_percent, "total return")
        vol = _number(self.annualized_volatility_percent, "volatility")
        if vol <= 0:
            raise MfPeerComparisonError("annualized volatility must be positive")
        object.__setattr__(self, "total_return_percent", ret)
        object.__setattr__(self, "annualized_volatility_percent", vol)
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise MfPeerComparisonError("source_sha256 must be lowercase SHA-256")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise MfPeerComparisonError("known_at must be timezone-aware")


@dataclass(frozen=True)
class RiskNormalizedPeer:
    scheme_code: str
    category_id: str
    benchmark_tri: str
    total_return_percent: Decimal
    volatility_percent: Decimal
    risk_normalized_return_percent: Decimal


def compare_same_category_equal_risk(
    periods: tuple[MfPeerPeriod, ...],
    *,
    risk_budget_percent: Decimal | str | int,
    risk_free_return_percent: Decimal | str | int,
) -> tuple[RiskNormalizedPeer, ...]:
    """Scale peers down to one common volatility budget; reject category mixing.

    This is a descriptive volatility-normalized statistic, not an investable return
    forecast. A source-declared category and benchmark version must cover the complete
    comparison window. No category is guessed from a scheme name.
    """
    if len(periods) < 2:
        raise MfPeerComparisonError("at least two peers are required")
    if len({x.scheme_code for x in periods}) != len(periods):
        raise MfPeerComparisonError("scheme codes must be unique")
    category_ids = {x.category_id for x in periods}
    windows = {(x.period_start, x.period_end) for x in periods}
    known_at = {x.known_at for x in periods}
    if len(category_ids) != 1:
        raise MfPeerComparisonError("cross-category comparison is prohibited")
    if len(windows) != 1:
        raise MfPeerComparisonError("peer returns must use the same period")
    if len(known_at) != 1:
        raise MfPeerComparisonError("peer returns must share the same as-known timestamp")

    budget = _number(risk_budget_percent, "risk budget")
    cash_return = _number(risk_free_return_percent, "risk-free return")
    if budget <= 0:
        raise MfPeerComparisonError("risk budget must be positive")
    if budget > min(_number(x.annualized_volatility_percent, "volatility")
                    for x in periods):
        raise MfPeerComparisonError("risk budget cannot scale a peer up with leverage")

    results = []
    for period in periods:
        ret = Decimal(period.total_return_percent)
        vol = Decimal(period.annualized_volatility_percent)
        normalized = cash_return + (ret - cash_return) * budget / vol
        results.append(RiskNormalizedPeer(
            scheme_code=period.scheme_code, category_id=period.category_id,
            benchmark_tri=period.benchmark_tri, total_return_percent=ret,
            volatility_percent=vol, risk_normalized_return_percent=normalized,
        ))
    return tuple(sorted(results, key=lambda row: (-row.risk_normalized_return_percent,
                                                   row.scheme_code)))


__all__ = [
    "MfPeerComparisonError", "MfPeerPeriod", "RiskNormalizedPeer",
    "compare_same_category_equal_risk",
]
