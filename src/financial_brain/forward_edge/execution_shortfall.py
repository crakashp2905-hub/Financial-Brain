"""Evidence-labeled execution shortfall against decision, touch, arrival and fill-model prices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class ExecutionShortfallError(ValueError):
    """Invalid order, quote or fill evidence for a shortfall report."""


@dataclass(frozen=True)
class ExecutionShortfallInput:
    side: str
    order_quantity: int
    filled_quantity: int
    decision_bid: Decimal
    decision_ask: Decimal
    arrival_bid: Decimal
    arrival_ask: Decimal
    observed_paper_fill: Decimal
    model_expected_fill: Decimal
    decision_quote_status: str  # OBSERVED, HYPOTHETICAL, UNKNOWN
    arrival_quote_status: str  # OBSERVED, HYPOTHETICAL, UNKNOWN
    paper_fill_model_id: str
    decision_at: datetime
    arrival_at: datetime
    fill_at: datetime


def report_execution_shortfall(data: ExecutionShortfallInput) -> dict[str, object]:
    """Report adverse-positive slippage against each reference and an additive bridge.

    Decision and arrival quote status are explicit inputs; a fill-model price is always
    labeled hypothetical. Metrics are quoted in basis points of decision midpoint.
    """
    if data.side not in {"BUY", "SELL"}:
        raise ExecutionShortfallError("side must be BUY or SELL")
    if (type(data.order_quantity) is not int or type(data.filled_quantity) is not int
            or data.order_quantity <= 0 or not 0 < data.filled_quantity <= data.order_quantity):
        raise ExecutionShortfallError("filled quantity must be positive and no greater than order quantity")
    if data.decision_quote_status not in {"OBSERVED", "HYPOTHETICAL", "UNKNOWN"}:
        raise ExecutionShortfallError("decision quote status must be explicit")
    if data.arrival_quote_status not in {"OBSERVED", "HYPOTHETICAL", "UNKNOWN"}:
        raise ExecutionShortfallError("arrival quote status must be explicit")
    if not data.paper_fill_model_id.strip():
        raise ExecutionShortfallError("paper fill model identity is required")
    timestamps = (data.decision_at, data.arrival_at, data.fill_at)
    if any(value.tzinfo is None for value in timestamps) or not data.decision_at <= data.arrival_at <= data.fill_at:
        raise ExecutionShortfallError("execution timestamps must be aware and causally ordered")
    prices = (data.decision_bid, data.decision_ask, data.arrival_bid, data.arrival_ask,
              data.observed_paper_fill, data.model_expected_fill)
    if any(not value.is_finite() for value in prices):
        raise ExecutionShortfallError("execution prices must be finite")
    if (min(data.decision_bid, data.arrival_bid) < 0
            or data.decision_ask < data.decision_bid
            or data.arrival_ask < data.arrival_bid
            or min(data.observed_paper_fill, data.model_expected_fill) <= 0):
        raise ExecutionShortfallError("quotes must be uncrossed and fills positive")
    decision_mid = (data.decision_bid + data.decision_ask) / Decimal(2)
    if decision_mid <= 0:
        raise ExecutionShortfallError("decision midpoint must be positive")
    decision_touch = data.decision_ask if data.side == "BUY" else data.decision_bid
    arrival_mid = (data.arrival_bid + data.arrival_ask) / Decimal(2)
    arrival_touch = data.arrival_ask if data.side == "BUY" else data.arrival_bid
    sign = Decimal(1) if data.side == "BUY" else Decimal(-1)

    def bp(price: Decimal, reference: Decimal) -> float:
        return float(sign * (price - reference) / decision_mid * Decimal(10_000))

    spread_crossing = bp(decision_touch, decision_mid)
    quote_movement = bp(arrival_touch, decision_touch)
    fill_vs_arrival = bp(data.observed_paper_fill, arrival_touch)
    decision_mid_shortfall = bp(data.observed_paper_fill, decision_mid)
    return {
        "side": data.side,
        "order_quantity": data.order_quantity,
        "filled_quantity": data.filled_quantity,
        "decision_mid": str(decision_mid),
        "decision_executable_touch": str(decision_touch),
        "arrival_mid": str(arrival_mid),
        "arrival_executable_touch": str(arrival_touch),
        "observed_paper_fill": str(data.observed_paper_fill),
        "model_expected_fill": str(data.model_expected_fill),
        "shortfall_bp_of_decision_mid": {
            "decision_mid_to_fill": decision_mid_shortfall,
            "decision_mid_to_touch": spread_crossing,
            "decision_touch_to_arrival_touch": quote_movement,
            "arrival_touch_to_observed_fill": fill_vs_arrival,
            "observed_fill_vs_paper_model": bp(data.observed_paper_fill, data.model_expected_fill),
        },
        "bridge_reconciles": abs(decision_mid_shortfall -
                                  (spread_crossing + quote_movement + fill_vs_arrival)) <= 1e-9,
        "reference_evidence": {
            "decision_quote": data.decision_quote_status,
            "arrival_quote": data.arrival_quote_status,
            "paper_fill": "OBSERVED_SIMULATOR_RESULT",
            "paper_fill_model": "HYPOTHETICAL_MODEL_REFERENCE",
        },
        "hypothetical_references": [name for name, status in (
            ("DECISION_QUOTE", data.decision_quote_status),
            ("ARRIVAL_QUOTE", data.arrival_quote_status),
            ("PAPER_FILL_MODEL", "HYPOTHETICAL")) if status == "HYPOTHETICAL"],
        "unknown_references": [name for name, status in (
            ("DECISION_QUOTE", data.decision_quote_status),
            ("ARRIVAL_QUOTE", data.arrival_quote_status)) if status == "UNKNOWN"],
        "paper_fill_model_id": data.paper_fill_model_id,
        "timestamps": {"decision": data.decision_at.isoformat(),
                       "arrival": data.arrival_at.isoformat(), "fill": data.fill_at.isoformat()},
        "interpretation": "positive basis points are adverse; model comparison is not an observed market benchmark",
    }
