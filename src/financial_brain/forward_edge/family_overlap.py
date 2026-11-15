"""Cross-sectional overlap diagnostics for low-volatility and ATR candidate families (PE-0050)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class FamilyOverlapError(ValueError):
    pass


@dataclass(frozen=True)
class FamilyOverlap:
    left_name: str
    right_name: str
    matched_names: int
    spearman_rank_correlation: float
    selected_count_each: int
    overlap_count: int
    jaccard_overlap: float
    classification: str


def _average_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = ((start + 1) + end) / 2.0
        for name, _ in ordered[start:end]:
            ranks[name] = rank
        start = end
    return ranks


def compare_rank_families(left_name: str, left: Mapping[str, float], right_name: str,
                          right: Mapping[str, float], *, quantile: float = 0.2,
                          min_names: int = 10) -> FamilyOverlap:
    """Compare same-direction low-tail families on matched finite names only.

    Missing/non-finite values are excluded rather than imputed. The diagnostic is a redundancy
    measure, not evidence that either family has predictive value.
    """
    if not left_name.strip() or not right_name.strip() or left_name == right_name:
        raise FamilyOverlapError("two distinct non-empty family names are required")
    if not 0 < quantile <= 0.5:
        raise FamilyOverlapError("low-tail quantile must be in (0, 0.5]")
    if min_names < 3:
        raise FamilyOverlapError("min_names must be at least 3")
    common = sorted(set(left) & set(right))
    matched = {name: (left[name], right[name]) for name in common
               if math.isfinite(left[name]) and math.isfinite(right[name])}
    if len(matched) < min_names:
        raise FamilyOverlapError(f"need at least {min_names} matched finite names; got {len(matched)}")
    lvals = {name: pair[0] for name, pair in matched.items()}
    rvals = {name: pair[1] for name, pair in matched.items()}
    lranks, rranks = _average_ranks(lvals), _average_ranks(rvals)
    n = len(matched)
    lm, rm = sum(lranks.values()) / n, sum(rranks.values()) / n
    covariance = sum((lranks[name] - lm) * (rranks[name] - rm) for name in matched)
    lvar = sum((value - lm) ** 2 for value in lranks.values())
    rvar = sum((value - rm) ** 2 for value in rranks.values())
    correlation = covariance / math.sqrt(lvar * rvar) if lvar and rvar else 0.0
    count = max(1, math.ceil(n * quantile))
    left_low = set(sorted(lvals, key=lambda name: (lvals[name], name))[:count])
    right_low = set(sorted(rvals, key=lambda name: (rvals[name], name))[:count])
    intersection = len(left_low & right_low)
    union = len(left_low | right_low)
    jaccard = intersection / union if union else 0.0
    classification = "OVERLAPPING" if correlation >= 0.8 and jaccard >= 0.7 else "DISTINCT_OR_UNPROVEN"
    return FamilyOverlap(left_name, right_name, n, correlation, count, intersection, jaccard, classification)
