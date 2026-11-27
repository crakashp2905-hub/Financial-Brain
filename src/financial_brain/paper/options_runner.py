"""Replay explicit long-option intents against archived, observed two-sided quotes.

This module never creates an option signal or calls a broker. An intent must
exist before its entry quote; both fills must be observed before expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import Counter
from collections.abc import Mapping
import math

from .options import OptionPaperError, Quote, cost_hurdle, long_round_trip, option_lot_count
from ..kernel.units import OptionLots
from financial_brain.forward_edge.evidence_labels import metadata


@dataclass(frozen=True)
class Intent:
    # signal_id identifies the option execution hypothesis; directional_signal_id is
    # a separate upstream hypothesis and never supplies its outcome score.
    signal_id: str
    directional_signal_id: str
    directional_evidence_sha256: str
    option_evidence_sha256: str
    cohort: str
    rule_version: str
    token: int
    decided_at: datetime
    planned_exit_at: datetime
    expected_exit_bid: float
    lots: int | OptionLots = 1
    max_entry_delay: timedelta = timedelta(minutes=1)
    max_exit_delay: timedelta = timedelta(minutes=1)
    max_quote_age: timedelta = timedelta(seconds=30)
    option_setup_id: str = "UNSPECIFIED"
    delta_at_decision: float | None = None
    directional_prediction: str | None = None  # frozen UP, DOWN, or FLAT forecast
    underlying_price_at_decision: float | None = None
    flat_tolerance_bps: float = 0.0

    def __post_init__(self) -> None:
        # Keep the durable payload scalar-compatible after validating the domain unit.
        object.__setattr__(self, "lots", option_lot_count(self.lots))

    def validate(self) -> None:
        if not self.signal_id or not self.directional_signal_id or not self.cohort or not self.rule_version:
            raise OptionPaperError("separate option/directional signals, cohort and rule version are required")
        if self.signal_id == self.directional_signal_id:
            raise OptionPaperError("directional and option hypotheses need distinct signal IDs")
        for name in ("directional_evidence_sha256", "option_evidence_sha256"):
            digest = getattr(self, name)
            if (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
                raise OptionPaperError(f"{name} must be a lowercase SHA-256 evidence reference")
        if not self.option_setup_id.strip():
            raise OptionPaperError("option setup ID is required")
        if (self.delta_at_decision is not None
                and (not math.isfinite(self.delta_at_decision)
                     or not -1 <= self.delta_at_decision <= 1)):
            raise OptionPaperError("decision delta must be finite and between -1 and 1")
        if self.directional_prediction is not None:
            if self.directional_prediction not in {"UP", "DOWN", "FLAT"}:
                raise OptionPaperError("directional prediction must be UP, DOWN, or FLAT")
            if (self.underlying_price_at_decision is None
                    or not math.isfinite(self.underlying_price_at_decision)
                    or self.underlying_price_at_decision <= 0
                    or not math.isfinite(self.flat_tolerance_bps)
                    or self.flat_tolerance_bps < 0):
                raise OptionPaperError("frozen direction forecast requires valid decision price and flat tolerance")
        elif self.underlying_price_at_decision is not None:
            raise OptionPaperError("decision underlying price requires a frozen directional prediction")
        if self.decided_at.tzinfo is None or self.planned_exit_at.tzinfo is None:
            raise OptionPaperError("option intent timestamps must be timezone-aware")
        if self.planned_exit_at <= self.decided_at:
            raise OptionPaperError("planned exit must follow the signal")
        option_lot_count(self.lots)
        if (self.max_entry_delay <= timedelta(0) or self.max_exit_delay <= timedelta(0)
                or self.max_quote_age <= timedelta(0)):
            raise OptionPaperError("quote delay limits must be positive")


@dataclass(frozen=True)
class DirectionalOutcome:
    """Caller-supplied underlying price observation for scoring a frozen direction forecast.

    The source digest is preserved for lineage but is not independently authenticated by
    this replay module. An absent mapping entry remains an unscored direction outcome.
    """
    directional_signal_id: str
    observed_at: datetime
    outcome_underlying_price: float
    source_sha256: str

    def validate(self) -> None:
        if not self.directional_signal_id.strip():
            raise OptionPaperError("directional outcome requires signal ID")
        if self.observed_at.tzinfo is None or self.outcome_underlying_price <= 0:
            raise OptionPaperError("directional outcome requires an aware timestamp and positive underlying price")
        values = (self.outcome_underlying_price,)
        if (any(not math.isfinite(value) for value in values)
                or self.outcome_underlying_price <= 0):
            raise OptionPaperError("directional outcome price must be finite and positive")
        if (len(self.source_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.source_sha256)):
            raise OptionPaperError("directional outcome source must be a lowercase SHA-256 reference")


def score_direction_separately(outcome: DirectionalOutcome, *, predicted_direction: str | None,
                               decision_at: datetime, decision_underlying_price: float | None,
                               flat_tolerance_bps: float, option_net_pnl_inr: float | None) -> dict:
    """Score underlying direction and realized option premium outcome independently."""
    outcome.validate()
    if predicted_direction not in {"UP", "DOWN", "FLAT"}:
        raise OptionPaperError("a frozen UP/DOWN/FLAT forecast is required for directional scoring")
    if (decision_at.tzinfo is None or outcome.observed_at <= decision_at
            or decision_underlying_price is None
            or not math.isfinite(decision_underlying_price) or decision_underlying_price <= 0
            or not math.isfinite(flat_tolerance_bps) or flat_tolerance_bps < 0):
        raise OptionPaperError("direction score requires valid frozen decision data and a later outcome")
    if option_net_pnl_inr is not None and not math.isfinite(option_net_pnl_inr):
        raise OptionPaperError("option net P&L must be finite when supplied")
    realized_return = outcome.outcome_underlying_price / decision_underlying_price - 1.0
    realized_bps = realized_return * 10_000
    realized_direction = ("UP" if realized_bps > flat_tolerance_bps else
                          "DOWN" if realized_bps < -flat_tolerance_bps else "FLAT")
    return {
        "directional_signal_id": outcome.directional_signal_id,
        "predicted_direction": predicted_direction,
        "realized_direction": realized_direction,
        "directional_correct": predicted_direction == realized_direction,
        "underlying_return_bps": realized_bps,
        "option_net_pnl_inr": option_net_pnl_inr,
        "option_premium_profitable": None if option_net_pnl_inr is None else option_net_pnl_inr > 0,
        "directional_source_sha256": outcome.source_sha256,
        "directional_outcome_source_status": "CALLER_SUPPLIED_UNVERIFIED",
        "independently_attested": False,
    }


def run_intent(intent: Intent, quotes: list[Quote]) -> dict:
    """No fill is synthesized when a quote, liquidity or exit is missing."""
    intent.validate()
    lots = option_lot_count(intent.lots)
    base = {"signal_id": intent.signal_id,
            "option_signal_id": intent.signal_id,
            "directional_signal_id": intent.directional_signal_id,
            "directional_evidence_sha256": intent.directional_evidence_sha256,
            "option_evidence_sha256": intent.option_evidence_sha256,
            "directional_prediction": intent.directional_prediction,
            "underlying_price_at_decision": intent.underlying_price_at_decision,
            "direction_flat_tolerance_bps": intent.flat_tolerance_bps,
            "cohort": intent.cohort, "option_setup_id": intent.option_setup_id,
            "delta_at_decision": intent.delta_at_decision,
            "rule_version": intent.rule_version, "mode": "replay", **metadata("replay")}
    ordered = sorted((q for q in quotes if q.token == intent.token), key=lambda q: q.observed_at)
    entry_candidates = [q for q in ordered if intent.decided_at < q.observed_at <=
                        intent.decided_at + intent.max_entry_delay]
    stale_entry = next((q for q in entry_candidates if
                        (q.observed_at - intent.decided_at) > intent.max_quote_age), None)
    zero_depth_entry = next((q for q in entry_candidates if q.bid_qty == 0 or q.ask_qty == 0), None)
    entry = next((q for q in entry_candidates if _valid(q)
                  and (q.observed_at - intent.decided_at) <= intent.max_quote_age
                  and q.bid_qty > 0 and q.ask_qty > 0), None)
    if entry is None:
        if stale_entry is not None:
            return {**base, "status": "NO_FILL", "reason": "STALE_QUOTE", "entry_at": stale_entry.observed_at}
        if zero_depth_entry is not None:
            return {**base, "status": "NO_FILL", "reason": "ZERO_VISIBLE_DEPTH",
                    "entry_at": zero_depth_entry.observed_at}
        return {**base, "status": "NO_ENTRY_QUOTE"}
    hurdle = cost_hurdle(entry, lots=lots, expected_exit_bid=intent.expected_exit_bid)
    if not hurdle["passes"]:
        if entry.ask_qty == 0:
            reason = "ZERO_VISIBLE_DEPTH"
        elif entry.ask_qty < lots * entry.lot_size:
            reason = "INSUFFICIENT_VISIBLE_DEPTH"
        else:
            reason = "COST_DOMINATED"
        return {**base, "status": "NO_FILL" if reason != "COST_DOMINATED" else "SKIPPED_COST_OR_LIQUIDITY",
                **hurdle, "reason": reason}
    exit_candidates = [q for q in ordered if intent.planned_exit_at <= q.observed_at <=
                       intent.planned_exit_at + intent.max_exit_delay and q.observed_at > entry.observed_at
                       ]
    stale_exit = next((q for q in exit_candidates if
                       (q.observed_at - intent.planned_exit_at) > intent.max_quote_age), None)
    zero_depth_exit = next((q for q in exit_candidates if q.bid_qty == 0), None)
    exit_quote = next((q for q in exit_candidates if _valid(q)
                       and (q.observed_at - intent.planned_exit_at) <= intent.max_quote_age
                       and q.bid_qty >= lots * q.lot_size
                       and (q.symbol, q.expiry, q.lot_size) ==
                       (entry.symbol, entry.expiry, entry.lot_size)), None)
    if exit_quote is None:
        if stale_exit is not None:
            return {**base, "status": "NO_FILL", "reason": "STALE_QUOTE", "entry_at": entry.observed_at}
        if zero_depth_exit is not None:
            return {**base, "status": "NO_FILL", "reason": "ZERO_VISIBLE_DEPTH", "entry_at": entry.observed_at}
        return {**base, "status": "NO_EXIT_QUOTE", "entry_at": entry.observed_at,
                "entry_premium_inr": entry.ask * entry.lot_size * lots}
    spread_bps = (entry.ask - entry.bid) / ((entry.ask + entry.bid) / 2) * 10_000
    expiry_days = (entry.expiry - entry.observed_at.date()).days
    delta = intent.delta_at_decision
    delta_bucket = ("UNREPORTED" if delta is None else
                    "0.00-0.25" if abs(delta) < 0.25 else
                    "0.25-0.50" if abs(delta) < 0.50 else
                    "0.50-0.75" if abs(delta) < 0.75 else "0.75-1.00")
    spread_bucket = ("0-25_BPS" if spread_bps < 25 else "25-100_BPS" if spread_bps < 100
                     else "100-300_BPS" if spread_bps < 300 else "300_PLUS_BPS")
    expiry_bucket = "0-1D" if expiry_days <= 1 else "2-7D" if expiry_days <= 7 else "8_PLUS_D"
    return {**base, "status": "CLOSED", "option_type": "CE" if entry.symbol.endswith("CE") else
            "PE" if entry.symbol.endswith("PE") else "UNKNOWN",
            "entry_spread_bps": spread_bps, "spread_bucket": spread_bucket,
            "expiry_days_at_entry": expiry_days, "expiry_bucket": expiry_bucket,
            "delta_bucket": delta_bucket,
            **long_round_trip(entry, exit_quote, lots=lots)}


def _valid(q: Quote) -> bool:
    try:
        q.validate()
    except OptionPaperError:
        return False
    return True


def archive_quotes(con, intent: Intent, *, max_source_lag: timedelta = timedelta(seconds=30)) -> list[Quote]:
    """Read only actually archived quotes; discard delayed source timestamps."""
    intent.validate()
    rows = con.execute("""SELECT received_at, source_at, instrument_token, tradingsymbol, expiry,
                                 lot_size, bid, ask, bid_qty, ask_qty
                          FROM option_quotes WHERE instrument_token = ? AND received_at > ?
                            AND received_at <= ? ORDER BY received_at""",
                       [intent.token, intent.decided_at,
                        intent.planned_exit_at + intent.max_exit_delay]).fetchall()
    out = []
    for received, source, token, symbol, expiry, lot, bid, ask, bid_qty, ask_qty in rows:
        if source is None or source > received or received - source > max_source_lag:
            continue
        out.append(Quote(int(token), symbol, expiry, int(lot), received, float(bid or 0),
                         float(ask or 0), int(bid_qty or 0), int(ask_qty or 0)))
    return out


def replay_cohort(con, intents: list[Intent], *,
                  directional_outcomes: Mapping[str, DirectionalOutcome] | None = None) -> dict:
    """Read-only coverage and net-cost report for a frozen set of intents."""
    keys = [(i.cohort, i.rule_version, i.signal_id) for i in intents]
    if len(keys) != len(set(keys)):
        raise OptionPaperError("duplicate cohort/version/signal identity")
    outcomes = dict(directional_outcomes or {})
    signal_ids = {i.directional_signal_id for i in intents}
    if set(outcomes) - signal_ids:
        raise OptionPaperError("directional outcomes contain an ID absent from the frozen intent cohort")
    results = []
    for intent in intents:
        result = run_intent(intent, archive_quotes(con, intent))
        outcome = outcomes.get(intent.directional_signal_id)
        if (outcome is not None and not intent.planned_exit_at <= outcome.observed_at
                <= intent.planned_exit_at + intent.max_exit_delay):
            raise OptionPaperError("directional outcome timestamp must match the frozen option evaluation window")
        result["directional_score"] = (score_direction_separately(
            outcome, predicted_direction=intent.directional_prediction,
            decision_at=intent.decided_at,
            decision_underlying_price=intent.underlying_price_at_decision,
            flat_tolerance_bps=intent.flat_tolerance_bps,
            option_net_pnl_inr=(float(result["net_pnl_inr"])
                                         if result.get("status") == "CLOSED" else None))
            if outcome is not None else None)
        results.append(result)
    closed = [r for r in results if r["status"] == "CLOSED"]
    counts = Counter(r["status"] for r in results)
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for result in results:
        key = (str(result.get("option_type", "UNRESOLVED")),
               str(result.get("expiry_bucket", "UNRESOLVED")),
               str(result.get("delta_bucket", "UNREPORTED")),
               str(result.get("spread_bucket", "UNRESOLVED")))
        groups.setdefault(key, []).append(result)
    attribution = [{"option_type": key[0], "expiry_bucket": key[1],
                    "delta_bucket": key[2], "spread_bucket": key[3],
                    "intents": len(rows),
                    "closed": sum(row["status"] == "CLOSED" for row in rows),
                    "no_fill_or_unresolved": sum(row["status"] != "CLOSED" for row in rows),
                    "net_pnl_inr": sum(float(row.get("net_pnl_inr", 0))
                                        for row in rows if row["status"] == "CLOSED")}
                   for key, rows in sorted(groups.items())]
    scored = [r["directional_score"] for r in results if r.get("directional_score") is not None]
    evaluated_premium = [row for row in scored if row["option_premium_profitable"] is not None]
    direction_metrics = {
        "scored": len(scored),
        "unscored": len(results) - len(scored),
        "directional_accuracy": (sum(row["directional_correct"] for row in scored) / len(scored)
                                 if scored else None),
        "closed_option_premium_profitable_rate": (
            sum(row["option_premium_profitable"] for row in evaluated_premium) / len(evaluated_premium)
            if evaluated_premium else None),
        "closed_option_premium_scored": len(evaluated_premium),
        "source_status": "CALLER_SUPPLIED_UNVERIFIED" if scored else "NOT_COMPUTED_MISSING_UNDERLYING_JOIN",
        "independently_attested": False,
    }
    return {"mode": "replay", **metadata("replay"), "intents": len(intents), "closed": len(closed),
            "coverage": len(closed) / len(intents) if intents else 0.0,
            "status_counts": dict(counts),
            "gross_pnl_inr": sum(r["gross_pnl_inr"] for r in closed),
            "charges_inr": sum(r["charges_inr"] for r in closed),
            "net_pnl_inr": sum(r["net_pnl_inr"] for r in closed),
            "option_attribution": attribution,
            "direction_correctness": "NOT_COMPUTED_UNDERLYING_OUTCOME_JOIN_REQUIRED",
            "direction_metrics": direction_metrics,
            "results": results}
