"""Paired, equal-cost signal ablations with explicit research-look multiplicity."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date


class SignalAblationError(ValueError):
    """Ablation inputs are not predeclared, aligned, or cost comparable."""


@dataclass(frozen=True)
class SignalAblationCase:
    ablation_id: str
    removed_signal_id: str
    session_dates: tuple[date, ...]
    full_net_returns: tuple[float, ...]
    ablated_net_returns: tuple[float, ...]
    equal_cost_null_net_returns: tuple[float, ...]
    full_cost_schedule_sha256: str
    ablated_cost_schedule_sha256: str
    null_cost_schedule_sha256: str
    research_look_id: str
    registered_research_look_ids: tuple[str, ...]
    predeclared: bool


def _contrast(left: tuple[float, ...], right: tuple[float, ...]) -> dict[str, float | None]:
    differences = [a - b for a, b in zip(left, right)]
    n = len(differences)
    mean = sum(differences) / n
    variance = sum((value - mean) ** 2 for value in differences) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(variance / n)
    t = mean / se if se > 0 else None
    return {"mean_paired_net_return_difference": mean, "standard_error": se,
            "t_statistic": t, "normal_approximation_two_sided_p": (
                math.erfc(abs(t) / math.sqrt(2)) if t is not None else None),
            "paired_sessions": n}


def evaluate_signal_ablation(case: SignalAblationCase) -> dict[str, object]:
    """Estimate each signal's marginal contribution without changing transaction costs."""
    if not case.predeclared or not case.ablation_id.strip() or not case.removed_signal_id.strip():
        raise SignalAblationError("a named predeclared signal ablation is required")
    n = len(case.session_dates)
    series = (case.full_net_returns, case.ablated_net_returns, case.equal_cost_null_net_returns)
    if n < 2 or any(len(row) != n for row in series):
        raise SignalAblationError("full, ablated and equal-cost null series must align for at least two sessions")
    if tuple(sorted(case.session_dates)) != case.session_dates or len(set(case.session_dates)) != n:
        raise SignalAblationError("ablation sessions must be unique and chronological")
    if any(not math.isfinite(value) or value <= -1 for row in series for value in row):
        raise SignalAblationError("net returns must be finite and above -100 percent")
    hashes = (case.full_cost_schedule_sha256, case.ablated_cost_schedule_sha256,
              case.null_cost_schedule_sha256)
    if len(set(hashes)) != 1 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise SignalAblationError("full, ablated and null cases must share a hashed cost schedule")
    looks = case.registered_research_look_ids
    if (not looks or any(not value.strip() for value in looks)
            or len(set(looks)) != len(looks) or not case.research_look_id.strip()
            or case.research_look_id not in looks):
        raise SignalAblationError("complete unique research-look registry including this look is required")
    with_signal = _contrast(case.full_net_returns, case.ablated_net_returns)
    against_null = _contrast(case.full_net_returns, case.equal_cost_null_net_returns)
    look_count = len(looks)
    for result in (with_signal, against_null):
        p_value = result["normal_approximation_two_sided_p"]
        result["bonferroni_adjusted_p"] = (min(1.0, p_value * look_count)
                                           if p_value is not None else None)
    body: dict[str, object] = {
        "ablation_id": case.ablation_id,
        "removed_signal_id": case.removed_signal_id,
        "predeclared": True,
        "research_look_id": case.research_look_id,
        "registered_research_look_count": look_count,
        "registered_research_look_ids": sorted(looks),
        "session_count": n,
        "cost_schedule_sha256": hashes[0],
        "with_signal_vs_ablated": with_signal,
        "with_signal_vs_equal_cost_null": against_null,
        "interpretation": "paired descriptive estimate; normal p-values ignore serial dependence and do not authorize promotion",
        "promotion_authorized": False,
    }
    body["evidence_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return body
