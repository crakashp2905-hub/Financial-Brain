"""Evidence-bounded periodic reviews for the local mutual-fund paper book.

This module produces review candidates only. It never creates, approves, or submits orders.
Portfolio suggestions are suppressed when NAV marks or the owner's target map are incomplete.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


class MFPeriodicReviewError(ValueError):
    """Invalid review inputs or inconsistent owner targets."""


def _decimal(value: Any, label: str, *, allow_zero: bool = True) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFPeriodicReviewError(f"{label} must be a finite decimal") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise MFPeriodicReviewError(f"{label} must be {'positive' if not allow_zero else 'non-negative'}")
    return result


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MFPeriodicReviewError(f"{label} must be an ISO date") from exc


def build_weekly_review(*, account: dict[str, Any], policy: dict[str, Any],
                        target_weights: dict[str, Any], installments: list[dict[str, Any]],
                        generated_at: datetime, sip_horizon_days: int = 90) -> dict[str, Any]:
    """Build a deterministic MF review from a point-in-time book snapshot.

    Target weights are explicit owner inputs keyed by scheme code and measured against total
    net paper assets (MF positions + cash + pending redemption receivables). No target is
    inferred from past performance. Stale/unavailable NAV or an incomplete target map blocks
    scheme rebalance suggestions. Pending SIP amounts due within the horizon are reserved when
    calculating excess cash; unsettled redemption receivables are never treated as cash.
    """
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise MFPeriodicReviewError("generated_at must be timezone-aware")
    if not isinstance(sip_horizon_days, int) or not 0 <= sip_horizon_days <= 366:
        raise MFPeriodicReviewError("sip_horizon_days must be between zero and 366")
    account_id = str(account.get("account_id", "")).strip()
    if not account_id:
        raise MFPeriodicReviewError("paper account ID is required")
    policy_id = str(policy.get("policy_id", "")).strip()
    if not policy_id:
        raise MFPeriodicReviewError("effective owner policy is required")
    cash = _decimal(account.get("cash_inr"), "cash_inr")
    receivable = _decimal(account.get("pending_redemption_inr", 0), "pending_redemption_inr")
    reserve_fraction = _decimal(policy.get("cash_reserve_fraction"), "cash_reserve_fraction")
    band = _decimal(policy.get("rebalance_band_fraction"), "rebalance_band_fraction")
    if reserve_fraction > 1 or band > 1:
        raise MFPeriodicReviewError("policy fractions must not exceed one")
    if not isinstance(target_weights, dict) or not target_weights:
        raise MFPeriodicReviewError("explicit non-empty target_weights are required")
    targets = {str(code).strip(): _decimal(weight, f"target weight {code}")
               for code, weight in target_weights.items()}
    if any(not code for code in targets):
        raise MFPeriodicReviewError("target scheme codes cannot be empty")
    if sum(targets.values(), Decimal(0)) + reserve_fraction > 1:
        raise MFPeriodicReviewError("scheme target weights plus cash reserve exceed 100%")

    positions = account.get("positions")
    if not isinstance(positions, list):
        raise MFPeriodicReviewError("account positions must be a list")
    values: dict[str, Decimal] = {}
    position_rows = []
    nav_gaps: list[dict[str, str]] = []
    unrealized_pnl = Decimal(0)
    pnl_complete = True
    for pos in positions:
        scheme = str(pos.get("scheme_code", "")).strip()
        if not scheme:
            raise MFPeriodicReviewError("every paper position needs a scheme code")
        value_raw = pos.get("market_value_inr")
        if value_raw is None or pos.get("stale") or pos.get("valuation_status") != "CURRENT":
            nav_gaps.append({"scheme_code": scheme,
                             "status": str(pos.get("valuation_status", "NAV_UNAVAILABLE"))})
            market_value = None
        else:
            market_value = _decimal(value_raw, f"market value for {scheme}")
            values[scheme] = values.get(scheme, Decimal(0)) + market_value
        pnl_raw = pos.get("unrealized_pnl_inr")
        if pnl_raw is None:
            pnl_complete = False
        else:
            try:
                pnl = Decimal(str(pnl_raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise MFPeriodicReviewError(f"unrealized P&L for {scheme} must be decimal") from exc
            if not pnl.is_finite():
                raise MFPeriodicReviewError(f"unrealized P&L for {scheme} must be finite")
            unrealized_pnl += pnl
        position_rows.append({
            "scheme_code": scheme, "plan": str(pos.get("plan", "")),
            "option": str(pos.get("option", "")), "isin": str(pos.get("isin", "")),
            "units": str(_decimal(pos.get("units", 0), f"units for {scheme}")),
            "cost_basis_inr": str(_decimal(pos.get("cost_basis_inr", 0),
                                            f"cost basis for {scheme}")),
            "market_value_inr": str(market_value) if market_value is not None else None,
            "unrealized_pnl_inr": (str(pos.get("unrealized_pnl_inr"))
                                     if pos.get("unrealized_pnl_inr") is not None else None),
            "nav_date": str(pos.get("nav_date")) if pos.get("nav_date") is not None else None,
            "nav_observation_id": (str(pos.get("nav_observation_id"))
                                   if pos.get("nav_observation_id") is not None else None),
            "nav_available_at": (str(pos.get("nav_available_at"))
                                 if pos.get("nav_available_at") is not None else None),
            "valuation_status": str(pos.get("valuation_status", "NAV_UNAVAILABLE")),
        })

    current_codes = set(values)
    unmapped = sorted(current_codes - set(targets))
    total_positions = sum(values.values(), Decimal(0))
    total_assets = total_positions + cash + receivable
    review_through = generated_at.date() + timedelta(days=sip_horizon_days)
    sip_rows = []
    sip_reserve = Decimal(0)
    for item in installments:
        state = str(item.get("current_state", ""))
        scheduled = _as_date(item.get("scheduled_for"), "SIP scheduled_for")
        if state in {"ALLOTTED", "MISSED"} or not generated_at.date() <= scheduled <= review_through:
            continue
        amount = _decimal(item.get("amount_inr"), "SIP amount", allow_zero=False)
        if state in {"SCHEDULED", "PAYMENT_INITIATED"}:
            sip_reserve += amount
        sip_rows.append({"installment_id": str(item.get("installment_id", "")),
                         "plan_id": str(item.get("plan_id", "")),
                         "scheme_code": str(item.get("scheme_code", "")),
                         "scheduled_for": scheduled.isoformat(), "amount_inr": str(amount),
                         "state": state})
    sip_rows.sort(key=lambda row: (row["scheduled_for"], row["installment_id"]))

    can_rebalance = (total_assets > 0 and not nav_gaps and not unmapped)
    rebalance_rows = []
    if can_rebalance:
        for scheme, target_weight in sorted(targets.items()):
            current_value = values.get(scheme, Decimal(0))
            actual_weight = current_value / total_assets
            drift = actual_weight - target_weight
            if drift != 0 and abs(drift) >= band:
                action = "ADD_REVIEW" if drift < 0 else "REDUCE_REVIEW"
            else:
                action = "HOLD_WITHIN_BAND"
            rebalance_rows.append({
                "scheme_code": scheme, "action": action,
                "current_weight": str(actual_weight), "target_weight": str(target_weight),
                "drift": str(drift), "target_value_inr": str(total_assets * target_weight),
                "difference_inr": str(total_assets * target_weight - current_value),
            })
    reserve_target = total_assets * reserve_fraction
    excess_cash = cash - reserve_target - sip_reserve
    if nav_gaps:
        liquidity = {"status": "BLOCKED_DATA_GAP", "candidate_amount_inr": "0",
                    "reason": "incomplete NAV marks make the total-asset reserve estimate unreliable",
                    "action": "NO_REALLOCATION"}
    elif excess_cash > 0:
        liquidity = {"status": "REVIEW_CANDIDATE", "candidate_amount_inr": str(excess_cash),
                    "reason": "cash exceeds the owner reserve after near-term unpaid SIPs",
                    "action": "OWNER_REVIEW_ONLY"}
    elif cash < reserve_target + sip_reserve:
        liquidity = {"status": "RESERVE_SHORTFALL", "candidate_amount_inr": "0",
                    "shortfall_inr": str(reserve_target + sip_reserve - cash),
                    "reason": "cash is below the reserve plus near-term unpaid SIP amounts",
                    "action": "OWNER_REVIEW_ONLY"}
    else:
        liquidity = {"status": "AT_RESERVE", "candidate_amount_inr": "0",
                    "reason": "cash is at the reserve after near-term unpaid SIPs",
                    "action": "NO_REALLOCATION"}

    report: dict[str, Any] = {
        "schema": "fb.mf_weekly_review.v1", "generated_at": generated_at.isoformat(),
        "review_window": {"from": generated_at.date().isoformat(),
                          "through": review_through.isoformat()},
        "account_id": account_id, "policy_id": policy_id,
        "policy_version": policy.get("version"), "policy_sha256": policy.get("policy_sha256"),
        "valuation_known_at": str(account.get("valuation_known_at", "")),
        "totals": {"positions_inr": str(total_positions), "cash_inr": str(cash),
                   "pending_redemption_receivable_inr": str(receivable),
                   "net_paper_assets_inr": str(total_assets) if not nav_gaps else None,
                   "unrealized_pnl_inr": str(unrealized_pnl) if pnl_complete else None,
                   "cash_reserve_target_inr": str(reserve_target) if not nav_gaps else None,
                   "near_term_unpaid_sip_reserve_inr": str(sip_reserve)},
        "positions": position_rows, "upcoming_sips": sip_rows,
        "rebalance_status": ("READY_FOR_OWNER_REVIEW" if can_rebalance else "BLOCKED_DATA_OR_TARGET_GAP"),
        "rebalance_gaps": {"nav": nav_gaps, "target_map_missing_schemes": unmapped},
        "rebalance_candidates": rebalance_rows,
        "liquidity_candidate": liquidity,
        "limitations": ["Paper-book review only; no broker or fund order is created.",
                        "Scheme weights are owner-supplied targets, not inferred recommendations.",
                        "Liquidity candidates are not spendable-cash guarantees; confirm pending obligations and fund terms.",
                        "Tax, exit loads, cutoff eligibility, and settlement timing are not estimated here."],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


__all__ = ["MFPeriodicReviewError", "build_weekly_review"]
