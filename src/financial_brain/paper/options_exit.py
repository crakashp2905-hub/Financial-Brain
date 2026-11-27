"""Quote-triggered premium stop/target and expiry exits for long paper options."""
from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from ..costs.india import CostModel, Segment, Side
from .options import OptionPaperError, Quote, option_lot_count


@dataclass(frozen=True)
class PremiumExitPolicy:
    stop_premium: float
    target_premium: float
    expiry_exit_at: datetime
    contract_expiry_at: datetime
    registered_event_triggers: tuple[str, ...] = (
        "TIME_STOP", "VOLATILITY_SHIFT", "SIGNAL_REVERSAL", "END_OF_DAY")

    def validate(self) -> None:
        if (not math.isfinite(self.stop_premium) or not math.isfinite(self.target_premium)
                or self.stop_premium <= 0 or self.target_premium <= self.stop_premium):
            raise OptionPaperError("premium stop and target must be positive and ordered")
        for value in (self.expiry_exit_at, self.contract_expiry_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise OptionPaperError("expiry exit clocks must be timezone-aware")
        if self.expiry_exit_at >= self.contract_expiry_at:
            raise OptionPaperError("expiry exit cutoff must precede contract expiry")
        allowed = {"TIME_STOP", "VOLATILITY_SHIFT", "SIGNAL_REVERSAL", "END_OF_DAY"}
        if (not self.registered_event_triggers
                or len(set(self.registered_event_triggers)) != len(self.registered_event_triggers)
                or not set(self.registered_event_triggers) <= allowed):
            raise OptionPaperError("exit policy event triggers must be unique supported registered rules")

    def sha256(self) -> str:
        body = {"stop_premium": self.stop_premium, "target_premium": self.target_premium,
                "expiry_exit_at": self.expiry_exit_at.isoformat(),
                "contract_expiry_at": self.contract_expiry_at.isoformat(),
                "registered_event_triggers": list(self.registered_event_triggers)}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OptionExitQuote:
    quote: Quote
    available_at: datetime


@dataclass(frozen=True)
class OptionExitDirective:
    event_id: str
    trigger: str
    available_at: datetime
    evidence_sha256: str


def manage_long_option_exit(*, entry: Quote, lots: int, policy: PremiumExitPolicy,
                            quotes: tuple[OptionExitQuote, ...], max_quote_age_seconds: float,
                            directives: tuple[OptionExitDirective, ...] = (),
                            costs: CostModel | None = None) -> dict[str, object]:
    """Process observed premium quotes; never invent a stop, target, or expiry fill.

    Once a stop/target/time trigger fires, remaining quantity stays queued and may be
    partially filled from later displayed bid depth. Expired/missing residual quantity
    is reported as unresolved rather than marked at theoretical intrinsic value.
    """
    entry.validate()
    policy.validate()
    lot_count = option_lot_count(lots)
    if (entry.ask_qty < lot_count * entry.lot_size
            or not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds < 0):
        raise OptionPaperError("entry depth and quote-age policy must support the position")
    if len({(item.quote.token, item.quote.observed_at) for item in quotes}) != len(quotes):
        raise OptionPaperError("exit quote observations must be unique by token and time")
    expiry_exit = policy.expiry_exit_at
    contract_expiry = policy.contract_expiry_at
    model = costs or CostModel()
    cost_body = {name: (dict(value) if name == "impact" else value)
                 for name in model.__dataclass_fields__
                 for value in (getattr(model, name),)}
    cost_hash = hashlib.sha256(json.dumps(cost_body, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode("utf-8")).hexdigest()
    for directive in directives:
        if (not directive.event_id.strip() or directive.trigger not in policy.registered_event_triggers
                or directive.available_at.tzinfo is None or directive.available_at.utcoffset() is None
                or len(directive.evidence_sha256) != 64
                or any(char not in "0123456789abcdef" for char in directive.evidence_sha256)):
            raise OptionPaperError("exit directive must match a pre-registered trigger with sourced evidence")
    if len({directive.event_id for directive in directives}) != len(directives):
        raise OptionPaperError("exit directive event IDs must be unique")
    requested = lot_count * entry.lot_size
    remaining = requested
    trigger: str | None = None
    triggered_at: datetime | None = None
    fills: list[dict[str, object]] = []
    entry_notional = entry.ask * requested
    entry_fees = float(model.one_way(turnover=entry_notional, side=Side.BUY,
                                     segment=Segment.OPT, include_impact=False)["total"])
    exit_fees = 0.0
    exit_value = 0.0
    ordered_directives = iter(sorted(directives, key=lambda directive: directive.available_at))
    next_directive = next(ordered_directives, None)
    for item in sorted(quotes, key=lambda observation: observation.available_at):
        quote = item.quote
        if item.available_at.tzinfo is None or item.available_at.utcoffset() is None:
            raise OptionPaperError("exit quote availability time must be timezone-aware")
        if item.available_at < quote.observed_at:
            raise OptionPaperError("exit quote cannot be available before observation")
        if quote.observed_at >= contract_expiry or item.available_at >= contract_expiry:
            continue
        quote.validate()
        if (quote.token, quote.symbol, quote.expiry, quote.lot_size) != (
                entry.token, entry.symbol, entry.expiry, entry.lot_size):
            raise OptionPaperError("exit quote must match the exact entry contract")
        if quote.observed_at <= entry.observed_at:
            continue
        age = (item.available_at - quote.observed_at).total_seconds()
        if age > max_quote_age_seconds:
            continue
        if trigger is None:
            while next_directive is not None and next_directive.available_at <= item.available_at:
                trigger = next_directive.trigger
                triggered_at = next_directive.available_at
                next_directive = next(ordered_directives, None)
                break
        if trigger is None:
            if quote.bid <= policy.stop_premium:
                trigger = "PREMIUM_STOP"
                triggered_at = quote.observed_at
            elif quote.bid >= policy.target_premium:
                trigger = "PREMIUM_TARGET"
                triggered_at = quote.observed_at
            elif item.available_at >= expiry_exit:
                trigger = "EXPIRY_CUTOFF"
                triggered_at = expiry_exit
        if trigger is None:
            continue
        # Receipt time is not execution-time evidence: a quote observed before a stop,
        # target, event trigger or expiry cutoff cannot be used to claim a post-trigger exit.
        if triggered_at is None or quote.observed_at < triggered_at:
            continue
        fill_quantity = min(remaining, quote.bid_qty)
        if fill_quantity <= 0:
            continue
        fill_value = quote.bid * fill_quantity
        fill_fee = float(model.one_way(turnover=fill_value, side=Side.SELL,
                                       segment=Segment.OPT, include_impact=False)["total"])
        fills.append({"at": item.available_at.isoformat(),
                      "observed_at": quote.observed_at.isoformat(), "reason": trigger,
                      "quantity": fill_quantity, "premium": quote.bid,
                      "visible_bid_quantity": quote.bid_qty})
        exit_value += fill_value
        exit_fees += fill_fee
        remaining -= fill_quantity
        if remaining == 0:
            break
    gross = exit_value - entry_notional
    status = "CLOSED" if remaining == 0 else "PARTIALLY_CLOSED" if fills else "OPEN"
    return {"status": status, "trigger": trigger, "exit_policy_sha256": policy.sha256(),
            "cost_assumptions_sha256": cost_hash, "requested_contracts": requested,
            "filled_contracts": requested - remaining, "remaining_contracts": remaining,
            "fills": fills, "entry_premium_inr": entry_notional,
            "exit_premium_inr": exit_value, "gross_pnl_inr": gross,
            "charges_inr": entry_fees + exit_fees,
            "net_pnl_inr": gross - entry_fees - exit_fees if fills else None,
            "unresolved_at_expiry": remaining > 0,
            "broker_order_created": False}
