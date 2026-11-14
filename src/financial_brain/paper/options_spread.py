"""Defined-risk debit vertical envelope; no expiry settlement or broker margin claim."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite

from ..costs.india import CostModel, Segment, Side
from .options import OptionPaperError, Quote


@dataclass(frozen=True)
class Leg:
    quote: Quote
    underlying: str
    option_type: str
    strike: float


@dataclass(frozen=True)
class OptionLegEvidence:
    """One option quote joined to its venue contract-master record."""

    leg: Leg
    quote_venue: str
    master_venue: str
    quote_available_at: datetime
    quote_source_sha256: str
    contract_master_sha256: str
    contract_master_expiry: date
    contract_master_lot_size: int


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OptionPaperError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def prepare_debit_vertical(long: OptionLegEvidence, short: OptionLegEvidence, *,
                           decision_at: datetime, expected_venue: str,
                           max_quote_age: timedelta = timedelta(seconds=5),
                           lots: int = 1, costs: CostModel | None = None) -> dict:
    """Fail closed unless every leg has fresh bid/ask and matching venue contract terms.

    ``debit_vertical`` remains the price/risk calculator. This function is the execution
    readiness gate: it binds each quote timestamp and digest to its sourced contract-master
    expiry, lot and venue, and returns no executable economics when any leg fails.
    """
    cutoff = _aware(decision_at, "decision_at")
    if not expected_venue.strip() or not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise OptionPaperError("an expected venue and non-negative quote age are required")
    blockers: list[str] = []
    evidence = []
    for side, item in (("long", long), ("short", short)):
        quote = item.leg.quote
        observed = _aware(quote.observed_at, f"{side} quote observed_at")
        available = _aware(item.quote_available_at, f"{side} quote_available_at")
        if observed > available or available > cutoff:
            blockers.append(f"{side.upper()}_QUOTE_NOT_AVAILABLE_AT_DECISION")
        if (cutoff - observed) > max_quote_age:
            blockers.append(f"{side.upper()}_QUOTE_STALE")
        if item.quote_venue != expected_venue or item.master_venue != expected_venue:
            blockers.append(f"{side.upper()}_VENUE_MISMATCH")
        if (item.contract_master_expiry != quote.expiry
                or item.contract_master_lot_size != quote.lot_size):
            blockers.append(f"{side.upper()}_CONTRACT_TERMS_MISMATCH")
        if (item.leg.underlying.strip() == "" or item.leg.option_type not in {"CE", "PE"}
                or item.leg.strike <= 0):
            blockers.append(f"{side.upper()}_INVALID_CONTRACT_IDENTITY")
        for label, digest in (("quote", item.quote_source_sha256),
                              ("contract", item.contract_master_sha256)):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                blockers.append(f"{side.upper()}_MISSING_{label.upper()}_SHA256")
        if (not isfinite(quote.bid) or not isfinite(quote.ask) or quote.bid <= 0
                or quote.ask < quote.bid or quote.bid_qty <= 0 or quote.ask_qty <= 0):
            blockers.append(f"{side.upper()}_INVALID_OR_INCOMPLETE_BOOK")
        evidence.append({
            "side": side,
            "instrument_id": quote.token,
            "venue": item.quote_venue,
            "master_venue": item.master_venue,
            "expiry": quote.expiry.isoformat(),
            "lot_size": quote.lot_size,
            "observed_at": observed.isoformat(),
            "available_at": available.isoformat(),
            "bid": quote.bid,
            "ask": quote.ask,
            "bid_qty": quote.bid_qty,
            "ask_qty": quote.ask_qty,
            "quote_source_sha256": item.quote_source_sha256,
            "contract_master_sha256": item.contract_master_sha256,
        })
    if long.leg.quote.token == short.leg.quote.token:
        blockers.append("DUPLICATE_OPTION_INSTRUMENT")
    body = {"decision_at": cutoff.isoformat(), "expected_venue": expected_venue,
            "max_quote_age_seconds": max_quote_age.total_seconds(), "legs": evidence,
            "blockers": sorted(set(blockers))}
    digest = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    if blockers:
        return {"status": "BLOCKED_OPTION_LEG_EVIDENCE", "allow_execution": False,
                "blockers": sorted(set(blockers)), "evidence_sha256": digest}
    try:
        economics = debit_vertical(long.leg, short.leg, lots=lots, costs=costs)
    except OptionPaperError as exc:
        return {"status": "BLOCKED_OPTION_SPREAD_VALIDATION", "allow_execution": False,
                "blockers": [str(exc)], "evidence_sha256": digest}
    return {"status": "ELIGIBLE_PAPER_OPTION_SPREAD", "allow_execution": True,
            "blockers": [], "evidence_sha256": digest, "leg_evidence": evidence,
            "economics": economics}


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
