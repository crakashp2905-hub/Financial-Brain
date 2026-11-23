"""Coverage-aware fund-of-funds look-through and overlap estimates.

Weights are percentages of each scheme's NAV. FOF constituents reference another scheme
by scheme code; ordinary securities use a stable instrument identifier. Repeated leaf
assets are summed once per position path and consolidated by identifier.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


class LookthroughError(ValueError):
    pass


@dataclass(frozen=True)
class Lookthrough:
    exposures: dict[str, float]
    expanded_weight_pct: float
    opaque_weight_pct: float
    coverage_pct: float


def lookthrough(scheme_code: str, portfolios: dict[str, list[dict]], *, max_depth: int = 8) -> Lookthrough:
    if not scheme_code or max_depth < 1:
        raise LookthroughError("scheme_code and positive max_depth are required")
    expanded: dict[str, float] = defaultdict(float)
    opaque = 0.0

    def visit(code: str, scale: float, path: tuple[str, ...], depth: int) -> None:
        nonlocal opaque
        if code in path:
            opaque += scale
            return
        rows = portfolios.get(code)
        if not rows:
            opaque += scale
            return
        weights = []
        for row in rows:
            try:
                weight = float(row["weight_pct"])
            except (KeyError, TypeError, ValueError) as exc:
                raise LookthroughError(f"{code} row has no numeric weight_pct") from exc
            if weight < 0 or weight > 100:
                raise LookthroughError(f"{code} weight_pct must be between 0 and 100")
            weights.append((row, weight))
        total = sum(weight for _, weight in weights)
        if total > 100.000001:
            raise LookthroughError(f"{code} disclosed holdings exceed 100% NAV")
        opaque += scale * max(0.0, 100.0 - total) / 100.0
        for row, weight in weights:
            contribution = scale * weight / 100.0
            underlying = row.get("underlying_scheme_code")
            if underlying:
                if depth >= max_depth:
                    opaque += contribution
                else:
                    visit(str(underlying), contribution, path + (code,), depth + 1)
            else:
                asset = str(row.get("instrument_id") or "").strip().upper()
                if not asset or str(row.get("asset_type", "")).upper() in {"UNKNOWN", "UNCLASSIFIED"}:
                    opaque += contribution
                else:
                    expanded[asset] += contribution

    visit(scheme_code, 100.0, (), 0)
    expanded_total = sum(expanded.values())
    total = expanded_total + opaque
    coverage = 100.0 * expanded_total / total if total else 0.0
    return Lookthrough(dict(sorted(expanded.items())), expanded_total, opaque, coverage)


def overlap(left: Lookthrough, right: Lookthrough) -> dict:
    """Return shared constituent mass, counting each leaf only once per side."""
    common = set(left.exposures) & set(right.exposures)
    shared = sum(min(left.exposures[key], right.exposures[key]) for key in common)
    left_cov, right_cov = left.coverage_pct, right.coverage_pct
    return {"shared_weight_pct": shared,
            "left_expanded_pct": left.expanded_weight_pct,
            "right_expanded_pct": right.expanded_weight_pct,
            "left_coverage_pct": left_cov, "right_coverage_pct": right_cov,
            "coverage_adjusted_overlap_pct": shared * min(left_cov, right_cov) / 100.0,
            "common_assets": sorted(common)}
