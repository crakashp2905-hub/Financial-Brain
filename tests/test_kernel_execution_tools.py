from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.execution_tools import (
    Mode,
    OcoPair,
    iceberg_slices,
    may_submit,
    slippage_bps,
    twap_slices,
    validate_preview,
)


def test_oco_cancels_the_other_leg_and_refuses_a_second_fill() -> None:
    oco = OcoPair("stop", "target")
    assert oco.on_fill("target") == {"stop"}
    with pytest.raises(ValueError, match="already filled"):
        oco.on_fill("stop")


def test_manual_never_submits_and_only_paper_auto_submits_unattended() -> None:
    assert may_submit(Mode.MANUAL, human_confirmed=True, paper=True) is False
    assert may_submit(Mode.SEMI_AUTO, human_confirmed=False, paper=True) is False
    assert may_submit(Mode.SEMI_AUTO, human_confirmed=True, paper=True) is True
    assert may_submit(Mode.PAPER_AUTO, human_confirmed=False, paper=True) is True
    assert may_submit(Mode.PAPER_AUTO, human_confirmed=False, paper=False) is False


def test_slices_sum_to_the_parent_quantity() -> None:
    assert twap_slices(10, 3) == [4, 3, 3]
    assert sum(twap_slices(1000, 7)) == 1000
    assert iceberg_slices(250, 100) == [100, 100, 50]
    assert sum(iceberg_slices(250, 100)) == 250


def test_preview_checks_tick_lot_and_multiplier() -> None:
    assert validate_preview(Decimal("100.05"), 50, Decimal("0.05"), 25, Decimal("1")) == []
    reasons = validate_preview(Decimal("100.03"), 30, Decimal("0.05"), 25, Decimal("1"))
    assert any("tick" in r for r in reasons)
    assert any("lot" in r for r in reasons)
    assert validate_preview(Decimal("100"), 25, Decimal("0.05"), 25, Decimal("0"))


def test_slippage_sign_is_adverse_positive() -> None:
    assert slippage_bps(Decimal("101"), Decimal("100"), "BUY") == Decimal("100")
    assert slippage_bps(Decimal("101"), Decimal("100"), "SELL") == Decimal("-100")
    with pytest.raises(ValueError):
        slippage_bps(Decimal("1"), Decimal("0"), "BUY")
