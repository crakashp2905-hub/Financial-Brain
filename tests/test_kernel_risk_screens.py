from __future__ import annotations

import pytest

from financial_brain.kernel.risk_screens import (
    Mode,
    allowed_actions,
    concentration_change,
    jump_screen,
    missed_stop_fill,
    scenario_loss,
)


def test_scenario_loss_uses_named_shocks() -> None:
    assert scenario_loss({"EQ": 1.0}, "2008_like") == pytest.approx(-0.40)
    with pytest.raises(KeyError):
        scenario_loss({"EQ": 1.0}, "made_up")


def test_jump_screen_flags_overnight_moves_at_or_above_threshold() -> None:
    assert jump_screen([0.01, -0.06, 0.03, 0.05], threshold=0.05) == [1, 3]
    with pytest.raises(ValueError):
        jump_screen([0.01], threshold=0.0)


def test_missed_stop_fills_at_the_gapped_open_not_the_stop() -> None:
    assert missed_stop_fill("LONG", stop=95.0, gap_open=90.0) == 90.0
    assert missed_stop_fill("LONG", stop=95.0, gap_open=97.0) is None
    assert missed_stop_fill("SHORT", stop=105.0, gap_open=110.0) == 110.0


def test_concentration_rises_when_adding_to_the_largest_position() -> None:
    current = {"A": 0.5, "B": 0.5}
    assert concentration_change(current, {"A": 0.5}) > 0
    assert concentration_change(current, {"C": 0.0}) == pytest.approx(0.0)


def test_degraded_mode_blocks_new_exposure() -> None:
    assert "open" in allowed_actions(Mode.NORMAL)
    assert "open" not in allowed_actions(Mode.DEGRADED)
    assert "close" in allowed_actions(Mode.DEGRADED)
