"""Timestamp-safe, non-registered ORB inverse/fade research hypothesis (PE-0160).

This module emits an exploratory candidate observation only. It has no sizing,
order, registry-promotion, paper-book, or execution adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


class OrbfadeError(ValueError):
    """Invalid or incomplete exploratory ORB-fade evidence."""


@dataclass(frozen=True)
class OrbFadeHypothesis:
    hypothesis_id: str = "EXPLORATORY-ORB-FAILED-BREAK-FADE-v1"
    evidence_class: str = "EXPLORATORY_RESEARCH"
    registered_as_evidence: bool = False
    mechanism: str = (
        "A confirmed opening-range break that quickly closes back inside the range may reveal failed price acceptance; "
        "temporary stop flow and breakout demand may mean-revert toward the range midpoint."
    )
    payer: str = "Late breakout entrants and liquidity takers caught on the failed move may bear the reversal cost."
    regimes: tuple[str, ...] = (
        "liquid continuous session with complete opening-range and rejection bars",
        "no unresolved halt, corporate action, or material-news exclusion",
    )
    falsifier: str = (
        "The timestamp-safe fade fails to beat same-time no-signal controls after two-sided spread, fees, taxes, "
        "impact, missed fills, and predeclared multiplicity adjustment."
    )
    decision_rule: str = "confirmed_bar_close_back_inside_opening_range"
    entry_cutoff: str = "predeclared_by_exchange_session_plan"
    exit_rule: str = "predeclared_stop_or_intraday_cutoff; no overnight position"
    cost_components: tuple[str, ...] = (
        "entry_spread", "exit_spread", "brokerage_and_exchange_fees", "statutory_taxes",
        "market_impact", "missed_fill_and_reversal_cost",
    )
    cost_budget_policy: str = (
        "Candidate gross edge must exceed 2x the complete round-trip cost at base, 1.5x, and 2x cost stress; "
        "all costs require effective-date source evidence. Missing any leg quote or cost component rejects the scenario."
    )
    quantity: None = None
    order_intent: None = None

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


ORB_FADE_HYPOTHESIS = OrbFadeHypothesis()


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise OrbfadeError(f"{field} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OrbfadeError(f"{field} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OrbfadeError(f"{field} must be timezone-aware")
    return parsed


def _positive(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrbfadeError(f"{field} must be finite and positive") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise OrbfadeError(f"{field} must be finite and positive")
    return parsed


def _nonnegative(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrbfadeError(f"{field} must be finite and non-negative") from exc
    if not parsed.is_finite() or parsed < 0:
        raise OrbfadeError(f"{field} must be finite and non-negative")
    return parsed


def observe_failed_break(observation: dict, *, decision_at: str) -> dict:
    """Return a candidate direction only from a completed, available rejection bar.

    Required times are the end of the opening range, rejection-bar event time,
    rejection-bar availability time, and decision time. The signal uses the
    rejection bar close and its predeclared excursion extreme; no later bar or
    full-day statistic is accepted as input.
    """
    if not isinstance(observation, dict):
        raise OrbfadeError("observation must be an object")
    range_complete = _timestamp(observation.get("opening_range_available_at"), "opening_range_available_at")
    bar_event = _timestamp(observation.get("rejection_bar_event_at"), "rejection_bar_event_at")
    bar_available = _timestamp(observation.get("rejection_bar_available_at"), "rejection_bar_available_at")
    decision = _timestamp(decision_at, "decision_at")
    if not (range_complete <= bar_event <= bar_available <= decision):
        raise OrbfadeError("future or not-yet-available opening-range/rejection data is forbidden")
    high = _positive(observation.get("opening_range_high"), "opening_range_high")
    low = _positive(observation.get("opening_range_low"), "opening_range_low")
    close = _positive(observation.get("rejection_bar_close"), "rejection_bar_close")
    probe_high = _positive(observation.get("probe_high"), "probe_high")
    probe_low = _positive(observation.get("probe_low"), "probe_low")
    if high <= low or probe_high < probe_low:
        raise OrbfadeError("opening range and probe OHLC are inconsistent")
    upper_rejection = probe_high > high and low < close <= high
    lower_rejection = probe_low < low and low <= close < high
    direction = "FADE_SHORT" if upper_rejection else "FADE_LONG" if lower_rejection else "NONE"
    return {
        "hypothesis_id": ORB_FADE_HYPOTHESIS.hypothesis_id,
        "hypothesis_sha256": ORB_FADE_HYPOTHESIS.sha256,
        "evidence_class": ORB_FADE_HYPOTHESIS.evidence_class,
        "status": "EXPLORATORY_CANDIDATE" if direction != "NONE" else "NO_CANDIDATE",
        "direction": direction,
        "decision_at": decision.isoformat(),
        "source_available_at": bar_available.isoformat(),
        "registered_as_evidence": False,
        "quantity": None,
        "order_intent": None,
    }


def cost_budget_scenarios(*, gross_edge_bps: object, entry_spread_bps: object,
                          exit_spread_bps: object, fees_bps: object, taxes_bps: object,
                          impact_bps: object, fee_schedule_id: str,
                          fee_schedule_sha256: str) -> dict:
    """Apply the hypothesis's frozen 2x gross-to-cost gate without claiming a fill or edge."""
    if not isinstance(fee_schedule_id, str) or not fee_schedule_id.strip():
        raise OrbfadeError("effective-date fee schedule identity is required")
    if not isinstance(fee_schedule_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", fee_schedule_sha256):
        raise OrbfadeError("fee schedule SHA-256 is required")
    gross = _positive(gross_edge_bps, "gross_edge_bps")
    components = {
        "entry_spread": _nonnegative(entry_spread_bps, "entry_spread_bps"),
        "exit_spread": _nonnegative(exit_spread_bps, "exit_spread_bps"),
        "fees": _nonnegative(fees_bps, "fees_bps"),
        "taxes": _nonnegative(taxes_bps, "taxes_bps"),
        "impact": _nonnegative(impact_bps, "impact_bps"),
    }
    base = sum(components.values(), Decimal(0))
    scenarios = []
    for multiplier in (Decimal("1"), Decimal("1.5"), Decimal("2")):
        stressed_cost = base * multiplier
        required_gross = stressed_cost * Decimal("2")
        scenarios.append({
            "cost_multiplier": str(multiplier), "round_trip_cost_bps": str(stressed_cost),
            "required_gross_edge_bps": str(required_gross),
            "passes_cost_budget": gross > required_gross,
        })
    return {
        "hypothesis_id": ORB_FADE_HYPOTHESIS.hypothesis_id,
        "fee_schedule_id": fee_schedule_id,
        "fee_schedule_sha256": fee_schedule_sha256,
        "round_trip_cost_components_bps": {name: str(value) for name, value in components.items()},
        "gross_edge_bps_input": str(gross),
        "scenarios": scenarios,
        "all_scenarios_pass": all(row["passes_cost_budget"] for row in scenarios),
        "evidence_class": "EXPLORATORY_RESEARCH",
        "registered_as_evidence": False,
        "quantity": None,
        "order_intent": None,
    }
