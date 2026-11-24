"""Executable bid/ask option-spread return and expiry assignment stress (FE-0087)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime

from .options_risk import OptionLegQuote


class OptionsSpreadError(ValueError):
    """Invalid, stale, non-executable or unsupported option-spread scenario."""


@dataclass(frozen=True)
class ExpiryScenario:
    scenario_id: str
    underlying_price: float

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not math.isfinite(float(self.underlying_price)) or self.underlying_price < 0:
            raise OptionsSpreadError("expiry scenario requires an ID and finite non-negative underlying price")


def evaluate_executable_spread(*, entry_legs: tuple[OptionLegQuote, ...],
                               exit_legs: tuple[OptionLegQuote, ...], lots: int,
                               entry_as_of: datetime, exit_as_of: datetime,
                               max_quote_age_seconds: float, fees_inr: float,
                               settlement_mode: str,
                               expiry_scenarios: tuple[ExpiryScenario, ...]) -> dict[str, object]:
    """Calculate executable bid/ask returns and expiry scenario obligations.

    Quote sizes are interpreted in contracts/lots, and each quoted premium is multiplied
    by contract lot size. Expiry settlement is scenario analysis only, never a settlement
    instruction or authoritative exchange obligation.
    """
    if lots < 1 or not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds < 0:
        raise OptionsSpreadError("positive lots and valid quote age are required")
    if not math.isfinite(fees_inr) or fees_inr < 0:
        raise OptionsSpreadError("fees must be finite and non-negative")
    if settlement_mode not in {"CASH_SETTLED", "PHYSICAL_DELIVERY"}:
        raise OptionsSpreadError("settlement_mode must be explicit")
    if not entry_legs or len(entry_legs) != len(exit_legs):
        raise OptionsSpreadError("entry and exit snapshots must contain the same non-empty leg set")
    if entry_as_of.tzinfo is None or exit_as_of.tzinfo is None or exit_as_of < entry_as_of:
        raise OptionsSpreadError("entry and exit clocks must be aware and chronological")
    if len({leg.leg_id for leg in entry_legs}) != len(entry_legs):
        raise OptionsSpreadError("entry leg IDs must be unique")
    exits = {leg.leg_id: leg for leg in exit_legs}
    if set(exits) != {leg.leg_id for leg in entry_legs}:
        raise OptionsSpreadError("exit snapshot must match each entry leg exactly once")

    entry_cash = 0.0
    exit_cash = 0.0
    legging_loss = 0.0
    cumulative = 0.0
    normalized_entries: list[dict[str, object]] = []
    for entry in entry_legs:
        exit_quote = exits[entry.leg_id]
        entry.validate(as_of=entry_as_of, quantity=lots)
        exit_quote.validate(as_of=exit_as_of, quantity=lots)
        if (entry.token, entry.symbol, entry.underlying, entry.option_type, entry.strike,
            entry.expiry, entry.lot_size) != (exit_quote.token, exit_quote.symbol, exit_quote.underlying,
            exit_quote.option_type, exit_quote.strike, exit_quote.expiry, exit_quote.lot_size):
            raise OptionsSpreadError("entry and exit contract specifications differ")
        if exit_quote.side != ("SELL" if entry.side == "BUY" else "BUY"):
            raise OptionsSpreadError("exit side must close the corresponding entry leg")
        if ((entry_as_of - entry.observed_at).total_seconds() > max_quote_age_seconds or
                (exit_as_of - exit_quote.observed_at).total_seconds() > max_quote_age_seconds):
            raise OptionsSpreadError("entry or exit quote is stale")
        contract_qty = lots * entry.lot_size
        entry_flow = (-entry.ask if entry.side == "BUY" else entry.bid) * contract_qty
        exit_flow = (exit_quote.bid if entry.side == "BUY" else -exit_quote.ask) * contract_qty
        entry_cash += entry_flow
        exit_cash += exit_flow
        cumulative += entry_flow
        legging_loss = max(legging_loss, -cumulative)
        normalized_entries.append({"leg_id": entry.leg_id, "side": entry.side,
                                   "contract_quantity": contract_qty, "entry_price": entry.ask if entry.side == "BUY" else entry.bid,
                                   "exit_price": exit_quote.bid if entry.side == "BUY" else exit_quote.ask})

    net_pnl = entry_cash + exit_cash - fees_inr
    scenarios = []
    for scenario in expiry_scenarios:
        intrinsic_cash = 0.0
        assignment_cash_required = 0.0
        assignment_cash_received = 0.0
        assignment_shares_received = 0
        assignment_shares_delivered = 0
        for entry in entry_legs:
            contracts = lots * entry.lot_size
            intrinsic = (max(0.0, scenario.underlying_price - entry.strike) if entry.option_type == "CE"
                         else max(0.0, entry.strike - scenario.underlying_price))
            sign = 1 if entry.side == "BUY" else -1
            intrinsic_cash += sign * intrinsic * contracts
            if settlement_mode == "PHYSICAL_DELIVERY" and intrinsic > 0:
                if entry.option_type == "CE":
                    # Call holder pays strike and receives shares; call writer delivers shares.
                    if sign > 0:
                        assignment_cash_required += entry.strike * contracts
                        assignment_shares_received += contracts
                    else:
                        assignment_cash_received += entry.strike * contracts
                        assignment_shares_delivered += contracts
                else:
                    # Put holder delivers shares for strike proceeds; put writer pays for delivery.
                    if sign > 0:
                        assignment_cash_received += entry.strike * contracts
                        assignment_shares_delivered += contracts
                    else:
                        assignment_cash_required += entry.strike * contracts
                        assignment_shares_received += contracts
        scenarios.append({"scenario_id": scenario.scenario_id,
                          "underlying_price": scenario.underlying_price,
                          "option_intrinsic_pnl_before_entry_costs": intrinsic_cash,
                          "net_expiry_pnl_after_entry_cash_and_fees": entry_cash + intrinsic_cash - fees_inr,
                          "physical_assignment_cash_required_inr": assignment_cash_required,
                          "physical_assignment_cash_received_inr": assignment_cash_received,
                          "physical_shares_received": assignment_shares_received,
                          "physical_shares_delivered": assignment_shares_delivered,
                          "assignment_status": "SCENARIO_ONLY"})
    body = {"legs": normalized_entries, "entry_cash_flow_inr": entry_cash,
            "exit_cash_flow_inr": exit_cash, "fees_inr": fees_inr, "net_pnl_inr": net_pnl,
            "legging_loss_bound_inr": legging_loss, "settlement_mode": settlement_mode,
            "expiry_scenarios": scenarios,
            "quote_status": "CALLER_SUPPLIED_NOT_AUTHENTICATED"}
    body["report_hash"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode()).hexdigest()
    return body
