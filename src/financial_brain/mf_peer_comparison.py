"""Same-category, equal-risk comparisons for mutual-fund NAV return series."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import stdev
from typing import Iterable


@dataclass(frozen=True)
class MFPeerComparison:
    status: str
    category: str | None
    common_observations: int
    left_annualized_volatility: float | None
    right_annualized_volatility: float | None
    target_annualized_volatility: float | None
    left_equal_risk_return: float | None
    right_equal_risk_return: float | None
    left_benchmark_is_tri: bool
    right_benchmark_is_tri: bool
    left_ter_pct: float | None
    right_ter_pct: float | None
    reasons: tuple[str, ...]


def _returns(rows: Iterable[tuple[date, float]]) -> dict[date, float]:
    values: dict[date, float] = {}
    for day, raw in rows:
        if not isinstance(day, date) or day in values:
            raise ValueError("return observations need unique date keys")
        value = float(raw)
        if not (value == value and abs(value) != float("inf")) or value <= -1:
            raise ValueError("daily returns must be finite and greater than -100%")
        values[day] = value
    return values


def compare_same_category(*, left_category: str | None, right_category: str | None,
                           left_returns: Iterable[tuple[date, float]],
                           right_returns: Iterable[tuple[date, float]],
                           left_benchmark_return_type: str,
                           right_benchmark_return_type: str,
                           left_ter_pct: float | None = None,
                           right_ter_pct: float | None = None,
                           minimum_observations: int = 30) -> MFPeerComparison:
    """Compare on common dates after de-risking both NAV return series to the lower volatility.

    The method never scales a fund up above its observed risk. NAV returns are net of fund
    expenses already; TER must not be subtracted a second time. A non-TRI benchmark is
    explicitly disclosed as unavailable for a total-return comparison.
    """
    if minimum_observations < 2:
        raise ValueError("minimum_observations must be at least two")
    left_type, right_type = (left_benchmark_return_type.upper(),
                             right_benchmark_return_type.upper())
    for ter in (left_ter_pct, right_ter_pct):
        if ter is not None and (not float(ter) >= 0 or not float(ter) < 100):
            raise ValueError("TER must be between 0 and 100 percent")
    left_tri, right_tri = left_type == "TRI", right_type == "TRI"
    left_label = " ".join((left_category or "").upper().split())
    right_label = " ".join((right_category or "").upper().split())
    empty = MFPeerComparison("REFUSED", None, 0, None, None, None, None, None,
                             left_tri, right_tri, left_ter_pct, right_ter_pct, ())
    if not left_label or not right_label:
        return MFPeerComparison(**{**empty.__dict__, "reasons": ("CATEGORY_UNKNOWN",)})
    if left_label != right_label:
        return MFPeerComparison(**{**empty.__dict__,
                                  "reasons": ("CROSS_CATEGORY_COMPARISON_BLOCKED",)})
    left, right = _returns(left_returns), _returns(right_returns)
    days = sorted(left.keys() & right.keys())
    if len(days) < minimum_observations:
        return MFPeerComparison("INSUFFICIENT_DATA", left_label, len(days), None, None, None,
                                None, None, left_tri, right_tri, left_ter_pct, right_ter_pct,
                                ("INSUFFICIENT_COMMON_OBSERVATIONS",))
    lv = [left[day] for day in days]
    rv = [right[day] for day in days]
    left_vol, right_vol = stdev(lv) * sqrt(252), stdev(rv) * sqrt(252)
    target = min(left_vol, right_vol)
    if left_vol <= 0 or right_vol <= 0:
        return MFPeerComparison("INSUFFICIENT_DATA", left_label, len(days), left_vol,
                                right_vol, target, None, None, left_tri, right_tri,
                                left_ter_pct, right_ter_pct,
                                ("RISK_NOT_ESTIMABLE",))

    def scaled_growth(series: list[float], vol: float) -> float:
        factor = target / vol
        growth = 1.0
        for ret in series:
            growth *= 1 + ret * factor
        return growth - 1

    reasons = () if left_tri and right_tri else ("BENCHMARK_NOT_TRI",)
    return MFPeerComparison("COMPARABLE", left_label, len(days), left_vol, right_vol,
                            target, scaled_growth(lv, left_vol),
                            scaled_growth(rv, right_vol), left_tri, right_tri,
                            left_ter_pct, right_ter_pct, reasons)
