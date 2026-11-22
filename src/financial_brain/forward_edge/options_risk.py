"""Executable multi-leg options risk checks with explicit legging and margin boundaries (FE-0062)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class OptionsRiskError(ValueError):
    """Invalid, stale, non-executable or unsupported options scenario."""


def validate_supported_paper_option_structure(*, legs: tuple["OptionLegQuote", ...],
                                               quantity: int,
                                               leg_order: tuple[str, ...] | None = None) -> dict[str, float | str]:
    """Allow long premium or a risk-bounded debit vertical in paper analysis.

    This is a strategy-shape gate, not a broker margin or settlement approval. It
    intentionally refuses naked shorts, credit spreads, reversed verticals and
    structures with more than two legs until their lifecycle and margin are modeled.
    """
    if quantity < 1 or not legs:
        raise OptionsRiskError("a supported paper option structure needs legs and positive quantity")
    if len(legs) == 1:
        leg = legs[0]
        if leg.side != "BUY":
            raise OptionsRiskError("naked short options are disabled")
        if leg.option_type not in {"CE", "PE"}:
            raise OptionsRiskError("unsupported option right")
        if leg_order is not None and leg_order != (leg.leg_id,):
            raise OptionsRiskError("leg_order must contain the long option exactly once")
        return {"strategy_type": "LONG_PREMIUM", "entry_debit_inr": leg.ask * quantity,
                "economic_max_loss_inr": leg.ask * quantity}
    if len(legs) != 2:
        raise OptionsRiskError("only long options and two-leg debit verticals are enabled")
    if len({leg.leg_id for leg in legs}) != 2 or len({leg.token for leg in legs}) != 2:
        raise OptionsRiskError("debit vertical legs must have distinct IDs and contracts")
    buys = [leg for leg in legs if leg.side == "BUY"]
    sells = [leg for leg in legs if leg.side == "SELL"]
    if len(buys) != 1 or len(sells) != 1:
        raise OptionsRiskError("debit vertical requires exactly one long and one covered short leg")
    long, short = buys[0], sells[0]
    long_identity = (long.underlying, long.option_type, long.expiry, long.lot_size)
    short_identity = (short.underlying, short.option_type, short.expiry, short.lot_size)
    if long_identity != short_identity:
        raise OptionsRiskError("debit vertical legs must share underlying, right, expiry and lot size")
    bounded_order = (long.strike < short.strike if long.option_type == "CE"
                     else long.strike > short.strike if long.option_type == "PE" else False)
    if not bounded_order:
        raise OptionsRiskError("short leg must cap the long leg in a debit vertical")
    if leg_order is not None:
        expected_order = (long.leg_id, short.leg_id)
        if leg_order != expected_order:
            raise OptionsRiskError("paper leg sequence must buy the protective leg before selling")
    debit = (long.ask - short.bid) * quantity
    width = abs(short.strike - long.strike) * quantity
    if not 0 < debit < width:
        raise OptionsRiskError("debit vertical must have positive debit below its maximum payoff width")
    return {"strategy_type": "DEBIT_VERTICAL", "entry_debit_inr": debit,
            "economic_max_loss_inr": debit}


@dataclass(frozen=True)
class OptionLegQuote:
    leg_id: str
    side: str
    token: int
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: date
    lot_size: int
    observed_at: datetime
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int

    def validate(self, *, as_of: datetime, quantity: int) -> None:
        if self.side not in {"BUY", "SELL"} or self.token <= 0 or not self.symbol or not self.underlying:
            raise OptionsRiskError("invalid option leg identity or side")
        if self.option_type not in {"CE", "PE"} or self.strike <= 0 or self.lot_size < 1:
            raise OptionsRiskError("invalid option contract terms")
        if self.observed_at.tzinfo is None or as_of.tzinfo is None:
            raise OptionsRiskError("option quote clocks must be timezone aware")
        if self.observed_at > as_of or self.observed_at.date() >= self.expiry:
            raise OptionsRiskError("quote is future-dated or at/after expiry")
        if self.bid <= 0 or self.ask < self.bid:
            raise OptionsRiskError("executable two-sided quote required")
        visible = self.ask_qty if self.side == "BUY" else self.bid_qty
        if visible < quantity:
            raise OptionsRiskError("insufficient visible quantity for option leg")


def evaluate_defined_risk_legs(*, legs: tuple[OptionLegQuote, ...], quantity: int,
                               as_of: datetime, leg_order: tuple[str, ...],
                               max_quote_age_seconds: float,
                               underlying_at_expiry: float | None = None,
                               verified_margin_inr: float | None = None) -> dict:
    if not legs or quantity < 1 or max_quote_age_seconds < 0:
        raise OptionsRiskError("legs, positive quantity and valid quote age required")
    if len({leg.leg_id for leg in legs}) != len(legs) or len({leg.token for leg in legs}) != len(legs):
        raise OptionsRiskError("option legs must have distinct IDs and tokens")
    by_id = {leg.leg_id: leg for leg in legs}
    if set(leg_order) != set(by_id) or len(leg_order) != len(legs):
        raise OptionsRiskError("leg_order must contain every leg exactly once")
    supported = validate_supported_paper_option_structure(legs=legs, quantity=quantity,
                                                           leg_order=leg_order)
    anchor = legs[0]
    if any((leg.underlying, leg.option_type, leg.expiry, leg.lot_size) !=
           (anchor.underlying, anchor.option_type, anchor.expiry, anchor.lot_size) for leg in legs):
        raise OptionsRiskError("defined-risk spread legs must share underlying, type, expiry and lot size")
    if any(leg.observed_at.tzinfo is None or as_of.tzinfo is None or
           (as_of - leg.observed_at).total_seconds() > max_quote_age_seconds for leg in legs):
        raise OptionsRiskError("leg quote is stale or lacks a clock")
    for leg in legs:
        leg.validate(as_of=as_of, quantity=quantity)
    ordered = [by_id[key] for key in leg_order]
    cash_flows = [(-leg.ask if leg.side == "BUY" else leg.bid) * quantity for leg in ordered]
    cumulative = 0.0
    worst_intermediate_loss = 0.0
    for cash_flow in cash_flows:
        cumulative += cash_flow
        worst_intermediate_loss = max(worst_intermediate_loss, -cumulative)
    entry_debit = -sum(cash_flows)
    strikes = sorted({leg.strike for leg in legs})
    defined_width = strikes[-1] - strikes[0] if len(strikes) > 1 else None
    max_loss = float(supported["economic_max_loss_inr"])
    expiry_pnl = None
    settlement_status = "NOT_MODELLED"
    if underlying_at_expiry is not None:
        expiry_pnl = 0.0
        for leg, cash_flow in zip(ordered, cash_flows, strict=True):
            intrinsic = max(0.0, underlying_at_expiry - leg.strike) if leg.option_type == "CE" else \
                max(0.0, leg.strike - underlying_at_expiry)
            expiry_pnl += (intrinsic * quantity if leg.side == "BUY" else -intrinsic * quantity) + cash_flow
        settlement_status = "INTRINSIC_SCENARIO_ONLY"
    return {"underlying": anchor.underlying, "expiry": anchor.expiry,
            "strategy_type": supported["strategy_type"], "entry_debit_inr": entry_debit,
            "defined_width": defined_width, "economic_max_loss_inr": max_loss,
            "legging_loss_bound_inr": worst_intermediate_loss, "leg_order": list(leg_order),
            "verified_margin_inr": verified_margin_inr,
            "margin_status": "BROKER_VERIFIED" if verified_margin_inr is not None else "NOT_VERIFIED",
            "expiry_settlement_status": settlement_status, "expiry_intrinsic_scenario_pnl_inr": expiry_pnl}
