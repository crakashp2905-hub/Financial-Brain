"""Point-in-time option contract routing and premium-bounded long-option sizing.

Routing is a deterministic paper decision over versioned contract records and quote
observations available by the decision time. It does not synthesize missing quotes or
submit broker orders.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from financial_brain.costs.india import CostModel, Segment, Side

from .options import OptionPaperError, Quote

OptionType = Literal["CE", "PE"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptionRoutingError(ValueError):
    """Invalid option contract, quote observation or routing policy."""


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionRoutingError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class OptionContractVersion:
    """Effective-dated listed contract identity with source publication provenance."""

    underlying: str
    option_type: OptionType
    strike: Decimal
    expiry: date
    symbol: str
    token: int
    lot_size: int
    effective_from: date
    effective_to: date | None
    published_at: datetime
    source_sha256: str

    def __post_init__(self) -> None:
        _aware(self.published_at, "published_at")
        if not self.underlying.strip() or self.option_type not in {"CE", "PE"}:
            raise OptionRoutingError("underlying and CE/PE type are required")
        if not self.strike.is_finite() or self.strike <= 0 or self.token <= 0 or self.lot_size <= 0:
            raise OptionRoutingError("strike, provider token and lot size must be positive")
        if not self.symbol.strip() or self.effective_from > self.expiry:
            raise OptionRoutingError("symbol and a valid effective start date are required")
        strike_text = f"{self.strike.normalize():f}"
        if "." in strike_text:
            strike_text = strike_text.rstrip("0").rstrip(".")
        if not self.symbol.endswith(f"{strike_text}{self.option_type}"):
            raise OptionRoutingError("contract symbol does not match its versioned strike and CE/PE type")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise OptionRoutingError("effective_to must be after effective_from")
        if not _SHA256.fullmatch(self.source_sha256):
            raise OptionRoutingError("contract master row needs a lowercase SHA-256 source hash")

    def version_sha256(self) -> str:
        value = {
            **asdict(self),
            "strike": str(self.strike),
            "expiry": self.expiry.isoformat(),
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "published_at": self.published_at.isoformat(),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def valid_as_known(self, decision_at: datetime) -> bool:
        _aware(decision_at, "decision_at")
        day = decision_at.date()
        return (self.published_at <= decision_at and self.effective_from <= day
                and (self.effective_to is None or day < self.effective_to)
                and self.expiry > day)


@dataclass(frozen=True)
class OptionQuoteObservation:
    quote: Quote
    available_at: datetime
    source: str
    exchange: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        _aware(self.available_at, "available_at")
        _aware(self.quote.observed_at, "quote.observed_at")
        if self.quote.observed_at > self.available_at:
            raise OptionRoutingError("quote source time cannot follow its availability time")
        if not self.source.strip() or not self.exchange.strip():
            raise OptionRoutingError("quote source and exchange are required")
        if not _SHA256.fullmatch(self.evidence_sha256):
            raise OptionRoutingError("quote evidence needs a lowercase SHA-256 hash")

    def provenance_sha256(self) -> str:
        q = self.quote
        value = {
            "token": q.token, "symbol": q.symbol, "expiry": q.expiry.isoformat(),
            "lot_size": q.lot_size, "observed_at": q.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(), "bid": q.bid, "ask": q.ask,
            "bid_qty": q.bid_qty, "ask_qty": q.ask_qty, "source": self.source,
            "exchange": self.exchange, "evidence_sha256": self.evidence_sha256,
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class OptionRoutingPolicy:
    """Versioned, explicit expiry/strike/liquidity and premium-budget selection rules."""

    min_dte_days: int = 2
    max_dte_days: int = 45
    target_dte_days: int = 14
    target_moneyness_bps: Decimal = Decimal(0)
    max_spread_bps: Decimal = Decimal(500)
    max_quote_age: timedelta = timedelta(seconds=5)
    premium_budget_inr: Decimal = Decimal("10000")
    max_lots: int = 100
    include_impact_cost: bool = True

    def __post_init__(self) -> None:
        if (self.min_dte_days < 1 or self.max_dte_days < self.min_dte_days
                or not self.min_dte_days <= self.target_dte_days <= self.max_dte_days):
            raise OptionRoutingError("invalid minimum, maximum or target DTE")
        if (not isinstance(self.target_moneyness_bps, Decimal)
                or not isinstance(self.max_spread_bps, Decimal)
                or not isinstance(self.premium_budget_inr, Decimal)
                or not self.target_moneyness_bps.is_finite() or not self.max_spread_bps.is_finite()
                or not self.premium_budget_inr.is_finite() or self.max_spread_bps < 0
                or self.premium_budget_inr <= 0 or self.max_lots < 1):
            raise OptionRoutingError("invalid moneyness, spread, budget or lot cap")
        if not isinstance(self.max_quote_age, timedelta) or self.max_quote_age <= timedelta(0):
            raise OptionRoutingError("max_quote_age must be positive")

    def sha256(self) -> str:
        value = {
            "min_dte_days": self.min_dte_days, "max_dte_days": self.max_dte_days,
            "target_dte_days": self.target_dte_days,
            "target_moneyness_bps": str(self.target_moneyness_bps),
            "max_spread_bps": str(self.max_spread_bps),
            "max_quote_age_seconds": self.max_quote_age.total_seconds(),
            "premium_budget_inr": str(self.premium_budget_inr), "max_lots": self.max_lots,
            "include_impact_cost": self.include_impact_cost,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def route_long_option(*, underlying: str, direction: OptionType, decision_at: datetime,
                      underlying_price: Decimal, observations: list[OptionQuoteObservation],
                      contracts: list[OptionContractVersion], policy: OptionRoutingPolicy,
                      costs: CostModel | None = None) -> dict:
    """Choose one point-in-time executable CE/PE contract and size within premium risk.

    Ranking is fixed by the supplied policy: closest target DTE, closest target
    moneyness, tightest spread, greatest displayed ask depth, then token. Candidate
    selection never reads a quote that was unavailable at ``decision_at``.
    """
    _aware(decision_at, "decision_at")
    if not underlying.strip() or direction not in {"CE", "PE"}:
        raise OptionRoutingError("underlying and CE/PE direction are required")
    if not underlying_price.is_finite() or underlying_price <= 0:
        raise OptionRoutingError("underlying price must be finite and positive")

    by_token: dict[int, list[OptionQuoteObservation]] = {}
    for observation in observations:
        by_token.setdefault(observation.quote.token, []).append(observation)
    model = costs or CostModel()
    candidates: list[tuple[tuple[Decimal | int, ...], OptionContractVersion,
                           OptionQuoteObservation, int, Decimal, Decimal]] = []
    rejected: dict[int, str] = {}

    for contract in contracts:
        if contract.underlying.upper() != underlying.upper() or contract.option_type != direction:
            continue
        if not contract.valid_as_known(decision_at):
            rejected[contract.token] = "CONTRACT_NOT_EFFECTIVE_OR_NOT_YET_PUBLISHED"
            continue
        dte = (contract.expiry - decision_at.date()).days
        if not policy.min_dte_days <= dte <= policy.max_dte_days:
            rejected[contract.token] = "EXPIRY_OUTSIDE_POLICY_WINDOW"
            continue
        eligible = [row for row in by_token.get(contract.token, ())
                    if row.available_at <= decision_at and row.available_at >= decision_at - policy.max_quote_age]
        eligible = [row for row in eligible if
                    (row.quote.symbol, row.quote.token, row.quote.expiry, row.quote.lot_size)
                    == (contract.symbol, contract.token, contract.expiry, contract.lot_size)]
        if not eligible:
            rejected[contract.token] = "NO_FRESH_IDENTITY_MATCHED_QUOTE_AVAILABLE_AT_DECISION"
            continue
        observation = max(eligible, key=lambda row: row.available_at)
        quote = observation.quote
        try:
            quote.validate()
        except OptionPaperError:
            rejected[contract.token] = "INVALID_OR_CROSSED_QUOTE"
            continue
        if (not math.isfinite(quote.bid) or not math.isfinite(quote.ask)
                or type(quote.bid_qty) is not int or type(quote.ask_qty) is not int
                or quote.bid_qty < 0 or quote.ask_qty < 0):
            rejected[contract.token] = "INVALID_QUOTE_PRICE_OR_DEPTH"
            continue
        spread_bps = Decimal(str((quote.ask - quote.bid) / quote.ask * 10_000))
        if spread_bps > policy.max_spread_bps:
            rejected[contract.token] = "SPREAD_ABOVE_POLICY_LIMIT"
            continue
        if quote.ask_qty < quote.lot_size or quote.bid_qty < quote.lot_size:
            rejected[contract.token] = "VISIBLE_DEPTH_BELOW_ONE_LOT"
            continue
        one_lot_premium = Decimal(str(quote.ask)) * quote.lot_size
        one_lot_charges = Decimal(str(model.one_way(
            turnover=float(one_lot_premium), side=Side.BUY, segment=Segment.OPT,
            include_impact=policy.include_impact_cost)["total"]))
        one_lot_all_in = one_lot_premium + one_lot_charges
        if one_lot_all_in > policy.premium_budget_inr:
            rejected[contract.token] = "ONE_LOT_EXCEEDS_PREMIUM_BUDGET"
            continue
        max_by_depth = min(quote.ask_qty, quote.bid_qty) // quote.lot_size
        upper = min(policy.max_lots, max_by_depth)
        low, high, affordable = 1, upper, 0
        while low <= high:
            lots = (low + high) // 2
            premium = one_lot_premium * lots
            charges = Decimal(str(model.one_way(
                turnover=float(premium), side=Side.BUY, segment=Segment.OPT,
                include_impact=policy.include_impact_cost)["total"]))
            if premium + charges <= policy.premium_budget_inr:
                affordable, low = lots, lots + 1
            else:
                high = lots - 1
        if affordable < 1:
            rejected[contract.token] = "PREMIUM_BUDGET_CANNOT_FUND_ONE_LOT"
            continue
        moneyness_bps = (contract.strike / underlying_price - Decimal(1)) * Decimal(10_000)
        rank = (abs(Decimal(dte - policy.target_dte_days)),
                abs(moneyness_bps - policy.target_moneyness_bps),
                spread_bps, -quote.ask_qty, contract.token)
        candidates.append((rank, contract, observation, affordable, one_lot_premium, one_lot_charges))

    if not candidates:
        return {"status": "NO_TRADE", "reason": "NO_ELIGIBLE_OPTION_CONTRACT",
                "simulation_fidelity": "OBSERVED_QUOTE_SIM", "policy_sha256": policy.sha256(),
                "rejections": {str(token): reason for token, reason in sorted(rejected.items())}}

    _rank, contract, observation, lots, one_lot_premium, _one_lot_charges = min(candidates, key=lambda row: row[0])
    quote = observation.quote
    quantity = lots * contract.lot_size
    premium = Decimal(str(quote.ask)) * quantity
    charges = Decimal(str(model.one_way(
        turnover=float(premium), side=Side.BUY, segment=Segment.OPT,
        include_impact=policy.include_impact_cost)["total"]))
    return {
        "status": "PAPER_PLAN", "side": "BUY", "underlying": contract.underlying.upper(),
        "option_type": contract.option_type, "symbol": contract.symbol, "token": contract.token,
        "expiry": contract.expiry.isoformat(), "strike": str(contract.strike),
        "lot_size": contract.lot_size, "lots": lots, "quantity": quantity,
        "entry_price_reference": "observed_ask", "entry_ask": str(quote.ask),
        "entry_bid": str(quote.bid), "entry_premium_inr": str(premium),
        "estimated_entry_charges_inr": str(charges), "all_in_premium_risk_inr": str(premium + charges),
        "dte_days": (contract.expiry - decision_at.date()).days,
        "spread_bps": str((Decimal(str(quote.ask - quote.bid)) / Decimal(str(quote.ask))) * Decimal(10_000)),
        "contract_version_sha256": contract.version_sha256(),
        "contract_source_sha256": contract.source_sha256,
        "quote_provenance_sha256": observation.provenance_sha256(),
        "quote_source": observation.source, "quote_exchange": observation.exchange,
        "quote_evidence_sha256": observation.evidence_sha256,
        "quote_observed_at": quote.observed_at.isoformat(),
        "quote_available_at": observation.available_at.isoformat(),
        "simulation_fidelity": "OBSERVED_QUOTE_SIM",
        "routing_policy_sha256": policy.sha256(), "broker_order_created": False,
        "settlement_modelled": False,
    }


__all__ = ["OptionContractVersion", "OptionQuoteObservation", "OptionRoutingPolicy",
           "OptionRoutingError", "route_long_option"]
