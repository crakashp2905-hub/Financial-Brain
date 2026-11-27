"""Maximum-loss and reserved-capital accounting for long option orders."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..costs.india import CostModel, Segment, Side
from .options import OptionPaperError


@dataclass(frozen=True)
class OptionBuyFill:
    contract_quantity: int
    premium: float


def long_option_capital_at_risk(*, requested_contracts: int, lot_size: int,
                                limit_premium: float, fills: tuple[OptionBuyFill, ...],
                                cancelled_contracts: int = 0,
                                costs: CostModel | None = None) -> dict[str, object]:
    """Bound filled premium loss plus fees and reserve only still-live buy quantity."""
    if (type(requested_contracts) is not int or requested_contracts <= 0
            or type(lot_size) is not int or lot_size <= 0
            or requested_contracts % lot_size
            or not math.isfinite(limit_premium) or limit_premium <= 0
            or type(cancelled_contracts) is not int or cancelled_contracts < 0
            or cancelled_contracts % lot_size):
        raise OptionPaperError("requested contracts, lot size, limit premium and cancellations are invalid")
    filled = 0
    filled_premium = 0.0
    filled_fees = 0.0
    model = costs or CostModel()
    normalized_fills: list[dict[str, float | int]] = []
    for fill in fills:
        if (type(fill.contract_quantity) is not int or fill.contract_quantity <= 0
                or fill.contract_quantity % lot_size
                or not math.isfinite(fill.premium) or fill.premium <= 0
                or fill.premium > limit_premium):
            raise OptionPaperError("buy fills must be positive, finite and at or below the limit premium")
        filled += fill.contract_quantity
        notional = fill.contract_quantity * fill.premium
        fee = float(model.one_way(turnover=notional, side=Side.BUY,
                                  segment=Segment.OPT, include_impact=False)["total"])
        filled_premium += notional
        filled_fees += fee
        normalized_fills.append({"contract_quantity": fill.contract_quantity,
                                 "premium": fill.premium, "premium_value_inr": notional,
                                 "entry_charges_inr": fee})
    if filled + cancelled_contracts > requested_contracts:
        raise OptionPaperError("fills plus cancellations exceed the requested contract quantity")
    open_quantity = requested_contracts - filled - cancelled_contracts
    reserve_value = open_quantity * limit_premium
    reserve_fees = float(model.one_way(turnover=reserve_value, side=Side.BUY,
                                       segment=Segment.OPT, include_impact=False)["total"])
    filled_max_loss = filled_premium + filled_fees
    return {"requested_contracts": requested_contracts, "filled_contracts": filled,
            "cancelled_contracts": cancelled_contracts, "open_contracts": open_quantity,
            "fill_ledger": normalized_fills,
            "filled_premium_inr": filled_premium, "filled_entry_charges_inr": filled_fees,
            "maximum_loss_filled_position_inr": filled_max_loss,
            "unfilled_order_reserve_inr": reserve_value + reserve_fees,
            "total_capital_at_risk_or_reserved_inr": filled_max_loss + reserve_value + reserve_fees,
            "loss_bound_basis": "LONG_PREMIUM_PLUS_ENTRY_CHARGES; NO_SHORT_OPTION_LOSS",
            "broker_order_created": False}
