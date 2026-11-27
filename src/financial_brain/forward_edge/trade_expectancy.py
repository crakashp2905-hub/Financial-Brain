"""Per-trade expectancy and break-even win rate from complete after-cost outcomes."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


class TradeExpectancyError(ValueError):
    """Invalid trade P&L components or incomplete cost evidence."""


@dataclass(frozen=True)
class CostedTradeOutcome:
    trade_id: str
    gross_pnl_inr: float
    execution_costs_inr: float
    fees_inr: float
    taxes_inr: float
    financing_inr: float
    other_costs_inr: float
    costs_complete: bool = True

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise TradeExpectancyError("trade identity is required")
        values = (self.gross_pnl_inr, self.execution_costs_inr, self.fees_inr,
                  self.taxes_inr, self.financing_inr, self.other_costs_inr)
        if any(not math.isfinite(value) for value in values):
            raise TradeExpectancyError("trade P&L and cost components must be finite")
        if any(value < 0 for value in values[1:]):
            raise TradeExpectancyError("trade costs cannot be negative")
        if not self.costs_complete:
            raise TradeExpectancyError("all-in trade costs must be complete before expectancy is computed")

    @property
    def net_pnl_inr(self) -> float:
        return (self.gross_pnl_inr - self.execution_costs_inr - self.fees_inr
                - self.taxes_inr - self.financing_inr - self.other_costs_inr)


def calculate_trade_expectancy(*, trades: tuple[CostedTradeOutcome, ...],
                               unresolved_outcomes: int = 0) -> dict[str, object]:
    """Compute empirical net expectancy and binary break-even probability.

    Positive, negative, and flat net trades are counted separately. The break-even
    probability uses the sample's average win and absolute average loss; it is not
    a universal target and is omitted when either side is absent.
    """
    if not trades or len({trade.trade_id for trade in trades}) != len(trades):
        raise TradeExpectancyError("unique resolved trade outcomes are required")
    if type(unresolved_outcomes) is not int or unresolved_outcomes < 0:
        raise TradeExpectancyError("unresolved outcome count must be a non-negative integer")
    net = [trade.net_pnl_inr for trade in trades]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    flats = [value for value in net if value == 0]
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = -sum(losses) / len(losses) if losses else None
    break_even = (average_loss / (average_win + average_loss)
                  if average_win is not None and average_loss is not None else None)
    total_gross = sum(trade.gross_pnl_inr for trade in trades)
    total_costs = sum(trade.gross_pnl_inr - trade.net_pnl_inr for trade in trades)
    report: dict[str, object] = {
        "resolved_trades": len(trades),
        "unresolved_outcomes": unresolved_outcomes,
        "positive_net_trades": len(wins),
        "negative_net_trades": len(losses),
        "flat_net_trades": len(flats),
        "observed_net_win_rate": len(wins) / len(trades),
        "average_net_win_inr": average_win,
        "average_net_loss_inr": -average_loss if average_loss is not None else None,
        "expectancy_net_inr_per_resolved_trade": sum(net) / len(net),
        "break_even_win_probability": break_even,
        "total_gross_pnl_inr": total_gross,
        "total_itemized_costs_inr": total_costs,
        "total_net_pnl_inr": sum(net),
        "net_expectancy_positive": sum(net) / len(net) > 0,
        "estimate_complete": unresolved_outcomes == 0,
        "interpretation": "empirical sample estimate after itemized costs; no fixed win-rate target",
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return report
