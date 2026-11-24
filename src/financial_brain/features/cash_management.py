"""Evidence-gated cash management comparisons for investor paper allocation.

This ranks caller-supplied product scenarios against keeping cash. It is not a
forecast provider, fund screener, tax opinion, or transaction interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Iterable
from zoneinfo import ZoneInfo

from ..forward_edge.data_permissions import DataPermission, DataUse


class CashManagementError(ValueError):
    """Invalid assumptions or evidence for cash allocation analysis."""


PRODUCT_KINDS = {"CASH", "LIQUID_FUND", "OVERNIGHT_FUND", "DEBT_FUND", "ETF"}


def _decimal(value: Decimal | str | int, label: str, *, nonnegative: bool = True) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CashManagementError(f"{label} must be a finite decimal") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise CashManagementError(f"{label} must be {'non-negative' if nonnegative else 'finite'}")
    return result


@dataclass(frozen=True)
class CashAlternative:
    """One scenario with source clocks, costs, settlement lag and risk haircuts.

    Yield, taxes, and stress deductions are explicit scenario inputs, not estimates
    made by this module. All monetary-rate inputs use basis points over their stated
    annual or holding-period basis.
    """

    instrument_id: str
    product_kind: str
    annualized_yield_bps: Decimal
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime
    available_at: datetime
    settlement_lag_business_days: int
    subscription_cost_bps: Decimal = Decimal(0)
    redemption_cost_bps: Decimal = Decimal(0)
    exit_load_bps: Decimal = Decimal(0)
    tax_rate_bps: Decimal | None = None
    stress_loss_bps: Decimal = Decimal(0)
    liquidity_stress_bps: Decimal = Decimal(0)
    capacity_inr: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.source_id.strip() or not self.source_ref.strip():
            raise CashManagementError("instrument and source identity are required")
        kind = self.product_kind.strip().upper()
        if kind not in PRODUCT_KINDS - {"CASH"}:
            raise CashManagementError("alternative kind must be liquid/overnight/debt fund or ETF")
        object.__setattr__(self, "product_kind", kind)
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise CashManagementError("source_sha256 must be lowercase SHA-256")
        clocks = (self.published_at, self.observed_at, self.available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in clocks):
            raise CashManagementError("source clocks must be timezone-aware")
        if self.observed_at < self.published_at or self.available_at < self.published_at:
            raise CashManagementError("observed/available time cannot precede publication")
        if not isinstance(self.settlement_lag_business_days, int) or self.settlement_lag_business_days < 0:
            raise CashManagementError("settlement lag must be a non-negative business-day count")
        object.__setattr__(self, "annualized_yield_bps",
                           _decimal(self.annualized_yield_bps, "annualized_yield_bps", nonnegative=False))
        for name in ("subscription_cost_bps", "redemption_cost_bps", "exit_load_bps",
                     "stress_loss_bps", "liquidity_stress_bps"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if self.tax_rate_bps is not None:
            tax = _decimal(self.tax_rate_bps, "tax_rate_bps")
            if tax > Decimal(10000):
                raise CashManagementError("tax_rate_bps cannot exceed 10000")
            object.__setattr__(self, "tax_rate_bps", tax)
        if self.capacity_inr is not None:
            capacity = _decimal(self.capacity_inr, "capacity_inr")
            object.__setattr__(self, "capacity_inr", capacity)

    def content_sha256(self) -> str:
        payload = {
            "instrument_id": self.instrument_id, "product_kind": self.product_kind,
            "annualized_yield_bps": str(self.annualized_yield_bps),
            "source_id": self.source_id, "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "published_at": self.published_at.isoformat(), "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "settlement_lag_business_days": self.settlement_lag_business_days,
            "subscription_cost_bps": str(self.subscription_cost_bps),
            "redemption_cost_bps": str(self.redemption_cost_bps),
            "exit_load_bps": str(self.exit_load_bps),
            "tax_rate_bps": str(self.tax_rate_bps) if self.tax_rate_bps is not None else None,
            "stress_loss_bps": str(self.stress_loss_bps),
            "liquidity_stress_bps": str(self.liquidity_stress_bps),
            "capacity_inr": str(self.capacity_inr) if self.capacity_inr is not None else None,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_cash_alternatives(
    alternatives: Iterable[CashAlternative],
    *,
    decision_time: datetime,
    principal_inr: Decimal | str | int,
    holding_period_business_days: int,
    max_evidence_age: timedelta,
    permissions: tuple[DataPermission, ...] = (),
    reserve_fraction: Decimal | str | int = Decimal("0.10"),
    max_product_fraction: Decimal | str | int = Decimal("0.50"),
    max_stress_loss_fraction: Decimal | str | int = Decimal("0.005"),
) -> dict:
    """Rank scenarios and allocate a paper amount under reserve, capacity and risk caps.

    Net scenario bps = pro-rated annual yield less estimated tax, entry/exit costs,
    exit load, and explicit stress/liquidity haircuts. Cash is always included at 0 bp.
    A missing tax input, stale/future evidence, excessive payout delay, or a scenario
    below cash makes the product ineligible for allocation. Stress deductions are not
    probabilities or calibrated VaR; they are caller-supplied conservative haircuts.
    """
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise CashManagementError("decision_time must be timezone-aware")
    if holding_period_business_days < 1 or max_evidence_age < timedelta(0):
        raise CashManagementError("holding period must be positive and evidence age non-negative")
    decision = decision_time.astimezone(timezone.utc)
    permission_date = decision.astimezone(ZoneInfo("Asia/Kolkata")).date()
    inputs = tuple(alternatives)
    principal = _decimal(principal_inr, "principal_inr")
    if principal <= 0:
        raise CashManagementError("principal_inr must be positive")
    reserve = _decimal(reserve_fraction, "reserve_fraction")
    product_cap = _decimal(max_product_fraction, "max_product_fraction")
    risk_cap = _decimal(max_stress_loss_fraction, "max_stress_loss_fraction")
    if reserve >= 1 or product_cap <= 0 or product_cap > 1 or risk_cap <= 0:
        raise CashManagementError("reserve/product/risk fractions are outside valid limits")

    scenarios: list[dict] = [{
        "instrument_id": "CASH", "product_kind": "CASH", "net_expected_bps": Decimal(0),
        "gross_yield_bps": Decimal(0), "tax_bps": Decimal(0), "cost_bps": Decimal(0),
        "stress_deduction_bps": Decimal(0), "settlement_lag_business_days": 0,
        "eligible": True, "reasons": [], "source_sha256": None,
    }]
    seen: set[str] = {"CASH"}
    for candidate in inputs:
        if candidate.instrument_id in seen:
            raise CashManagementError("duplicate instrument_id in alternative scenarios")
        seen.add(candidate.instrument_id)
        reasons: list[str] = []
        published = candidate.published_at.astimezone(timezone.utc)
        observed = candidate.observed_at.astimezone(timezone.utc)
        available = candidate.available_at.astimezone(timezone.utc)
        age = decision - observed
        if published > decision or observed > decision or available > decision:
            reasons.append("evidence_not_yet_available")
        elif age < timedelta(0) or age > max_evidence_age:
            reasons.append("evidence_stale")
        if candidate.settlement_lag_business_days > holding_period_business_days:
            reasons.append("payout_lag_exceeds_holding_period")
        if candidate.tax_rate_bps is None:
            reasons.append("tax_assumption_missing")
        required_uses = (DataUse.RESEARCH, DataUse.PAPER_SIMULATION)
        effective_grants = [
            grant for grant in permissions
            if grant.source_id == candidate.source_id and grant.use in required_uses
            and grant.allows(source_id=candidate.source_id, use=grant.use, on=permission_date)
        ]
        granted_uses = {grant.use for grant in effective_grants}
        missing_uses = [use.value for use in required_uses if use not in granted_uses]
        if missing_uses:
            reasons.append("data_rights_missing:" + ",".join(missing_uses))

        gross = _decimal(candidate.annualized_yield_bps, "annualized_yield_bps", nonnegative=False)
        gross *= Decimal(holding_period_business_days) / Decimal(252)
        tax = max(gross, Decimal(0)) * (candidate.tax_rate_bps or Decimal(0)) / Decimal(10000)
        costs = (candidate.subscription_cost_bps + candidate.redemption_cost_bps + candidate.exit_load_bps)
        stress = candidate.stress_loss_bps + candidate.liquidity_stress_bps
        net = gross - tax - costs - stress
        scenarios.append({
            "instrument_id": candidate.instrument_id, "product_kind": candidate.product_kind,
            "net_expected_bps": net, "gross_yield_bps": gross, "tax_bps": tax,
            "cost_bps": costs, "stress_deduction_bps": stress,
            "settlement_lag_business_days": candidate.settlement_lag_business_days,
            "evidence_age_seconds": age.total_seconds(),
            "source_id": candidate.source_id, "source_ref": candidate.source_ref,
            "source_sha256": candidate.source_sha256,
            "scenario_sha256": candidate.content_sha256(),
            "permission_evidence": [
                {"source_id": grant.source_id, "use": grant.use.value,
                 "effective_from": grant.effective_from.isoformat() if grant.effective_from else None,
                 "effective_through": grant.effective_through.isoformat() if grant.effective_through else None,
                 "evidence_ref": grant.evidence_ref, "reviewer": grant.reviewer}
                for grant in sorted(effective_grants, key=lambda grant: grant.use.value)
            ],
            "eligible": not reasons and net > 0, "reasons": reasons + ([] if net > 0 else ["does_not_beat_cash_after_cost_and_risk"]),
        })

    scenarios.sort(key=lambda row: (row["eligible"], row["net_expected_bps"], row["instrument_id"]), reverse=True)
    investable = principal * (Decimal(1) - reserve)
    risk_budget = principal * risk_cap
    allocated_risk = Decimal(0)
    for row in scenarios:
        row["allocation_inr"] = Decimal(0)
        if row["instrument_id"] == "CASH" or not row["eligible"]:
            continue
        candidate = next(c for c in inputs if c.instrument_id == row["instrument_id"])
        remaining_risk = risk_budget - allocated_risk
        loss_fraction = row["stress_deduction_bps"] / Decimal(10000)
        if loss_fraction <= 0:
            row["reasons"].append("stress_loss_haircut_required_for_allocation")
            row["eligible"] = False
            continue
        amount = min(investable, principal * product_cap, remaining_risk / loss_fraction)
        if candidate.capacity_inr is not None:
            amount = min(amount, candidate.capacity_inr)
        if amount <= 0:
            row["reasons"].append("risk_budget_exhausted")
            row["eligible"] = False
            continue
        row["allocation_inr"] = amount
        investable -= amount
        allocated_risk += amount * loss_fraction
    return {
        "status": "SCENARIO_ESTIMATE",
        "decision_time": decision.isoformat(),
        "principal_inr": principal,
        "cash_reserve_inr": principal * reserve,
        "unallocated_cash_inr": principal * reserve + investable,
        "stress_loss_budget_inr": risk_budget,
        "allocated_stress_budget_inr": allocated_risk,
        "scenarios": scenarios,
        "limitations": [
            "Scenario yields, taxes, costs and stress deductions are caller-supplied, not forecasts by this module.",
            "Cash is the zero-return control; no allocation is placed or fund units are created.",
            "Stress deductions are deterministic haircuts, not calibrated probabilities or tail-risk estimates.",
            "Permissioned product universes, point-in-time completeness and prospective outcomes are not established here.",
        ],
    }


__all__ = ["CashAlternative", "CashManagementError", "compare_cash_alternatives"]
