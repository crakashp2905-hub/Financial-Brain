"""Conservative, paper-only long-option fills from observed two-sided quotes.

This is not an option-writing or expiry-settlement engine. A missing exit quote
leaves the position unresolved, never marked at an invented intrinsic value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..costs.india import CostModel, Segment, Side


class OptionPaperError(ValueError):
    pass


@dataclass(frozen=True)
class Quote:
    token: int
    symbol: str
    expiry: date
    lot_size: int
    observed_at: datetime
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int

    def validate(self) -> None:
        if self.token <= 0 or self.lot_size <= 0 or self.bid <= 0 or self.ask < self.bid:
            raise OptionPaperError("invalid or crossed option quote")
        if self.observed_at.tzinfo is None or self.observed_at.date() >= self.expiry:
            raise OptionPaperError("option quote needs an aware timestamp before expiry")


def long_round_trip(entry: Quote, exit: Quote, *, lots: int = 1,
                    costs: CostModel | None = None) -> dict:
    """Buy at observed ask; close at later observed bid, before expiry."""
    entry.validate()
    exit.validate()
    if (entry.token, entry.symbol, entry.expiry, entry.lot_size) != (exit.token, exit.symbol, exit.expiry, exit.lot_size):
        raise OptionPaperError("entry and exit must be the same contract")
    if exit.observed_at <= entry.observed_at or lots <= 0:
        raise OptionPaperError("exit must follow entry and lots must be positive")
    qty = lots * entry.lot_size
    if entry.ask_qty < qty or exit.bid_qty < qty:
        raise OptionPaperError("visible top-of-book quantity cannot fill the paper order")
    model = costs or CostModel()
    entry_value, exit_value = entry.ask * qty, exit.bid * qty
    buy_fee = model.one_way(turnover=entry_value, side=Side.BUY, segment=Segment.OPT,
                            include_impact=False)["total"]
    sell_fee = model.one_way(turnover=exit_value, side=Side.SELL, segment=Segment.OPT,
                             include_impact=False)["total"]
    gross = exit_value - entry_value
    return {"contract": entry.symbol, "token": entry.token, "expiry": entry.expiry,
            "entry_at": entry.observed_at, "exit_at": exit.observed_at, "lots": lots,
            "quantity": qty, "entry_premium_inr": entry_value, "exit_premium_inr": exit_value,
            "gross_pnl_inr": gross, "charges_inr": buy_fee + sell_fee,
            "net_pnl_inr": gross - buy_fee - sell_fee,
            "spread_paid_in_prices": True, "settlement_modelled": False}


def cost_hurdle(entry: Quote, *, lots: int, expected_exit_bid: float,
                costs: CostModel | None = None) -> dict:
    """Reject a proposed option buy unless expected executable exit pays charges."""
    entry.validate()
    if lots <= 0 or expected_exit_bid <= 0:
        raise OptionPaperError("positive lots and expected exit bid required")
    qty = lots * entry.lot_size
    if entry.ask_qty < qty:
        return {"passes": False, "reason": "insufficient visible ask quantity"}
    model = costs or CostModel()
    buy = model.one_way(turnover=entry.ask * qty, side=Side.BUY, segment=Segment.OPT,
                        include_impact=False)["total"]
    sell = model.one_way(turnover=expected_exit_bid * qty, side=Side.SELL, segment=Segment.OPT,
                         include_impact=False)["total"]
    reward = (expected_exit_bid - entry.ask) * qty
    return {"passes": reward > buy + sell, "projected_reward_inr": reward,
            "projected_charges_inr": buy + sell}
