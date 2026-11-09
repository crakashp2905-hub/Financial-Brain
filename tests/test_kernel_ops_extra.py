from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.ops_extra import (
    OptionJournal,
    hedge_index_holding,
    liquidity_score,
    roll_analysis,
    tail_report,
)


def test_liquidity_score_falls_with_wider_spread() -> None:
    tight = liquidity_score(Decimal("1000000"), Decimal("0.05"), Decimal("100"))
    wide = liquidity_score(Decimal("1000000"), Decimal("1"), Decimal("100"))
    assert tight > wide > 0


def test_hedge_index_holding_uses_beta_and_rounds_toward_zero() -> None:
    assert hedge_index_holding(Decimal("1000000"), Decimal("1.2"), Decimal("500000")) == 2


def test_roll_cost_per_unit_and_total() -> None:
    result = roll_analysis(Decimal("100"), Decimal("102"), 50)
    assert result == {"per_unit": Decimal("2"), "total": Decimal("100")}


def test_option_journal_refuses_a_second_mark_for_the_same_day() -> None:
    journal = OptionJournal()
    journal.record_mark("2026-11-09", {"NIFTY-C-22000": Decimal("150")})
    with pytest.raises(ValueError, match="already recorded"):
        journal.record_mark("2026-11-09", {"NIFTY-C-22000": Decimal("160")})


def test_tail_report_gives_cvar_per_regime() -> None:
    returns = [-0.10, -0.02, 0.01, 0.02, -0.05, 0.03]
    regimes = ["calm", "calm", "calm", "calm", "stress", "stress"]
    report = tail_report(returns, regimes, alpha=0.5)
    assert set(report) == {"calm", "stress"}
    assert report["stress"] > 0
    with pytest.raises(ValueError):
        tail_report(returns, ["calm"], alpha=0.5)
