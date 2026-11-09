"""Risk screens (FB-0279, FB-0282, FB-0287, FB-0288, FB-0291).

* ``REGIME_SCENARIOS`` are illustrative per-asset shocks, labelled as such. They are not calibrated to history.
* ``jump_screen`` flags overnight moves at or beyond a threshold.
* ``missed_stop_fill`` gives the fill when a gap opens through a stop: the fill is at the open, not at the stop.
* ``concentration_change`` gives the change in Herfindahl concentration a candidate trade would cause, before it trades.
* ``allowed_actions`` lets DEGRADED mode keep viewing and closing, and refuses new exposure.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum

from financial_brain.kernel.portfolio_planning import apply_shocks

REGIME_SCENARIOS: dict[str, dict[str, float]] = {
    "2008_like": {"EQ": -0.40, "CREDIT": -0.15, "BOND": 0.05},
    "2020_like": {"EQ": -0.30, "CREDIT": -0.10, "BOND": 0.03},
    "flash_crash": {"EQ": -0.09, "CREDIT": -0.01},
    "fx_spike": {"FX": 0.15, "EQ": -0.05},
}


def scenario_loss(weights: Mapping[str, float], scenario: str) -> float:
    if scenario not in REGIME_SCENARIOS:
        raise KeyError(f"unknown scenario {scenario!r}")
    return apply_shocks(weights, REGIME_SCENARIOS[scenario])


def jump_screen(overnight_moves: Sequence[float], threshold: float) -> list[int]:
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    return [i for i, m in enumerate(overnight_moves) if abs(m) >= threshold]


def missed_stop_fill(side: str, stop: float, gap_open: float) -> float | None:
    """A stop sitting through a gap fills at the open (worse than the stop). None if the stop was not reached."""
    if side == "LONG":
        return gap_open if gap_open <= stop else None
    if side == "SHORT":
        return gap_open if gap_open >= stop else None
    raise ValueError("side must be LONG or SHORT")


def _hhi(weights: Mapping[str, float]) -> float:
    total = sum(abs(w) for w in weights.values())
    if total == 0:
        return 0.0
    return sum((abs(w) / total) ** 2 for w in weights.values())


def concentration_change(current: Mapping[str, float], candidate: Mapping[str, float]) -> float:
    after = dict(current)
    for asset, delta in candidate.items():
        after[asset] = after.get(asset, 0.0) + delta
    return _hhi(after) - _hhi(current)


class Mode(Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"


def allowed_actions(mode: Mode) -> set[str]:
    if mode is Mode.NORMAL:
        return {"view", "open", "close", "modify"}
    return {"view", "close"}
