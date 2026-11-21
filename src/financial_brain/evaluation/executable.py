"""Resolve frozen picks against itemized executable outcomes (no implicit fills).

This is a calculation boundary, not a data provider. Callers must supply exact scheduled
sessions and source-hashed outcomes. Missing rows and missing exit data receive an explicit
worst-case penalty; unfilled capital earns only its declared cash rate.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable


class ExecutableResolutionError(ValueError):
    """Invalid or incomplete executable outcome input."""


@dataclass(frozen=True)
class ExecutableOutcome:
    session: date
    strategy: str
    instrument: str
    fill_fraction: float
    gross_return: float | None
    cost_fraction: float
    source_sha256: str

    def body(self) -> dict[str, object]:
        return {"session": self.session.isoformat(), "strategy": self.strategy,
                "instrument": self.instrument, "fill_fraction": self.fill_fraction,
                "gross_return": self.gross_return, "cost_fraction": self.cost_fraction}


def outcome_sha256(outcome: ExecutableOutcome) -> str:
    raw = json.dumps(outcome.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_executable(*, selected: Iterable[tuple[date, str, str]],
                       outcomes: Iterable[ExecutableOutcome], scheduled_days: Iterable[date],
                       horizon_sessions: int, annual_cash_rate: float,
                       missing_fill_loss: float = -1.0) -> dict[str, object]:
    """Compute equal-weight portfolio net returns for a predeclared exact session schedule.

    `selected` is the frozen (session, strategy, instrument) pick set. Outcome return and
    cost fields are decimal fractions of filled notional. A missing row is treated as a
    fully filled position with a -100% return and no fee credit; a row with a known partial
    fill and unknown return applies that penalty only to the filled portion. Unfilled
    capital compounds at the supplied annual cash rate for the stated session horizon.
    """
    schedule = tuple(scheduled_days)
    if not schedule or schedule != tuple(sorted(set(schedule))):
        raise ExecutableResolutionError("scheduled_days must be non-empty, unique and ordered")
    if type(horizon_sessions) is not int or horizon_sessions < 1:
        raise ExecutableResolutionError("horizon_sessions must be a positive integer")
    if not math.isfinite(annual_cash_rate) or annual_cash_rate <= -1:
        raise ExecutableResolutionError("annual_cash_rate must be finite and greater than -100%")
    if not math.isfinite(missing_fill_loss) or not -1 <= missing_fill_loss <= 0:
        raise ExecutableResolutionError("missing_fill_loss must be in [-1, 0]")

    picks = tuple(selected)
    if not picks:
        raise ExecutableResolutionError("selected pick set cannot be empty")
    pick_keys = set(picks)
    if len(pick_keys) != len(picks):
        raise ExecutableResolutionError("duplicate frozen selection")
    if any(day not in schedule or not strategy.strip() or not instrument.strip()
           for day, strategy, instrument in picks):
        raise ExecutableResolutionError("selection is outside schedule or has blank identity")

    outcome_map: dict[tuple[date, str, str], ExecutableOutcome] = {}
    for row in outcomes:
        key = (row.session, row.strategy, row.instrument)
        if key not in pick_keys:
            raise ExecutableResolutionError("outcome does not match a frozen selection")
        if key in outcome_map:
            raise ExecutableResolutionError("duplicate executable outcome")
        numbers = (row.fill_fraction, row.cost_fraction)
        if any(not math.isfinite(value) for value in numbers) or not 0 <= row.fill_fraction <= 1 or row.cost_fraction < 0:
            raise ExecutableResolutionError("fill and cost values must be finite and within bounds")
        if row.gross_return is not None and not math.isfinite(row.gross_return):
            raise ExecutableResolutionError("gross return must be finite or missing")
        if len(row.source_sha256) != 64 or row.source_sha256 != outcome_sha256(row):
            raise ExecutableResolutionError("outcome source hash missing or mismatched")
        outcome_map[key] = row

    cash_return = (1.0 + annual_cash_rate) ** (horizon_sessions / 252.0) - 1.0
    rows: dict[str, dict[str, float | int]] = {}
    total_cost = 0.0
    total_missing = 0
    total_net = 0.0
    for session in schedule:
        session_picks = sorted((p for p in picks if p[0] == session), key=lambda p: (p[1], p[2]))
        if not session_picks:
            raise ExecutableResolutionError(f"scheduled session has no frozen picks: {session.isoformat()}")
        allocation = 1.0 / len(session_picks)
        net = 0.0
        missing = 0
        costs = 0.0
        filled = 0.0
        for _, strategy, instrument in session_picks:
            outcome_row = outcome_map.get((session, strategy, instrument))
            if outcome_row is None:
                fill, gross, cost = 1.0, missing_fill_loss, 0.0
                missing += 1
            else:
                fill, cost = outcome_row.fill_fraction, outcome_row.cost_fraction
                gross = (missing_fill_loss if fill > 0 and outcome_row.gross_return is None
                         else (outcome_row.gross_return or 0.0))
                if fill > 0 and outcome_row.gross_return is None:
                    missing += 1
            net += allocation * (fill * (gross - cost) + (1.0 - fill) * cash_return)
            costs += allocation * fill * cost
            filled += allocation * fill
        rows[session.isoformat()] = {"net_return": net, "cost_fraction": costs,
                                     "filled_fraction": filled, "cash_return": cash_return,
                                     "selected_count": len(session_picks), "missing_count": missing}
        total_net += net
        total_cost += costs
        total_missing += missing
    digest_input = {"schedule": [d.isoformat() for d in schedule],
                    "selected": sorted((d.isoformat(), s, i) for d, s, i in picks),
                    "outcomes": [asdict(o) | {"session": o.session.isoformat()} for o in
                                 sorted(outcome_map.values(), key=lambda x: (x.session, x.strategy, x.instrument))],
                    "horizon_sessions": horizon_sessions, "annual_cash_rate": annual_cash_rate,
                    "missing_fill_loss": missing_fill_loss, "results": rows}
    digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode("utf-8")).hexdigest()
    return {"metric_version": "executable_next_session_v1", "sessions": rows,
            "mean_net_return": total_net / len(rows),
            "total_cost_fraction": total_cost,
            "cash_return_per_session": cash_return,
            "missing_outcome_count": total_missing,
            "source_sha256": digest,
            "promotion_eligible": False}
