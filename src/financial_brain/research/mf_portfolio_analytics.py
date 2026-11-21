"""Coverage-aware MF concentration, debt metrics, and FoF look-through analytics."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from ..providers.mf_portfolio_disclosures import (
    MfDisclosureError, PortfolioAssetClass, PortfolioDisclosureRow,
)


@dataclass(frozen=True)
class PortfolioMetrics:
    reported_weight_percent: Decimal
    unreported_weight_percent: Decimal
    top_five_weight_percent: Decimal
    concentration_hhi: Decimal
    unknown_asset_weight_percent: Decimal
    sector_coverage_weight_percent: Decimal
    sector_weights_percent: dict[str, Decimal]
    debt_weight_percent: Decimal
    duration_coverage_weight_percent: Decimal
    weighted_modified_duration_years: Decimal | None
    ytm_coverage_weight_percent: Decimal
    weighted_yield_to_maturity_percent: Decimal | None
    credit_rating_weights_percent: dict[str, Decimal]


def analyze_portfolio(rows: Sequence[PortfolioDisclosureRow]) -> PortfolioMetrics:
    """Summarize reported weights while showing missing/unclassified coverage."""
    if not rows:
        raise MfDisclosureError("portfolio snapshot has no rows")
    record_ids = [row.record_id for row in rows]
    if len(record_ids) != len(set(record_ids)):
        raise MfDisclosureError("duplicate disclosure rows would double-count portfolio weight")
    total = sum((Decimal(row.weight_percent) for row in rows), Decimal(0))
    if total > Decimal("100.5"):
        raise MfDisclosureError("reported portfolio weights exceed 100.5 percent")

    exposures: dict[str, Decimal] = defaultdict(Decimal)
    sector_weights: dict[str, Decimal] = defaultdict(Decimal)
    credit_weights: dict[str, Decimal] = defaultdict(Decimal)
    unknown = sector_coverage = debt = duration_weight = duration_sum = Decimal(0)
    ytm_weight = ytm_sum = Decimal(0)
    for row in rows:
        weight = Decimal(row.weight_percent)
        identity = row.asset_id or f"UNIDENTIFIED:{row.record_id}"
        exposures[identity] += weight
        if row.asset_class == PortfolioAssetClass.UNKNOWN:
            unknown += weight
        if row.asset_class == PortfolioAssetClass.EQUITY and row.sector:
            sector_weights[row.sector] += weight
            sector_coverage += weight
        if row.asset_class == PortfolioAssetClass.DEBT:
            debt += weight
            if row.modified_duration_years is not None:
                duration_weight += weight
                duration_sum += weight * Decimal(row.modified_duration_years)
            if row.yield_to_maturity_percent is not None:
                ytm_weight += weight
                ytm_sum += weight * Decimal(row.yield_to_maturity_percent)
            if row.credit_rating:
                credit_weights[row.credit_rating] += weight

    descending = sorted(exposures.values(), reverse=True)
    duration = duration_sum / duration_weight if duration_weight else None
    ytm = ytm_sum / ytm_weight if ytm_weight else None
    return PortfolioMetrics(
        reported_weight_percent=total,
        unreported_weight_percent=max(Decimal(0), Decimal(100) - total),
        top_five_weight_percent=sum(descending[:5], Decimal(0)),
        concentration_hhi=sum(((weight / Decimal(100)) ** 2 for weight in descending), Decimal(0)),
        unknown_asset_weight_percent=unknown,
        sector_coverage_weight_percent=sector_coverage,
        sector_weights_percent=dict(sorted(sector_weights.items())),
        debt_weight_percent=debt,
        duration_coverage_weight_percent=duration_weight,
        weighted_modified_duration_years=duration,
        ytm_coverage_weight_percent=ytm_weight,
        weighted_yield_to_maturity_percent=ytm,
        credit_rating_weights_percent=dict(sorted(credit_weights.items())),
    )


@dataclass(frozen=True)
class LookthroughResult:
    root_scheme_code: str
    leaf_exposures_percent: dict[str, Decimal]
    looked_through_weight_percent: Decimal
    unlooked_through_weight_percent: Decimal


def look_through_fof(
    root_scheme_code: str,
    portfolios: Mapping[str, Sequence[PortfolioDisclosureRow]],
    *,
    max_depth: int = 8,
) -> LookthroughResult:
    """Expand FOF units into leaf exposures; preserve gaps and reject graph cycles.

    An underlying-fund row is replaced by its holdings on expansion. It is never also
    counted as a leaf position, which prevents double counting. Identical leaf asset IDs
    from multiple funds are consolidated by summing their economic weights.
    """
    if not root_scheme_code.strip():
        raise MfDisclosureError("root_scheme_code is required")
    if max_depth < 1:
        raise MfDisclosureError("max_depth must be positive")
    leaves: dict[str, Decimal] = defaultdict(Decimal)
    unlooked = Decimal(0)

    def expand(scheme: str, allocated: Decimal, path: tuple[str, ...], depth: int) -> None:
        nonlocal unlooked
        if scheme in path:
            raise MfDisclosureError(f"fund-of-funds cycle detected: {' -> '.join(path + (scheme,))}")
        if depth > max_depth:
            unlooked += allocated
            return
        rows = portfolios.get(scheme)
        if not rows:
            unlooked += allocated
            return
        if any(row.scheme_code != scheme for row in rows):
            raise MfDisclosureError("portfolio map key does not match row scheme identity")
        total = sum((Decimal(row.weight_percent) for row in rows), Decimal(0))
        if total > Decimal("100.5"):
            raise MfDisclosureError(f"portfolio weights exceed 100.5 percent for {scheme}")
        for row in rows:
            contribution = allocated * Decimal(row.weight_percent) / Decimal(100)
            if row.asset_class == PortfolioAssetClass.FUND_OF_FUNDS:
                if row.asset_id is None:
                    unlooked += contribution
                else:
                    expand(row.asset_id, contribution, path + (scheme,), depth + 1)
            else:
                leaf_id = row.asset_id or f"UNIDENTIFIED:{row.record_id}"
                leaves[leaf_id] += contribution
        residual = max(Decimal(0), Decimal(100) - total)
        unlooked += allocated * residual / Decimal(100)

    expand(root_scheme_code, Decimal(100), (), 1)
    looked = sum(leaves.values(), Decimal(0))
    return LookthroughResult(
        root_scheme_code=root_scheme_code,
        leaf_exposures_percent=dict(sorted(leaves.items())),
        looked_through_weight_percent=looked,
        unlooked_through_weight_percent=unlooked,
    )


__all__ = ["LookthroughResult", "PortfolioMetrics", "analyze_portfolio", "look_through_fof"]
