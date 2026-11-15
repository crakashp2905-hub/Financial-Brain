"""Preregistered chronological cohort blocks and regime concentration diagnostics (FE-0100)."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone


class CohortBlockError(ValueError):
    """Invalid preregistered cohort block plan or outcome coverage."""


@dataclass(frozen=True)
class ChronologicalBlockPlan:
    plan_id: str
    frozen_at: datetime
    first_eligible_session: date
    block_starts: tuple[date, ...]
    concentration_limit: float = 0.8

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise CohortBlockError("plan_id is required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise CohortBlockError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise CohortBlockError("blocks must be frozen before the first eligible session")
        if len(self.block_starts) < 3 or self.block_starts[0] != self.first_eligible_session:
            raise CohortBlockError("at least three preregistered blocks must start at the first session")
        if tuple(sorted(self.block_starts)) != self.block_starts or len(set(self.block_starts)) != len(self.block_starts):
            raise CohortBlockError("block starts must be unique and chronological")
        if not math.isfinite(self.concentration_limit) or not 0 < self.concentration_limit <= 1:
            raise CohortBlockError("concentration limit must be in (0, 1]")

    def digest(self) -> str:
        body = {"plan_id": self.plan_id, "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "block_starts": [day.isoformat() for day in self.block_starts],
                "concentration_limit": self.concentration_limit}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class BlockOutcome:
    session: date
    net_alpha: float
    regime: str

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.net_alpha)) or not self.regime.strip():
            raise CohortBlockError("outcome must have finite net alpha and an explicit regime")


@dataclass(frozen=True)
class CohortBlockReport:
    plan_hash: str
    blocks: tuple[dict[str, object], ...]
    total_sessions: int
    overall_mean_net_alpha: float
    positive_blocks: int
    largest_positive_block_share: float | None
    single_lucky_period_flag: bool
    promotion_allowed: bool
    report_hash: str


def evaluate_chronological_blocks(plan: ChronologicalBlockPlan,
                                  outcomes: tuple[BlockOutcome, ...]) -> CohortBlockReport:
    """Attribute every supplied session to its frozen block; do not select boundaries post hoc."""
    if not outcomes:
        raise CohortBlockError("outcomes are required")
    sessions = tuple(item.session for item in outcomes)
    if tuple(sorted(sessions)) != sessions or len(set(sessions)) != len(sessions):
        raise CohortBlockError("outcome sessions must be unique and chronological")
    if sessions[0] < plan.first_eligible_session:
        raise CohortBlockError("outcome predates the cohort plan")
    accum: dict[int, list[BlockOutcome]] = defaultdict(list)
    for item in outcomes:
        eligible_starts = [i for i, start in enumerate(plan.block_starts) if start <= item.session]
        if not eligible_starts:
            raise CohortBlockError("session is outside preregistered blocks")
        accum[eligible_starts[-1]].append(item)
    blocks = []
    block_totals: list[float] = []
    for index, start in enumerate(plan.block_starts):
        entries = accum.get(index, [])
        regime_counts: dict[str, int] = {}
        for row in entries:
            regime_counts[row.regime] = regime_counts.get(row.regime, 0) + 1
        total = sum(row.net_alpha for row in entries)
        block_totals.append(total)
        blocks.append({"block_index": index, "start": start.isoformat(),
                       "end_exclusive": plan.block_starts[index + 1].isoformat()
                       if index + 1 < len(plan.block_starts) else None,
                       "sessions": len(entries), "net_alpha_sum": total,
                       "mean_net_alpha": total / len(entries) if entries else None,
                       "regime_counts": dict(sorted(regime_counts.items()))})
    total = sum(row.net_alpha for row in outcomes)
    positive_totals = [value for value in block_totals if value > 0]
    positive_blocks = len(positive_totals)
    positive_sum = sum(positive_totals)
    concentration = max(positive_totals) / positive_sum if positive_sum else None
    lucky = positive_blocks <= 1 or (concentration is not None and concentration > plan.concentration_limit)
    fields = {"plan_hash": plan.digest(), "blocks": blocks, "total_sessions": len(outcomes),
              "overall_mean_net_alpha": total / len(outcomes), "positive_blocks": positive_blocks,
              "largest_positive_block_share": concentration, "single_lucky_period_flag": lucky,
              "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CohortBlockReport(plan.digest(), tuple(blocks), len(outcomes), total / len(outcomes),
                             positive_blocks, concentration, lucky, False, digest)
