"""Operations extras (FB-0307, FB-0312, FB-0313, FB-0329, FB-0330).

* ``liquidity_score`` summarises a contract from ADV and quoted spread. It is a ranking aid, not a trading signal.
* ``hedge_index_holding`` sizes futures to hedge an equity holding with a beta. Rounds toward zero.
* ``roll_analysis`` gives the cost of rolling a position from the front to the back contract, per unit and in total.
* ``OptionJournal`` is append-only. Daily marks are recorded per date and a second mark for the same date is refused.
* ``tail_report`` groups per-period returns by regime and gives the tail loss (CVaR) per group.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from financial_brain.kernel.portfolio_risk import historical_var_cvar


def liquidity_score(adv: Decimal, spread: Decimal, mid: Decimal) -> float:
    if adv < 0 or spread < 0 or mid <= 0:
        raise ValueError("adv and spread must be non-negative and mid positive")
    rel_spread = float(spread / mid)
    return float(adv) / (1.0 + 100.0 * rel_spread)


def hedge_index_holding(holding_value: Decimal, beta: Decimal, index_futures_notional: Decimal) -> int:
    if index_futures_notional <= 0:
        raise ValueError("futures notional must be positive")
    raw = (holding_value * beta / index_futures_notional).to_integral_value(rounding=ROUND_DOWN)
    return int(raw)


def roll_analysis(front: Decimal, back: Decimal, quantity: int) -> dict[str, Decimal]:
    per_unit = back - front
    return {"per_unit": per_unit, "total": per_unit * Decimal(quantity)}


@dataclass
class OptionJournal:
    marks: dict[str, Mapping[str, Decimal]] = field(default_factory=dict)

    def record_mark(self, day: str, marks: Mapping[str, Decimal]) -> None:
        if day in self.marks:
            raise ValueError(f"daily mark for {day} already recorded")
        self.marks[day] = dict(marks)


def tail_report(returns: Sequence[float], regimes: Sequence[str], alpha: float) -> dict[str, float]:
    if len(returns) != len(regimes):
        raise ValueError("returns and regimes must align")
    groups: dict[str, list[float]] = {}
    for r, reg in zip(returns, regimes, strict=True):
        groups.setdefault(reg, []).append(r)
    out: dict[str, float] = {}
    for reg, vals in sorted(groups.items()):
        _, cvar = historical_var_cvar(vals, alpha)
        out[reg] = cvar
    return out
