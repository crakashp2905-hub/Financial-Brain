"""Partial-fill, legging-margin and closeout stress for paper option verticals."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from typing import Mapping, Sequence

from financial_brain.costs.india import CostModel, Segment, Side
from financial_brain.paper.options_spread import OptionLegEvidence


class SpreadLifecycleError(ValueError):
    """Invalid option spread plan or fill evidence."""


@dataclass(frozen=True)
class SpreadLifecyclePlan:
    underlying: str
    venue: str
    option_type: str
    expiry: date
    lot_size: int
    target_lots: int
    long_token: int
    long_strike: Decimal
    short_token: int
    short_strike: Decimal
    defined_spread_margin_per_lot: Decimal
    naked_short_margin_per_lot: Decimal
    long_contract_sha256: str
    short_contract_sha256: str
    margin_source_sha256: str


@dataclass(frozen=True)
class OptionFill:
    event_id: str
    instrument_token: int
    action: str
    lots: int
    price: Decimal
    fee_inr: Decimal
    filled_at: datetime
    source_sha256: str


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SpreadLifecycleError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SpreadLifecycleError(f"{name} must be a SHA-256 digest")


def _plan_valid(plan: SpreadLifecyclePlan) -> None:
    if not plan.underlying.strip() or not plan.venue.strip() or plan.option_type not in {"CE", "PE"}:
        raise SpreadLifecycleError("underlying, venue, and CE/PE type are required")
    if type(plan.expiry) is not date or type(plan.lot_size) is not int or plan.lot_size <= 0:
        raise SpreadLifecycleError("expiry and positive venue lot size are required")
    if type(plan.target_lots) is not int or plan.target_lots <= 0:
        raise SpreadLifecycleError("target_lots must be positive")
    if plan.long_token <= 0 or plan.short_token <= 0 or plan.long_token == plan.short_token:
        raise SpreadLifecycleError("spread legs need distinct contract tokens")
    for name, value in (("long_strike", plan.long_strike), ("short_strike", plan.short_strike),
                        ("defined margin", plan.defined_spread_margin_per_lot),
                        ("naked short margin", plan.naked_short_margin_per_lot)):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise SpreadLifecycleError(f"{name} must be a positive finite Decimal")
    if plan.option_type == "CE" and plan.long_strike >= plan.short_strike:
        raise SpreadLifecycleError("call debit vertical strikes are not ordered")
    if plan.option_type == "PE" and plan.long_strike <= plan.short_strike:
        raise SpreadLifecycleError("put debit vertical strikes are not ordered")
    _digest(plan.long_contract_sha256, "long contract source")
    _digest(plan.short_contract_sha256, "short contract source")
    _digest(plan.margin_source_sha256, "margin source")


def evaluate_spread_lifecycle(
    plan: SpreadLifecyclePlan,
    fills: Sequence[OptionFill],
    *,
    as_of: datetime,
    close_quotes: Mapping[int, OptionLegEvidence],
    stress_spot_prices: Sequence[Decimal],
    max_quote_age: timedelta = timedelta(seconds=5),
    costs: CostModel | None = None,
) -> dict:
    """Retain partial fills, unmatched leg risk, margin and executable-closeout stress.

    Fill quantities are whole lots. Close marks use only fresh, source-hashed bid/ask
    quotes and displayed depth. Unfilled lots remain in the report, and missing or
    insufficient exit depth remains unresolved instead of being marked flat.
    """
    _plan_valid(plan)
    cutoff = _aware(as_of, "as_of")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise SpreadLifecycleError("max_quote_age must be non-negative")
    spots = tuple(Decimal(item) for item in stress_spot_prices)
    if not spots or any(not item.is_finite() or item <= 0 for item in spots):
        raise SpreadLifecycleError("positive finite stress spot prices are required")
    if plan.expiry <= cutoff.date():
        raise SpreadLifecycleError("expired spread cannot enter a new lifecycle evaluation")

    legs = {
        plan.long_token: {"action": "BUY", "strike": plan.long_strike,
                          "contract_sha256": plan.long_contract_sha256},
        plan.short_token: {"action": "SELL", "strike": plan.short_strike,
                           "contract_sha256": plan.short_contract_sha256},
    }
    quantities = {plan.long_token: 0, plan.short_token: 0}
    notionals = {plan.long_token: Decimal(0), plan.short_token: Decimal(0)}
    fees = {plan.long_token: Decimal(0), plan.short_token: Decimal(0)}
    event_ids: set[str] = set()
    prior_time: datetime | None = None
    fill_rows = []
    for fill in fills:
        if fill.event_id in event_ids or not fill.event_id.strip():
            raise SpreadLifecycleError("fill event IDs must be unique and non-empty")
        event_ids.add(fill.event_id)
        if fill.instrument_token not in legs:
            raise SpreadLifecycleError("fill references an instrument outside the frozen spread")
        expected = legs[fill.instrument_token]
        at = _aware(fill.filled_at, "filled_at")
        if at > cutoff or prior_time is not None and at < prior_time:
            raise SpreadLifecycleError("fills must be available by as_of and ordered by time")
        prior_time = at
        _digest(fill.source_sha256, "fill source")
        if fill.action != expected["action"]:
            raise SpreadLifecycleError("fill side does not match its frozen spread leg")
        if type(fill.lots) is not int or fill.lots <= 0:
            raise SpreadLifecycleError("fill lots must be positive whole lots")
        if (not isinstance(fill.price, Decimal) or not fill.price.is_finite() or fill.price <= 0
                or not isinstance(fill.fee_inr, Decimal) or not fill.fee_inr.is_finite()
                or fill.fee_inr < 0):
            raise SpreadLifecycleError("fill price and fees must be finite; fees cannot be negative")
        token = fill.instrument_token
        quantities[token] += fill.lots
        if quantities[token] > plan.target_lots:
            raise SpreadLifecycleError("fills exceed the frozen target lots")
        notionals[token] += fill.price * fill.lots
        fees[token] += fill.fee_inr
        fill_rows.append({"event_id": fill.event_id, "instrument_token": token,
                          "action": fill.action, "lots": fill.lots, "price": str(fill.price),
                          "fee_inr": str(fill.fee_inr), "filled_at": at.isoformat(),
                          "source_sha256": fill.source_sha256})

    long_lots, short_lots = quantities[plan.long_token], quantities[plan.short_token]
    paired_lots = min(long_lots, short_lots)
    unmatched_short_lots = max(short_lots - long_lots, 0)
    unmatched_long_lots = max(long_lots - short_lots, 0)
    margin_reserved = (Decimal(paired_lots) * plan.defined_spread_margin_per_lot
                       + Decimal(unmatched_short_lots) * plan.naked_short_margin_per_lot)
    pending = {plan.long_token: plan.target_lots - long_lots,
               plan.short_token: plan.target_lots - short_lots}
    average_fill = {
        token: (notionals[token] / quantities[token] if quantities[token] else Decimal(0))
        for token in legs
    }

    model = costs or CostModel()
    closed_lots: dict[int, int] = {}
    closeout_pnl = Decimal(0)
    closeout_rows = []
    for token, expected in legs.items():
        item = close_quotes.get(token)
        if item is None:
            closed_lots[token] = 0
            closeout_rows.append({"instrument_token": token, "closed_lots": 0,
                                  "reason": "MISSING_CLOSE_QUOTE"})
            continue
        quote = item.leg.quote
        blockers = []
        observed, available = _aware(quote.observed_at, "close quote observed_at"), _aware(
            item.quote_available_at, "close quote available_at"
        )
        _digest(item.quote_source_sha256, "close quote source")
        _digest(item.contract_master_sha256, "close contract source")
        if quote.token != token or item.leg.underlying != plan.underlying:
            blockers.append("CONTRACT_IDENTITY_MISMATCH")
        if (item.leg.option_type != plan.option_type
                or Decimal(str(item.leg.strike)) != expected["strike"]
                or item.contract_master_sha256 != expected["contract_sha256"]):
            blockers.append("CONTRACT_TERMS_MISMATCH")
        if (item.quote_venue != plan.venue or item.master_venue != plan.venue
                or item.contract_master_expiry != plan.expiry
                or item.contract_master_lot_size != plan.lot_size
                or quote.expiry != plan.expiry or quote.lot_size != plan.lot_size):
            blockers.append("CONTRACT_TERMS_MISMATCH")
        if observed > available or available > cutoff:
            blockers.append("QUOTE_NOT_AVAILABLE_AT_AS_OF")
        if cutoff - observed > max_quote_age:
            blockers.append("STALE_CLOSE_QUOTE")
        if (not isfinite(quote.bid) or not isfinite(quote.ask) or quote.bid <= 0
                or quote.ask < quote.bid):
            blockers.append("INVALID_CLOSE_BOOK")
        displayed = quote.bid_qty if expected["action"] == "BUY" else quote.ask_qty
        if type(displayed) is not int or displayed <= 0:
            blockers.append("NO_CLOSE_DEPTH")
        closable = min(quantities[token], displayed // plan.lot_size) if not blockers else 0
        closed_lots[token] = closable
        if closable == 0:
            closeout_rows.append({"instrument_token": token, "closed_lots": 0,
                                  "reason": ",".join(blockers) if blockers else "INSUFFICIENT_CLOSE_DEPTH"})
            continue
        close_price = Decimal(str(quote.bid if expected["action"] == "BUY" else quote.ask))
        units = closable * plan.lot_size
        price_pnl = ((close_price - average_fill[token]) if expected["action"] == "BUY"
                     else (average_fill[token] - close_price)) * units
        allocated_entry_fee = fees[token] * Decimal(closable) / Decimal(quantities[token])
        close_side = Side.SELL if expected["action"] == "BUY" else Side.BUY
        close_fee = Decimal(str(model.one_way(
            turnover=close_price * units, side=close_side, segment=Segment.OPT,
            include_impact=False,
        )["total"]))
        net = price_pnl - allocated_entry_fee - close_fee
        closeout_pnl += net
        closeout_rows.append({"instrument_token": token, "closed_lots": closable,
                              "close_price": str(close_price), "net_pnl_inr": str(net),
                              "quote_source_sha256": item.quote_source_sha256})

    tail = []
    for spot in spots:
        pnl = Decimal(0)
        for token, expected in legs.items():
            lots = quantities[token]
            if not lots:
                continue
            intrinsic = (max(spot - expected["strike"], Decimal(0)) if plan.option_type == "CE"
                         else max(expected["strike"] - spot, Decimal(0)))
            sign = Decimal(1) if expected["action"] == "BUY" else Decimal(-1)
            pnl += sign * (intrinsic - average_fill[token]) * lots * plan.lot_size
            pnl -= fees[token] * Decimal(lots) / Decimal(quantities[token])
        tail.append({"spot_price": str(spot), "stress_pnl_less_entry_fees_inr": str(pnl)})

    full_close = all(closed_lots[token] == quantities[token] for token in legs)
    if not any(quantities.values()):
        status = "NO_FILLS"
    elif long_lots == plan.target_lots and short_lots == plan.target_lots:
        status = "FULLY_FILLED"
    else:
        status = "PARTIAL_OR_LEGGED"
    body = {
        "underlying": plan.underlying, "venue": plan.venue, "option_type": plan.option_type,
        "expiry": plan.expiry.isoformat(), "lot_size": plan.lot_size,
        "target_lots": plan.target_lots, "as_of": cutoff.isoformat(),
        "fills": fill_rows, "margin_source_sha256": plan.margin_source_sha256,
        "closeout": closeout_rows, "tail_scenarios": tail,
    }
    evidence_sha = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return {
        "status": status,
        "filled_lots": {str(token): quantities[token] for token in legs},
        "unfilled_lots": {str(token): pending[token] for token in legs},
        "unmatched_long_lots": unmatched_long_lots,
        "unmatched_short_lots": unmatched_short_lots,
        "legging_exposure": bool(unmatched_long_lots or unmatched_short_lots),
        "margin_reserved_inr": str(margin_reserved),
        "closeout_status": "FULL" if full_close else "PARTIAL_OR_UNRESOLVED",
        "closeout_net_pnl_inr": str(closeout_pnl) if full_close else None,
        "partial_closeout_pnl_inr": str(closeout_pnl),
        "closeout_legs": closeout_rows,
        "tail_scenarios": tail,
        "worst_stress_pnl_inr": str(min((Decimal(row["stress_pnl_less_entry_fees_inr"])
                                         for row in tail), default=Decimal(0))),
        "evidence_sha256": evidence_sha,
    }
