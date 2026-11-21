"""Prior-trend requirement for double tops and bottoms, and the candidate denominator (ALG-033, ALG-034, ALG-040)."""

from __future__ import annotations

from financial_brain.patterns.detectors import Formation
from financial_brain.strategies.structure import formation_denominator, require_prior_trend


def _f(start: int, known: int, confirmed: int | None = None, failed: int | None = None) -> Formation:
    return Formation("double_top", "1", start, known, confirmed_index=confirmed, failed_index=failed)


def test_double_top_needs_a_prior_uptrend() -> None:  # ALG-033
    closes = [100.0 + i * 0.5 for i in range(30)] + [100.0] * 10
    up = require_prior_trend([_f(25, 30)], closes, direction="UP", lookback=10)
    assert len(up) == 1
    down = [100.0 - i * 0.5 for i in range(30)] + [100.0] * 10
    assert require_prior_trend([_f(25, 30)], down, direction="UP", lookback=10) == []


def test_double_bottom_needs_a_prior_downtrend() -> None:  # ALG-034
    closes = [100.0 - i * 0.5 for i in range(30)] + [100.0] * 10
    assert len(require_prior_trend([_f(25, 30)], closes, direction="DOWN", lookback=10)) == 1
    assert require_prior_trend([_f(3, 8)], closes, direction="DOWN", lookback=10) == []  # not enough history


def test_denominator_counts_formed_confirmed_failed_and_open() -> None:  # ALG-040
    forms = [_f(0, 5, confirmed=7), _f(2, 6, failed=9), _f(4, 8), _f(10, 40)]
    counts = formation_denominator(forms, now=20)
    assert counts == {"formed": 3, "confirmed": 1, "failed": 1, "open": 1}  # the candidate known at 40 is not yet formed
