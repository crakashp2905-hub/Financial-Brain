"""Executable multi-leg options risk checks with explicit legging and margin boundaries (FE-0062)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class OptionsRiskError(ValueError):
    """Invalid, stale, non-executable or unsupported options scenario."""


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
    underlying_price: float | None = None
    implied_volatility: float | None = None

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
    if defined_width is not None:
        if len(legs) != 2 or {leg.side for leg in legs} != {"BUY", "SELL"}:
            raise OptionsRiskError("only two-leg debit vertical spreads are supported")
        long_leg = next(leg for leg in legs if leg.side == "BUY")
        short_leg = next(leg for leg in legs if leg.side == "SELL")
        correctly_oriented = (long_leg.strike < short_leg.strike if anchor.option_type == "CE"
                              else long_leg.strike > short_leg.strike)
        if not correctly_oriented:
            raise OptionsRiskError("debit vertical option legs are inverted")
        if entry_debit <= 0:
            raise OptionsRiskError("defined-risk debit structure must have positive entry debit")
    max_loss = entry_debit if defined_width is not None else None
    expiry_pnl = None
    settlement_status = "NOT_MODELLED"
    if underlying_at_expiry is not None:
        expiry_pnl = 0.0
        for leg, cash_flow in zip(ordered, cash_flows, strict=True):
            intrinsic = max(0.0, underlying_at_expiry - leg.strike) if leg.option_type == "CE" else \
                max(0.0, leg.strike - underlying_at_expiry)
            expiry_pnl += (intrinsic * quantity if leg.side == "BUY" else -intrinsic * quantity) + cash_flow
        settlement_status = "INTRINSIC_SCENARIO_ONLY"
    return {"underlying": anchor.underlying, "expiry": anchor.expiry, "entry_debit_inr": entry_debit,
            "defined_width": defined_width, "economic_max_loss_inr": max_loss,
            "legging_loss_bound_inr": worst_intermediate_loss, "leg_order": list(leg_order),
            "verified_margin_inr": verified_margin_inr,
            "margin_status": "BROKER_VERIFIED" if verified_margin_inr is not None else "NOT_VERIFIED",
            "expiry_settlement_status": settlement_status, "expiry_intrinsic_scenario_pnl_inr": expiry_pnl}
