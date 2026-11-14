"""Strict reconciliation of a paper book's NAV change to economic components."""
from __future__ import annotations

import re
from decimal import Decimal

from financial_brain.kernel.units import Money, to_decimal

COMPONENTS = (
    "realized_price_pnl",
    "unrealized_price_pnl",
    "carry_pnl",
    "cash_yield_income",
    "financing_cost",
    "tax_cost",
    "slippage_cost",
    "trading_fees",
)
_COST_COMPONENTS = frozenset({"financing_cost", "tax_cost", "slippage_cost", "trading_fees"})


class AttributionError(ValueError):
    """A return bridge is incomplete, unprovenanced, or does not reconcile."""


def reconcile_pnl_attribution(*, components: dict[str, Money], source_hashes: dict[str, str],
                              nav_change: Money, external_flows: Money,
                              tolerance: Decimal = Decimal("0.01")) -> dict:
    """Reconcile total NAV change, adjusted for capital flows, to explicit P&L components.

    Positive cost components are subtracted. Every component, including a true zero,
    must be provided and carry a content hash so unmodeled economics cannot disappear.
    """
    if set(components) != set(COMPONENTS):
        missing = sorted(set(COMPONENTS) - set(components))
        extra = sorted(set(components) - set(COMPONENTS))
        raise AttributionError(f"component set mismatch; missing={missing}, extra={extra}")
    if set(source_hashes) != set(COMPONENTS) or any(
        not isinstance(source_hashes[name], str) or not re.fullmatch(r"[0-9a-f]{64}", source_hashes[name])
        for name in COMPONENTS
    ):
        raise AttributionError("every P&L component requires a lowercase SHA-256 source hash")
    currency = nav_change.currency
    if external_flows.currency != currency or any(value.currency != currency for value in components.values()):
        raise AttributionError("NAV, external flows, and all components must use one reporting currency")
    for name in _COST_COMPONENTS:
        if components[name].amount < 0:
            raise AttributionError(f"{name} must be represented as a non-negative cost")
    limit = to_decimal(tolerance)
    if limit < 0:
        raise AttributionError("tolerance cannot be negative")
    net_components = sum((components[name].amount for name in COMPONENTS if name not in _COST_COMPONENTS), Decimal(0))
    net_components -= sum((components[name].amount for name in _COST_COMPONENTS), Decimal(0))
    flow_adjusted_nav_change = nav_change.amount - external_flows.amount
    difference = flow_adjusted_nav_change - net_components
    if abs(difference) > limit:
        raise AttributionError(f"NAV attribution does not reconcile: residual {difference} {currency}")
    return {
        "currency": currency,
        "component_amounts": {name: str(components[name].amount) for name in COMPONENTS},
        "source_hashes": dict(source_hashes),
        "nav_change": str(nav_change.amount),
        "external_flows": str(external_flows.amount),
        "flow_adjusted_nav_change": str(flow_adjusted_nav_change),
        "net_attributed_pnl": str(net_components),
        "rounding_residual": str(difference),
        "tolerance": str(limit),
        "reconciled": True,
    }
