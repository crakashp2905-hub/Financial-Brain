"""Strategy families, research hypotheses, adapters, synthetic generators, verdicts and release gates
(ALG-095..096, ALG-098, ALG-100, ALG-106..108, ALG-122, ALG-125, ALG-127).

A family must declare its null, alternative, sample and complexity before any result is read. Parameter variants
are counted as trials once each, after de-duplication. Verdicts are REJECT when the paired after-cost difference
against the baseline is not positive. EDGE_CONFIRMED is never produced by this package.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from financial_brain.patterns.spec import canonical_json, sha256_hex
from financial_brain.strategies.breakouts import basic_breakouts
from financial_brain.strategies.specs import AlgorithmSpec

FAMILY_HYPOTHESES: dict[str, dict[str, str]] = {
    "trend_breakout": {"null": "after-cost incremental return vs momentum is zero or negative", "alternative": "after-cost incremental return vs momentum is positive", "sample": "DEVELOPMENT_ONLY", "complexity": "fixed lookback 20 and 10"},
    "session_breakout": {"null": "opening-range breakout return equals the time-of-day placebo", "alternative": "breakout beats the placebo after costs", "sample": "DEVELOPMENT_ONLY", "complexity": "range 5/15/30 minutes"},
    "volatility_expansion": {"null": "ATR breakout return equals the no-trade benchmark after costs", "alternative": "ATR breakout beats no-trade after costs", "sample": "DEVELOPMENT_ONLY", "complexity": "one ATR multiple"},
    "mean_reversion": {"null": "reversion return equals zero after costs", "alternative": "reversion return is positive after costs", "sample": "DEVELOPMENT_ONLY", "complexity": "one z-score window"},
    "structural_reversal": {"null": "confirmed reversal return equals the cash benchmark", "alternative": "confirmed reversal beats cash after costs", "sample": "DEVELOPMENT_ONLY", "complexity": "pivot strength and tolerance"},
    "trend_control": {"null": "control matches or beats a straightforward lagged rule", "alternative": "control adds value over simple rules", "sample": "DEVELOPMENT_ONLY", "complexity": "one EMA period"},
    "structure": {"null": "structure labels carry no information", "alternative": "structure labels predict after-cost returns", "sample": "DEVELOPMENT_ONLY", "complexity": "pivot window"},
    "research_method": {"null": "walk-forward selection does not reduce out-of-sample loss", "alternative": "walk-forward selection reduces it", "sample": "METHOD_ONLY", "complexity": "method"},
    "baseline": {"null": "not applicable", "alternative": "not applicable", "sample": "CONTROL", "complexity": "none"},
}


def family_complete(family: str) -> bool:
    """Every family declares a null, an alternative, a sample and complexity (ALG-095)."""
    h = FAMILY_HYPOTHESES.get(family)
    if h is None:
        return False
    return all(h.get(k) for k in ("null", "alternative", "sample", "complexity"))


def effective_trials(specs: Sequence[AlgorithmSpec]) -> int:
    """Distinct effective trials: de-duplicated by spec hash, so re-labelled copies count once (ALG-096)."""
    return len({s.spec_sha256() for s in specs})


def cohort_report(pre_confirmation: Sequence[float], post_confirmation: Sequence[float]) -> dict[str, dict[str, float]]:
    """Pre- and post-confirmation cohorts stay in separate blocks and never mix (ALG-098)."""
    def stats(x: Sequence[float]) -> dict[str, float]:
        return {"count": float(len(x)), "total": float(sum(x))}
    return {"pre_confirmation": stats(pre_confirmation), "post_confirmation": stats(post_confirmation)}


def paired_incremental(strategy_net: Sequence[float], baseline_net: Sequence[float]) -> dict[str, float]:
    """Net paired difference used for ranking (ALG-100). Win rate is never the ranking key."""
    if len(strategy_net) != len(baseline_net) or not strategy_net:
        raise ValueError("paired series must be non-empty and equal length")
    diffs = [s - b for s, b in zip(strategy_net, baseline_net, strict=True)]
    return {"mean_diff": sum(diffs) / len(diffs), "total_diff": sum(diffs), "n": float(len(diffs))}


def verdict(incremental: dict[str, float]) -> str:
    """REJECT unless the paired after-cost difference is positive. Never returns an edge label (ALG-125)."""
    return "KEEP_RESEARCHING" if incremental["total_diff"] > 0 else "REJECT"


def release_gate(forward_observations: int) -> str:
    """EDGE_CONFIRMED and live use stay blocked until independent forward observations exist (ALG-127)."""
    if forward_observations <= 0:
        return "BLOCKED_NO_FORWARD_EVIDENCE"
    return "FORWARD_EVIDENCE_PRESENT_REVIEW_REQUIRED"


class StrategyAdapter(Protocol):
    """One typed protocol for every algorithm (ALG-107)."""

    name: str

    def signals(self, closes: Sequence[float], highs: Sequence[float]) -> list[int]: ...


@dataclass(frozen=True)
class DonchianAdapter:
    name: str = "donchian_20"
    lookback: int = 20

    def signals(self, closes: Sequence[float], highs: Sequence[float]) -> list[int]:
        return [s.index for s in basic_breakouts(closes, highs, self.lookback)]


def synthetic_breakout(seed: int, n: int = 120) -> tuple[list[float], list[float]]:
    """Trend-then-break synthetic series (ALG-108). Labelled synthetic; not market data."""
    rng = random.Random(seed)
    closes: list[float] = []
    level = 100.0
    for i in range(n):
        level += 0.0 if i < n * 0.6 else 0.8
        closes.append(level + rng.uniform(-0.4, 0.4))
    highs = [c + 0.3 for c in closes]
    return closes, highs


def synthetic_reversal(seed: int, n: int = 120) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    closes = [100 + 4 * (1 if (i // 15) % 2 else -1) + rng.uniform(-0.3, 0.3) for i in range(n)]
    return closes, [c + 0.3 for c in closes]


def synthetic_noisy_range(seed: int, n: int = 120) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    closes = [100 + rng.uniform(-1.5, 1.5) for _ in range(n)]
    return closes, [c + 0.4 for c in closes]


def card(entry_id: str, spec: AlgorithmSpec, source_url: str, paper_only: bool = True) -> dict[str, Any]:
    """Strategy card with source link, spec hash and paper-only banner (ALG-122, ALG-081)."""
    body = {"source_id": entry_id, "source_url": source_url, "spec_sha256": spec.spec_sha256(),
            "family": spec.family, "evidence_tier": spec.evidence_tier, "paper_only": paper_only,
            "label": "SOURCE_CLAIM" if entry_id else "UNSOURCED"}
    body["card_sha256"] = sha256_hex(canonical_json(body))
    return body
