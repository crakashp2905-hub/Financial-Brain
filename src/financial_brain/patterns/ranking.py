"""Observational ranking, quality separation and trader-mode filtering (stage 14).

Ranking here orders *what to look at*. It never produces an expected return: there
is no return field in :class:`Opportunity` and calibration is reported as
``not calibrated`` until valid historical labels exist (CP-0226). Every rank carries
reason codes so it can be reconstructed from stored inputs (CP-0229).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from financial_brain.patterns.detectors import Formation

MODES = ("analyst", "trader")
RESEARCH_ONLY_BADGE = "RESEARCH ONLY - descriptive, not a trade recommendation"


@dataclass(frozen=True)
class Scores:
    """Four distinct concepts, never collapsed into one confidence number (CP-0220)."""

    geometry_fit: float  # 0..1 quality of shape
    data_quality: float  # 0..1; None-equivalent 0 means impossible to compare
    research_evidence: str  # evidence tier label, e.g. DESCRIPTIVE
    calibration: str = "not calibrated"


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    formation: Formation
    scores: Scores
    liquidity_rank: float  # 0..1, higher is more liquid
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


def _state_rank(f: Formation, now: int) -> int:
    state = f.state_at(now)
    return {"CONFIRMED": 2, "CANDIDATE": 1, "FAILED": 0}.get(state or "", -1)


def rank(opps: list[Opportunity], *, now: int, max_age: int) -> list[tuple[Opportunity, list[str]]]:
    """Deterministic ordering by recency, completion, geometry and liquidity.

    Stale formations past ``max_age`` expire rather than live forever (CP-0224).
    Incomplete data is penalised by ranking it last, with an explicit code (CP-0223).
    """
    scored: list[tuple[tuple[Any, ...], Opportunity, list[str]]] = []
    for o in opps:
        codes = list(o.reason_codes)
        age = now - o.formation.candidate_known_index
        if age > max_age:
            codes.append("EXPIRED")
        if o.scores.data_quality <= 0:
            codes.append("MISSING_DATA")
        key = (
            1 if "EXPIRED" in codes or "MISSING_DATA" in codes else 0,  # penalised rows sort last
            -_state_rank(o.formation, now),
            -o.formation.candidate_known_index,
            -o.scores.geometry_fit,
            -o.liquidity_rank,
            o.symbol,
            o.formation.detector,
        )
        scored.append((key, o, codes))
    scored.sort(key=lambda item: item[0])
    return [(o, codes) for _, o, codes in scored]


def dedupe_by_symbol(ranked: list[tuple[Opportunity, list[str]]]) -> dict[str, list[str]]:
    """Group overlapping evidence per symbol into one summary (CP-0222)."""
    out: dict[str, list[str]] = {}
    for o, _ in ranked:
        out.setdefault(o.symbol, []).append(o.formation.detector)
    return out


def similarity_clusters(formations: list[Formation]) -> list[list[int]]:
    """Group formations that share a pivot index, so correlated shapes are not counted as votes (CP-0227)."""
    parent = list(range(len(formations)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[int, int] = {}
    for i, f in enumerate(formations):
        pivots = f.geometry.get("pivots") or f.geometry.get("extrema") or f.geometry.get("shoulders") or []
        for p in pivots:
            if p in owner:
                parent[find(i)] = find(owner[p])
            else:
                owner[p] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(formations)):
        groups.setdefault(find(i), []).append(i)
    return sorted(sorted(g) for g in groups.values())


def contrast(pattern_value: float, momentum_value: float) -> dict[str, float]:
    """Side-by-side baseline signal values (CP-0228)."""
    return {"pattern": pattern_value, "momentum_baseline": momentum_value, "difference": pattern_value - momentum_value}


def net_cost_gate(expected_gross_bps: float | None, cost_bps: float) -> str:
    """Unsupported or non-positive net edge defaults to WATCH, never BUY (CP-0231)."""
    if expected_gross_bps is None:
        return "WATCH"
    return "WATCH" if expected_gross_bps - cost_bps <= 0 else "CANDIDATE_FOR_PAPER_REVIEW"


SCENARIOS = ("continuation", "throwback", "failed_breakout", "sideways_drift")


def scenario_summary(state: str) -> dict[str, str]:
    """Descriptive scenario labels with no fabricated probabilities (CP-0230)."""
    return {
        "state": state,
        "scenarios": ", ".join(SCENARIOS),
        "probabilities": "not available: no calibrated labels",
    }


def filter_opportunities(
    ranked: list[tuple[Opportunity, list[str]]], *, mode: str, min_liquidity: float = 0.0,
    detector: str | None = None,
) -> list[tuple[Opportunity, list[str]]]:
    """Local, deterministic filters (CP-0232). Trader mode drops research-only shapes (CP-0233)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    out = []
    for o, codes in ranked:
        if o.liquidity_rank < min_liquidity:
            continue
        if detector is not None and o.formation.detector != detector:
            continue
        out.append((o, codes))
    return out


def trader_recommendations(ranked: list[tuple[Opportunity, list[str]]]) -> list[Opportunity]:
    """Only evidence above DESCRIPTIVE may appear as a recommendation (CP-0233).

    Everything else is still shown in the radar, badged RESEARCH ONLY, but never listed here.
    """
    return [o for o, _ in ranked if o.scores.research_evidence != "DESCRIPTIVE"]


OVERLAP_PRECEDENCE = (
    "double_bottom", "double_top", "head_shoulders_top", "head_shoulders_bottom",
    "rectangle", "triangle_ascending", "triangle_descending", "triangle_symmetric",
    "wedge_rising", "wedge_falling", "flag_bullish", "flag_bearish",
)


def resolve_overlaps(formations: list[Formation]) -> list[Formation]:
    """Formations that share their pivots describe one structure: keep the highest-precedence one (CP-0146, CP-0089)."""
    def pivots(f: Formation) -> tuple[int, ...]:
        raw = f.geometry.get("pivots") or f.geometry.get("extrema") or f.geometry.get("shoulders") or []
        return tuple(int(x) for x in raw)

    rank_of = {name: i for i, name in enumerate(OVERLAP_PRECEDENCE)}
    kept: dict[tuple[int, ...], Formation] = {}
    loose: list[Formation] = []
    for f in formations:
        key = pivots(f)
        if not key:
            loose.append(f)
            continue
        current = kept.get(key)
        if current is None or rank_of.get(f.detector, 999) < rank_of.get(current.detector, 999):
            kept[key] = f
    return loose + sorted(kept.values(), key=lambda f: (f.candidate_known_index, f.detector))
