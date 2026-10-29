"""Defined-risk debit vertical envelope; no expiry settlement or broker margin claim."""
from __future__ import annotations

from dataclasses import dataclass

from ..costs.india import CostModel, Segment, Side
from .options import OptionPaperError, Quote


@dataclass(frozen=True)
class Leg:
    quote: Quote
    underlying: str
    option_type: str
    strike: float


def debit_vertical(long: Leg, short: Leg, *, lots: int = 1,
                   costs: CostModel | None = None) -> dict:
    """Buy a call lower/sell higher, or buy a put higher/sell lower.

    Bid/ask and visible quantities determine the paper entry. The risk reserve
    is economic maximum loss, **not** a broker SPAN/exposure margin estimate.
    """
    a, b = long.quote, short.quote
    a.validate()
    b.validate()
    if lots <= 0 or long.strike <= 0 or short.strike <= 0:
        raise OptionPaperError("positive lots and strikes required")
    if (long.underlying, long.option_type, a.expiry, a.lot_size) != (
            short.underlying, short.option_type, b.expiry, b.lot_size):
        raise OptionPaperError("vertical legs need the same underlying, type, expiry and lot size")
    if long.option_type not in ("CE", "PE") or a.token == b.token:
        raise OptionPaperError("vertical needs two distinct call or put contracts")
    width = (short.strike - long.strike if long.option_type == "CE"
             else long.strike - short.strike)
    if width <= 0:
        raise OptionPaperError("debit vertical strikes are not ordered")
    qty = lots * a.lot_size
    if a.ask_qty < qty or b.bid_qty < qty:
        raise OptionPaperError("visible two-leg quantity cannot fill the spread")
    debit = (a.ask - b.bid) * qty
    if not 0 < debit < width * qty:
        raise OptionPaperError("spread debit must be positive and below the strike width")
    model = costs or CostModel()
    buy = model.one_way(turnover=a.ask * qty, side=Side.BUY, segment=Segment.OPT,
                        include_impact=False)["total"]
    sell = model.one_way(turnover=b.bid * qty, side=Side.SELL, segment=Segment.OPT,
                         include_impact=False)["total"]
    fees = buy + sell
    return {"underlying": long.underlying, "option_type": long.option_type,
            "expiry": a.expiry, "lots": lots, "quantity": qty,
            "entry_debit_inr": debit, "entry_charges_inr": fees,
            "max_loss_before_exit_charges_inr": debit + fees,
            "max_gain_before_exit_charges_inr": width * qty - debit - fees,
            "economic_risk_reserve_inr": debit + fees,
            "broker_margin_verified": False, "expiry_settlement_modelled": False}
