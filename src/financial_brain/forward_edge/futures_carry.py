"""Conservative futures carry candidate inventory and economics gate (PE-0053)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .registry import Status, StrategySpec, parameter_sha256


class FuturesCarryError(ValueError):
    pass


@dataclass(frozen=True)
class CarryInputs:
    as_of: date
    expiry: date
    spot: Decimal
    future: Decimal
    transaction_cost_per_unit: Decimal | None
    funding_cost_per_unit: Decimal | None
    margin_financing_cost_per_unit: Decimal | None
    dividend_per_unit: Decimal | None
    contract_identity_verified: bool
    corporate_actions_resolved: bool
    data_rights_verified: bool
    eligible_universe_verified: bool
    position_limits_known: bool


def evaluate_carry(inputs: CarryInputs) -> dict[str, object]:
    """Calculate fully itemized per-unit carry only when all inputs and governance checks exist."""
    if inputs.spot <= 0 or inputs.future <= 0 or inputs.expiry <= inputs.as_of:
        raise FuturesCarryError("positive spot/future prices and a future expiry are required")
    amounts = (inputs.transaction_cost_per_unit, inputs.funding_cost_per_unit,
               inputs.margin_financing_cost_per_unit, inputs.dividend_per_unit)
    if any(value is None for value in amounts):
        return {"verdict": "INSUFFICIENT_ECONOMIC_INPUTS", "paper_eligible": False,
                "missing": [name for name, value in zip(("transaction_cost", "funding_cost", "margin_financing_cost",
                                                          "dividend"), amounts, strict=True) if value is None]}
    transaction_cost = inputs.transaction_cost_per_unit
    funding_cost = inputs.funding_cost_per_unit
    margin_financing_cost = inputs.margin_financing_cost_per_unit
    dividend = inputs.dividend_per_unit
    assert transaction_cost is not None and funding_cost is not None
    assert margin_financing_cost is not None and dividend is not None
    costs = (inputs.transaction_cost_per_unit, inputs.funding_cost_per_unit,
             inputs.margin_financing_cost_per_unit)
    if any(value < 0 for value in costs if value is not None) or dividend < 0:
        raise FuturesCarryError("costs and dividend assumptions cannot be negative")
    basis = inputs.future - inputs.spot
    gross = basis + dividend
    net = gross - transaction_cost - funding_cost - margin_financing_cost
    days = (inputs.expiry - inputs.as_of).days
    governance = {
        "contract_identity_verified": inputs.contract_identity_verified,
        "corporate_actions_resolved": inputs.corporate_actions_resolved,
        "data_rights_verified": inputs.data_rights_verified,
        "eligible_universe_verified": inputs.eligible_universe_verified,
        "position_limits_known": inputs.position_limits_known,
    }
    missing_governance = [key for key, ok in governance.items() if not ok]
    verdict = "SHADOW_ONLY_UNRESOLVED_GOVERNANCE" if missing_governance else (
        "NET_CARRY_POSITIVE_RESEARCH_ONLY" if net > 0 else "NO_POSITIVE_NET_CARRY")
    return {
        "verdict": verdict, "paper_eligible": False, "as_of": inputs.as_of.isoformat(),
        "expiry": inputs.expiry.isoformat(), "days_to_expiry": days,
        "basis_per_unit": str(basis), "gross_carry_per_unit": str(gross),
        "transaction_cost_per_unit": str(transaction_cost),
        "funding_cost_per_unit": str(funding_cost),
        "margin_financing_cost_per_unit": str(margin_financing_cost),
        "net_carry_per_unit": str(net),
        "annualized_net_on_spot": str(net / inputs.spot * Decimal(365) / Decimal(days)),
        "governance": governance, "missing_governance": missing_governance,
    }


def candidate_specs() -> tuple[StrategySpec, ...]:
    """Register explicit future carry/basis hypotheses as SHADOW_ONLY; never a paper permit."""
    root = Path(__file__).resolve().parents[1]
    code = hashlib.sha256((root / "evaluation" / "basis.py").read_bytes()
                          + (Path(__file__).with_name("futures.py")).read_bytes()
                          + Path(__file__).read_bytes()).hexdigest()
    definitions = (
        ("FE53-STOCK-CASH-CARRY", "stock_futures_cash_carry", "NSE_EQ+NSE_FUTURES",
         "Long cash instrument, short verified single-expiry future to settlement."),
        ("FE53-STOCK-BASIS", "single_expiry_stock_future_basis", "NSE_FUTURES",
         "Single-expiry stock-futures basis with costs, financing and corporate actions."),
        ("FE53-INDEX-CARRY", "index_futures_carry", "NSE_INDEX+NSE_FUTURES",
         "Index carry proxy; basket creation/redemption costs remain required."),
    )
    return tuple(StrategySpec(
        strategy_id=key, family=family, version=1, code_sha256=code, asset_scope=(asset,),
        decision_clock="daily_close_after_contract_snapshot", direction="LONG_SHORT",
        exit_rule="verified_contract_expiry", horizon_sessions=20,
        data_requirements=("single_expiry_contract_id", "spot_reference", "transaction_costs", "funding",
                           "margin_financing", "dividends", "corporate_actions", "data_rights"),
        source_lineage=f"src/financial_brain/evaluation/basis.py; {claim}", status=Status.SHADOW_ONLY,
        parameter_sha256=parameter_sha256({"family": family, "claim": claim}),
    ) for key, family, asset, claim in definitions)
