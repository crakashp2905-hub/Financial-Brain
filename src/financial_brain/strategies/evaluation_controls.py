"""Matched controls (ALG-097) and probability of backtest overfitting (ALG-102).

Matched controls pick, for each event, the nearest unrelated candidate on covariates known before the event
(12-1 momentum, 52-week-high proximity, volatility and sector). A candidate can match only once per event and never
the event's own symbol.

PBO follows the combinatorially symmetric cross-validation idea: split the return history into an even number of
blocks, choose the best strategy in-sample for each half-and-half split, and record how often that choice ranks in
the bottom half out-of-sample. A value near 0.5 means selection is no better than chance.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass(frozen=True)
class Candidate:
    symbol: str
    sector: str
    momentum_12_1: float
    high_52w_ratio: float
    volatility: float


def matched_controls(events: Sequence[Candidate], pool: Sequence[Candidate]) -> list[dict[str, Any]]:
    """Nearest same-sector control per event on standardised covariates; each control is used at most once."""
    if not events:
        return []
    def z(values: list[float]) -> tuple[float, float]:
        m = sum(values) / len(values)
        sd = math.sqrt(sum((v - m) ** 2 for v in values) / max(len(values) - 1, 1)) or 1.0
        return m, sd
    feats = [(c.momentum_12_1, c.high_52w_ratio, c.volatility) for c in list(events) + list(pool)]
    stats = [z([f[i] for f in feats]) for i in range(3)]

    def distance(a: Candidate, b: Candidate) -> float:
        total = 0.0
        for i, (x, y) in enumerate(zip((a.momentum_12_1, a.high_52w_ratio, a.volatility),
                                        (b.momentum_12_1, b.high_52w_ratio, b.volatility), strict=True)):
            m, sd = stats[i]
            total += ((x - m) / sd - (y - m) / sd) ** 2
        return math.sqrt(total)

    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for ev in events:
        best: tuple[float, int] | None = None
        for j, c in enumerate(pool):
            if j in used or c.symbol == ev.symbol or c.sector != ev.sector:
                continue
            d = distance(ev, c)
            if best is None or (d, j) < best:
                best = (d, j)
        if best is None:
            out.append({"event": ev.symbol, "control": None, "reason": "NO_SAME_SECTOR_CONTROL"})
            continue
        used.add(best[1])
        out.append({"event": ev.symbol, "control": pool[best[1]].symbol, "distance": round(best[0], 6)})
    return out


def probability_of_backtest_overfitting(returns: Sequence[Sequence[float]], blocks: int = 8) -> float:
    """PBO over a T x N matrix of strategy returns (rows are time, columns are strategies).

    ``blocks`` must be even. For each half-and-half split, the in-sample best strategy is chosen by mean return; its
    out-of-sample relative rank w is converted to a logit, and PBO is the share of logits at or below zero.
    """
    if blocks < 4 or blocks % 2:
        raise ValueError("blocks must be an even number of at least four")
    t = len(returns)
    if t < blocks or any(len(r) != len(returns[0]) for r in returns):
        raise ValueError("returns matrix must be rectangular with at least one row per block")
    n = len(returns[0])
    if n < 2:
        raise ValueError("PBO needs at least two strategies")
    size = t // blocks
    chunks = [list(range(b * size, (b + 1) * size)) for b in range(blocks)]
    logits: list[float] = []
    for is_blocks in combinations(range(blocks), blocks // 2):
        is_rows = [r for b in is_blocks for r in chunks[b]]
        oos_rows = [r for b in range(blocks) if b not in is_blocks for r in chunks[b]]
        means_is = [sum(returns[r][k] for r in is_rows) / len(is_rows) for k in range(n)]
        best = max(range(n), key=lambda k: (means_is[k], -k))
        means_oos = [sum(returns[r][k] for r in oos_rows) / len(oos_rows) for k in range(n)]
        rank = 1 + sum(1 for k in range(n) if means_oos[k] < means_oos[best])
        omega = rank / (n + 1)
        logits.append(math.log(omega / (1 - omega)))
    return sum(1 for x in logits if x <= 0) / len(logits)
