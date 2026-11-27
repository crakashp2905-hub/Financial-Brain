"""Validated, paper-only long-premium option plan from an observed quote.

The planner does not generate directional signals or send orders. It binds an
upstream underlying thesis to an exact listed contract and executable quote.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .options import OptionPaperError, Quote, cost_hurdle, option_lot_count
from ..kernel.units import OptionLots
from ..forecasting.index_sizing import (SizingEligibility,
                                         confidence_adjusted_paper_lots)


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    option_type: str
    strike: float
    quote: Quote


def build_long_option_plan(
    contract: OptionContract,
    *,
    decision_at: datetime,
    underlying_price: float,
    invalidation_price: float,
    lots: int | OptionLots,
    stop_premium: float,
    target_premium: float,
    max_quote_age: timedelta,
    calibrated_confidence: float | None = None,
    sizing_eligibility: SizingEligibility | None = None,
) -> dict[str, Any]:
    """Return a complete contract-qualified BUY plan or fail closed.

    Entry uses the observed ask, stop/target are premium levels, and the
    underlying invalidation is explicit. The target must clear estimated
    round-trip charges; this is a paper admission rule, not an edge claim.
    """
    q = contract.quote
    q.validate()
    if not contract.underlying.strip() or contract.option_type not in {"CE", "PE"}:
        raise OptionPaperError("underlying and CE/PE option type are required")
    if not math.isfinite(contract.strike) or contract.strike <= 0:
        raise OptionPaperError("strike must be finite and positive")
    strike_text = f"{contract.strike:g}"
    if not q.symbol.endswith(f"{strike_text}{contract.option_type}"):
        raise OptionPaperError("tradingsymbol does not match the declared strike and CE/PE type")
    lot_count = option_lot_count(lots)
    confidence_evidence = None
    if calibrated_confidence is not None or sizing_eligibility is not None:
        if calibrated_confidence is None or sizing_eligibility is None:
            raise OptionPaperError("confidence sizing requires both calibrated confidence and eligible evidence")
        sized = confidence_adjusted_paper_lots(
            base_risk_capped_lots=lot_count, calibrated_confidence=calibrated_confidence,
            eligibility=sizing_eligibility)
        if sized["status"] != "PAPER_SIZE":
            raise OptionPaperError(f"confidence sizing refused: {sized['reason']}")
        lot_count = int(sized["lots"])
        confidence_evidence = {"confidence": calibrated_confidence,
                               "evidence_sha256": sized["evidence_sha256"],
                               "method": "calibrated_confidence_times_risk_capped_lots"}
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise OptionPaperError("decision timestamp must be timezone-aware")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise OptionPaperError("maximum quote age must be non-negative")
    values = (underlying_price, invalidation_price, stop_premium, target_premium)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise OptionPaperError("underlying, invalidation, stop and target prices must be finite and positive")
    if q.observed_at > decision_at or decision_at - q.observed_at > max_quote_age:
        raise OptionPaperError("contract quote is future-dated or stale at decision time")
    if q.ask_qty < lot_count * q.lot_size:
        raise OptionPaperError("visible ask depth cannot support requested lots")
    if not (0 < stop_premium < q.ask < target_premium):
        raise OptionPaperError("long-premium levels must satisfy stop < ask entry < target")
    if contract.option_type == "CE" and invalidation_price >= underlying_price:
        raise OptionPaperError("call thesis invalidation must be below the underlying decision price")
    if contract.option_type == "PE" and invalidation_price <= underlying_price:
        raise OptionPaperError("put thesis invalidation must be above the underlying decision price")

    hurdle = cost_hurdle(contract.quote, lots=lot_count, expected_exit_bid=target_premium)
    if not hurdle["passes"]:
        raise OptionPaperError("target premium does not clear estimated round-trip charges")
    return {
        "status": "PAPER_PLAN",
        "side": "BUY",
        "underlying": contract.underlying.strip().upper(),
        "option_type": contract.option_type,
        "strike": contract.strike,
        "expiry": q.expiry.isoformat(),
        "symbol": q.symbol,
        "token": q.token,
        "lot_size": q.lot_size,
        "lots": lot_count,
        "confidence_sizing": confidence_evidence or {"status": "NOT_USED"},
        "quantity": lot_count * q.lot_size,
        "entry_premium_band": {"bid": q.bid, "ask": q.ask},
        "entry_price_reference": "observed_ask",
        "stop_premium": stop_premium,
        "target_premium": target_premium,
        "underlying_invalidation": {
            "operator": "<=" if contract.option_type == "CE" else ">=",
            "price": invalidation_price,
            "decision_price": underlying_price,
        },
        "projected_round_trip_charges_inr": hurdle["projected_charges_inr"],
        "projected_reward_inr": hurdle["projected_reward_inr"],
        "settlement_modelled": False,
        "broker_order_created": False,
    }


__all__ = ["OptionContract", "build_long_option_plan"]
