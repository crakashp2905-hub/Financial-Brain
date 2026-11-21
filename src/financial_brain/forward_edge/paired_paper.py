"""Compare automatic and human-reviewed paper sleeves on one frozen opportunity set.

This is an evaluation boundary, not an OMS. It consumes completed paper outcomes,
requires explicit cash outcomes for abstentions, and never infers fills or promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone


class PairedPaperError(ValueError):
    """Paired paper evidence is incomplete, mismatched or temporally invalid."""


AUTO_ACTIONS = {"TAKE", "NO_TRADE"}
HUMAN_ACTIONS = {"APPROVE", "REJECT", "WATCH", "NO_TRADE"}
TAKEN_STATUSES = {"FILLED", "PARTIAL", "NO_FILL", "UNKNOWN"}


@dataclass(frozen=True, slots=True)
class EligibleSignal:
    signal_id: str
    session: date
    instrument_id: str
    signal_at: datetime
    capital_weight: float
    quote_stream_sha256: str
    risk_policy_sha256: str
    cost_model_sha256: str

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.instrument_id.strip():
            raise PairedPaperError("signal and instrument identities are required")
        if self.signal_at.tzinfo is None or self.signal_at.utcoffset() is None:
            raise PairedPaperError("signal_at must be timezone-aware")
        if not math.isfinite(self.capital_weight) or not 0 < self.capital_weight <= 1:
            raise PairedPaperError("capital_weight must be in (0, 1]")
        for name in ("quote_stream_sha256", "risk_policy_sha256", "cost_model_sha256"):
            _sha(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class SleeveOutcome:
    signal_id: str
    action: str
    status: str
    actor: str
    decided_at: datetime
    outcome_available_at: datetime
    net_nav_contribution: float


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PairedPaperError(f"{name} must be a lowercase SHA-256")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PairedPaperError(f"{name} must be timezone-aware")


def compare_paper_sleeves(*, eligible: tuple[EligibleSignal, ...],
                          automatic: tuple[SleeveOutcome, ...],
                          human: tuple[SleeveOutcome, ...],
                          cash_return_by_signal: dict[str, float]) -> dict:
    """Compare two paper books using the exact same frozen signal denominator.

    ``net_nav_contribution`` is each signal's contribution to the sleeve's initial NAV,
    after modeled execution costs. A ``NOT_TAKEN`` outcome must equal the frozen cash
    contribution, so a rejected signal cannot disappear from the denominator. Unknown
    outcomes remain visible and make the report incomplete.
    """
    if not eligible:
        raise PairedPaperError("eligible signal set cannot be empty")
    by_id = {signal.signal_id: signal for signal in eligible}
    if len(by_id) != len(eligible):
        raise PairedPaperError("eligible signal IDs must be unique")
    by_session: dict[date, float] = {}
    for signal in eligible:
        by_session[signal.session] = by_session.get(signal.session, 0.0) + signal.capital_weight
    if any(weight > 1.0 + 1e-12 for weight in by_session.values()):
        raise PairedPaperError("eligible signal capital weights exceed sleeve NAV")
    if set(cash_return_by_signal) != set(by_id):
        raise PairedPaperError("cash outcomes must cover the exact eligible signal set")
    if any(not math.isfinite(value) for value in cash_return_by_signal.values()):
        raise PairedPaperError("cash outcomes must be finite")

    auto_rows = _index_outcomes(automatic, by_id, mode="automatic",
                                cash_return_by_signal=cash_return_by_signal)
    human_rows = _index_outcomes(human, by_id, mode="human",
                                 cash_return_by_signal=cash_return_by_signal)
    sessions = sorted(by_session)
    session_rows = []
    for session in sessions:
        ids = [signal.signal_id for signal in eligible if signal.session == session]
        auto_return = sum(auto_rows[sid].net_nav_contribution for sid in ids)
        human_return = sum(human_rows[sid].net_nav_contribution for sid in ids)
        session_rows.append({"session": session.isoformat(), "eligible_signals": len(ids),
                             "automatic_net_return": auto_return,
                             "human_net_return": human_return,
                             "human_minus_automatic": human_return - auto_return})
    unknown = sum(row.status == "UNKNOWN" for row in automatic + human)
    payload = {
        "schema": "fb.paired_paper_comparison.v1",
        "eligible_signal_ids": sorted(by_id),
        "signal_set_sha256": _digest(sorted(by_id)),
        "sessions": session_rows,
        "automatic_outcomes": _serialize(auto_rows),
        "human_outcomes": _serialize(human_rows),
        "unknown_outcome_count": unknown,
        "complete": unknown == 0,
        "promotion_eligible": False,
        "limitations": ["paired paper comparison only", "not a profitability verdict",
                        "does not authenticate human identity or independent market timestamps"],
    }
    payload["report_sha256"] = _digest(payload)
    return payload


def _index_outcomes(rows: tuple[SleeveOutcome, ...], eligible: dict[str, EligibleSignal], *,
                    mode: str, cash_return_by_signal: dict[str, float]) -> dict[str, SleeveOutcome]:
    indexed: dict[str, SleeveOutcome] = {}
    for row in rows:
        if row.signal_id not in eligible or row.signal_id in indexed:
            raise PairedPaperError(f"{mode} outcomes must uniquely match the eligible set")
        signal = eligible[row.signal_id]
        _aware(row.decided_at, "decided_at")
        _aware(row.outcome_available_at, "outcome_available_at")
        if row.decided_at < signal.signal_at or row.outcome_available_at <= row.decided_at:
            raise PairedPaperError("decisions must follow the signal and precede outcome availability")
        if not math.isfinite(row.net_nav_contribution):
            raise PairedPaperError("net NAV contribution must be finite")
        if mode == "automatic":
            if row.action not in AUTO_ACTIONS or not row.actor.startswith("system:"):
                raise PairedPaperError("automatic outcomes need TAKE/NO_TRADE and a system actor")
        else:
            if row.action not in HUMAN_ACTIONS or not row.actor.strip() or row.actor.startswith("agent:"):
                raise PairedPaperError("human outcomes need a human actor and an allowed review action")
        not_taken = row.action == "NO_TRADE" or row.action in {"REJECT", "WATCH"}
        if not_taken:
            if row.status != "NOT_TAKEN":
                raise PairedPaperError("no-trade/rejected/watch decisions must be NOT_TAKEN")
            expected_cash = signal.capital_weight * cash_return_by_signal[row.signal_id]
            if not math.isclose(row.net_nav_contribution, expected_cash, rel_tol=1e-10, abs_tol=1e-12):
                raise PairedPaperError("NOT_TAKEN return must equal its frozen cash contribution")
        elif row.status not in TAKEN_STATUSES:
            raise PairedPaperError("taken decisions need an explicit fill/unknown status")
        indexed[row.signal_id] = row
    if set(indexed) != set(eligible):
        missing = sorted(set(eligible) - set(indexed))
        raise PairedPaperError(f"missing {mode} outcome rows for {missing}")
    return indexed


def _serialize(rows: dict[str, SleeveOutcome]) -> list[dict]:
    return [{**asdict(row), "decided_at": row.decided_at.astimezone(timezone.utc).isoformat(),
             "outcome_available_at": row.outcome_available_at.astimezone(timezone.utc).isoformat()}
            for _, row in sorted(rows.items())]


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["EligibleSignal", "PairedPaperError", "SleeveOutcome", "compare_paper_sleeves"]
