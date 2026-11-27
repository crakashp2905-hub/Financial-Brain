"""Transparent paper ranking of exact quoted long option contracts."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from ..costs.india import CostModel, Segment, Side
from ..kernel.options import OptionError, greeks
from .options import OptionPaperError, Quote


@dataclass(frozen=True)
class OptionRankCandidate:
    candidate_id: str
    underlying: str
    option_type: str
    strike: float
    quote: Quote
    implied_volatility: float
    expected_exit_bid: float
    time_to_expiry_years: float
    minutes_to_expiry: float | None = None
    stress_tail_loss_per_lot_inr: float | None = None
    expected_underlying_exit_price: float | None = None


def rank_long_option_contracts(*, candidates: tuple[OptionRankCandidate, ...],
                               underlying_price: float, as_of: datetime,
                               risk_budget_inr: float, max_quote_age_seconds: float,
                               max_spread_bps: float, rate: float = 0.0,
                               dividend_yield: float = 0.0,
                               near_expiry_minutes: float = 60.0,
                               costs: CostModel | None = None) -> dict[str, object]:
    """Rank quoted CE/PE candidates using after-cost reward per risk, failing closed.

    ``expected_exit_bid`` is an upstream scenario estimate, not an observed fill. This
    function produces paper research diagnostics only and never submits an order.
    """
    if (not candidates or as_of.tzinfo is None or as_of.utcoffset() is None
            or not all(math.isfinite(value) for value in
                       (underlying_price, risk_budget_inr, max_quote_age_seconds,
                        max_spread_bps, rate, dividend_yield, near_expiry_minutes))
            or underlying_price <= 0 or risk_budget_inr <= 0
            or max_quote_age_seconds < 0 or max_spread_bps <= 0
            or near_expiry_minutes < 0):
        raise OptionPaperError("valid candidates, aware decision time and finite risk policies are required")
    if len({row.candidate_id for row in candidates}) != len(candidates):
        raise OptionPaperError("candidate IDs must be unique")
    model = costs or CostModel()
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        reasons: list[str] = []
        quote = candidate.quote
        try:
            quote.validate()
        except OptionPaperError:
            reasons.append("INVALID_QUOTE")
        if candidate.option_type not in {"CE", "PE"}:
            reasons.append("UNSUPPORTED_OPTION_RIGHT")
        if (not candidate.candidate_id.strip() or not candidate.underlying.strip()
                or not math.isfinite(candidate.strike) or candidate.strike <= 0):
            reasons.append("INVALID_CONTRACT_IDENTITY")
        if (not math.isfinite(candidate.implied_volatility) or candidate.implied_volatility <= 0
                or not math.isfinite(candidate.expected_exit_bid) or candidate.expected_exit_bid <= 0
                or not math.isfinite(candidate.time_to_expiry_years)
                or candidate.time_to_expiry_years <= 0):
            reasons.append("INVALID_SCENARIO_INPUT")
        if (candidate.minutes_to_expiry is not None
                and (not math.isfinite(candidate.minutes_to_expiry)
                     or candidate.minutes_to_expiry < 0)):
            reasons.append("INVALID_EXPIRY_CLOCK")
        if (candidate.stress_tail_loss_per_lot_inr is not None
                and (not math.isfinite(candidate.stress_tail_loss_per_lot_inr)
                     or candidate.stress_tail_loss_per_lot_inr < 0)):
            reasons.append("INVALID_STRESS_TAIL_LOSS")
        if (candidate.expected_underlying_exit_price is not None
                and (not math.isfinite(candidate.expected_underlying_exit_price)
                     or candidate.expected_underlying_exit_price <= 0)):
            reasons.append("INVALID_UNDERLYING_SCENARIO")
        if quote.observed_at.tzinfo is None or quote.observed_at.utcoffset() is None:
            reasons.append("QUOTE_CLOCK_MISSING")
        else:
            age = (as_of - quote.observed_at).total_seconds()
            if age < 0:
                reasons.append("QUOTE_FROM_FUTURE")
            elif age > max_quote_age_seconds:
                reasons.append("STALE_QUOTE")
        if reasons:
            rows.append({"candidate_id": candidate.candidate_id, "selected": False,
                         "status": "REJECTED", "reason_codes": sorted(set(reasons))})
            continue
        call = candidate.option_type == "CE"
        try:
            greek_values = greeks(underlying_price, candidate.strike,
                                  candidate.time_to_expiry_years, rate,
                                  candidate.implied_volatility, call, dividend_yield)
        except (OptionError, ValueError, OverflowError):
            rows.append({"candidate_id": candidate.candidate_id, "selected": False,
                         "status": "REJECTED", "reason_codes": ["GREEKS_UNAVAILABLE"]})
            continue
        spread_bps = ((quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2) * 10_000)
        quantity_available = quote.ask_qty
        quantity_per_lot = quote.lot_size

        def fee_for(turnover: float, side: Side) -> float:
            return float(model.one_way(turnover=turnover, side=side,
                                       segment=Segment.OPT, include_impact=False)["total"])

        risk_lots = max(0, math.floor(risk_budget_inr / (quote.ask * quantity_per_lot)))
        while risk_lots > 0:
            risk_quantity = risk_lots * quantity_per_lot
            risk_loss = (quote.ask * risk_quantity
                         + fee_for(quote.ask * risk_quantity, Side.BUY))
            if risk_loss <= risk_budget_inr:
                break
            risk_lots -= 1
        depth_lots = quantity_available // quantity_per_lot
        lots = min(risk_lots, depth_lots)
        quantity = lots * quantity_per_lot
        entry_fee = fee_for(quote.ask * quantity, Side.BUY) if lots else 0.0
        exit_fee = fee_for(candidate.expected_exit_bid * quantity, Side.SELL) if lots else 0.0
        max_loss = quote.ask * quantity + entry_fee
        net_reward = ((candidate.expected_exit_bid - quote.ask) * quantity
                      - entry_fee - exit_fee)
        entry_fee_per_lot = fee_for(quote.ask * quantity_per_lot, Side.BUY)
        exit_fee_per_lot = fee_for(candidate.expected_exit_bid * quantity_per_lot, Side.SELL)
        expected_alpha_per_lot = ((candidate.expected_exit_bid - quote.ask) * quantity_per_lot
                                  - entry_fee_per_lot - exit_fee_per_lot)
        spread_cost_per_lot = (quote.ask - quote.bid) * quantity_per_lot
        break_even = (candidate.strike + quote.ask if call
                      else candidate.strike - quote.ask)
        break_even_move_pct = (break_even - underlying_price) / underlying_price * 100
        delta_equivalent = None
        if candidate.expected_underlying_exit_price is not None:
            delta_units = greek_values["delta"] * quantity_per_lot
            delta_equivalent = {
                "underlying_units_per_lot": delta_units,
                "expected_underlying_exit_price": candidate.expected_underlying_exit_price,
                "gross_pnl_per_lot_inr_before_underlying_costs":
                    delta_units * (candidate.expected_underlying_exit_price - underlying_price),
                "calibrated": False,
            }
        if spread_bps > max_spread_bps:
            reasons.append("SPREAD_EXCEEDS_POLICY")
        if depth_lots < 1:
            reasons.append("INSUFFICIENT_ASK_DEPTH")
        if risk_lots < 1:
            reasons.append("RISK_BUDGET_BELOW_ONE_LOT")
        if net_reward <= 0:
            reasons.append("EXPECTED_NET_REWARD_NON_POSITIVE")
        near_expiry = (candidate.minutes_to_expiry is not None
                       and candidate.minutes_to_expiry <= near_expiry_minutes)
        if spread_cost_per_lot >= expected_alpha_per_lot:
            reasons.append("SPREAD_COST_EXCEEDS_EXPECTED_ALPHA")
        if near_expiry and candidate.stress_tail_loss_per_lot_inr is None:
            reasons.append("NEAR_EXPIRY_TAIL_RISK_UNMEASURED")
        elif (candidate.stress_tail_loss_per_lot_inr is not None
              and candidate.stress_tail_loss_per_lot_inr >= expected_alpha_per_lot):
            reasons.append("TAIL_GAP_EXCEEDS_EXPECTED_ALPHA")
        rows.append({"candidate_id": candidate.candidate_id,
                     "contract": quote.symbol, "token": quote.token,
                     "underlying": candidate.underlying, "option_type": candidate.option_type,
                     "strike": candidate.strike, "expiry": quote.expiry.isoformat(),
                     "selected": False, "status": "ELIGIBLE" if not reasons else "REJECTED",
                     "reason_codes": sorted(set(reasons)),
                     "greeks": greek_values, "break_even_price": break_even,
                     "break_even_move_percent": break_even_move_pct,
                     "gamma_exposure_per_lot": greek_values["gamma"] * quantity_per_lot,
                     "theta_exposure_per_day_per_lot": greek_values["theta"] * quantity_per_lot / 365,
                     "observed_bid": quote.bid, "observed_ask": quote.ask,
                     "spread_bps": spread_bps, "visible_ask_contracts": quantity_available,
                     "spread_cost_per_lot_inr": spread_cost_per_lot,
                     "expected_alpha_per_lot_inr": expected_alpha_per_lot,
                     "minutes_to_expiry": candidate.minutes_to_expiry,
                     "stress_tail_loss_per_lot_inr": candidate.stress_tail_loss_per_lot_inr,
                     "delta_equivalent_benchmark": delta_equivalent,
                     "risk_capped_lots": risk_lots, "depth_capped_lots": depth_lots,
                     "recommended_lots": lots if not reasons else 0,
                     "maximum_loss_inr": max_loss,
                     "projected_net_reward_inr": net_reward,
                     "reward_to_max_loss": net_reward / max_loss if max_loss > 0 else 0.0,
                     "ranking_score": net_reward / max_loss
                     if lots > 0 and not reasons else None,
                     "entry_price_reference": "OBSERVED_ASK",
                     "expected_exit_bid_is_observed": False})
    eligible = [row for row in rows if row["status"] == "ELIGIBLE"]
    if eligible:
        winner = max(eligible, key=lambda row: (cast(float, row["ranking_score"]),
                                                cast(float, row["projected_net_reward_inr"]),
                                                str(row["candidate_id"])))
        winner["selected"] = True
        winner["status"] = "SELECTED"
        for row in eligible:
            if row is not winner:
                row["status"] = "NOT_SELECTED"
                row["reason_codes"] = ["LOWER_REWARD_TO_MAX_LOSS"]
    output: dict[str, object] = {
        "schema": "fb.paper-option-ranking.v1", "as_of": as_of.isoformat(),
        "paper_only": True, "risk_budget_inr": risk_budget_inr,
        "candidates": sorted(rows, key=lambda row: str(row["candidate_id"])),
        "selected_candidate_id": winner["candidate_id"] if eligible else None,
        "selected_action": "OPTION_CANDIDATE" if eligible else "CASH_NO_ACTION",
        "cash_no_action_baseline": {"expected_net_pnl_inr": 0.0, "risk_inr": 0.0,
                                    "always_available": True},
        "selection_reason": ("HIGHEST_AFTER_COST_REWARD_TO_MAX_LOSS_AMONG_ELIGIBLE"
                             if eligible else "NO_CANDIDATE_CLEARED_ALL_GATES"),
        "calibrated": False,
    }
    canonical = json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False)
    output["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return output
