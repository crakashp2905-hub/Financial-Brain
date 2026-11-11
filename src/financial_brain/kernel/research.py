"""Research integrity checks (FB-0150, FB-0152, FB-0157, FB-0164, FB-0170 subset, FB-0490, FB-0491).

* ``holm_adjust``: Holm step-down family-wise correction over a set of p-values.
* ``placebo_outperforms``: a strategy that does not beat its randomised or reversed placebo is flagged.
* ``reversed_signal``: the same rule with inverted direction; a strategy whose reversal is as good is suspect.
* ``effective_sample_size``: a deterministic overlap-aware count, so overlapping events are not counted as
  independent.
* ``capsule``: a reproducibility capsule whose digest changes if code, data or parameters change.
* ``confidence_status``: returns INSUFFICIENT_EVIDENCE below a minimum effective sample rather than a verdict.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, (m - rank) * p_values[idx])
        running = max(running, val)
        adjusted[idx] = running
    return adjusted


def placebo_outperforms(strategy_metric: float, placebo_metrics: Sequence[float]) -> bool:
    """True when at least as many placebos match or beat the strategy: the strategy is flagged, not promoted."""
    if not placebo_metrics:
        raise ValueError("placebo set must not be empty")
    beaten = sum(1 for p in placebo_metrics if p >= strategy_metric)
    return beaten / len(placebo_metrics) >= 0.5


def reversed_signal(signal: Sequence[int]) -> list[int]:
    return [-s for s in signal]


def effective_sample_size(event_starts: Sequence[int], horizon: int) -> int:
    """Count events whose holding windows do not overlap the previous counted event."""
    count, last_end = 0, -10**18
    for s in sorted(event_starts):
        if s >= last_end:
            count += 1
            last_end = s + horizon
    return count


def capsule(*, code_sha: str, data_sha256: str, params: dict[str, Any], result: Any) -> dict[str, Any]:
    body = {"code_sha": code_sha, "data_sha256": data_sha256, "params": params, "result": result}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {**body, "digest": digest}


def confidence_status(effective_n: int, minimum: int = 30) -> str:
    return "SUFFICIENT" if effective_n >= minimum else "INSUFFICIENT_EVIDENCE"
