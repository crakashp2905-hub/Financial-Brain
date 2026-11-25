"""Per-position lifecycle ledger, manual decisions, MTM view and sleeve comparison
(FB-0471, FB-0485, FB-0486, FB-0487, FB-0494, FB-0902, FB-1105, FB-1109, FB-1212, FB-2106).

Events are append-only with an idempotency key and an immutable snapshot of what was known.
Human decisions are separate events; they never edit the original algorithmic plan.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LedgerError(ValueError):
    pass


class Ev(str, Enum):
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    ENTRY_INTENT = "ENTRY_INTENT"
    ENTRY_FILL = "ENTRY_FILL"
    STOP_TP_ARMED = "STOP_TP_ARMED"
    EXIT_TRIGGER = "EXIT_TRIGGER"
    EXIT_INTENT = "EXIT_INTENT"
    EXIT_FILL = "EXIT_FILL"
    POSITION_CLOSED = "POSITION_CLOSED"
    HUMAN_DECISION = "HUMAN_DECISION"


ORDER = [Ev.ENTRY_SIGNAL, Ev.ENTRY_INTENT, Ev.ENTRY_FILL, Ev.STOP_TP_ARMED, Ev.EXIT_TRIGGER,
         Ev.EXIT_INTENT, Ev.EXIT_FILL, Ev.POSITION_CLOSED]

ALERT_KINDS = ("BUY_READY", "SELL_READY", "STOP_HIT", "TP_HIT", "TRAIL_MOVED", "ENTRY_EXPIRED",
               "NO_FILL", "EXIT_UNPRICED", "MF_NAV_PENDING", "POSITION_CLOSED")


@dataclass(frozen=True)
class Record:
    key: str
    position_id: str
    account: str          # funding account / sleeve: AUTONOMOUS or TRADER_APPROVED
    instrument: str
    kind: Ev
    at: datetime
    snapshot: tuple[tuple[str, str], ...]


@dataclass
class PositionLedger:
    records: list[Record] = field(default_factory=list)

    def append(self, r: Record) -> bool:
        if any(x.key == r.key for x in self.records):
            return False
        own = [x.kind for x in self.records if x.position_id == r.position_id and x.kind in ORDER]
        if r.kind in ORDER:
            if r.kind == Ev.ENTRY_SIGNAL and own:
                raise LedgerError("position already has an entry signal")
            if r.kind != Ev.ENTRY_SIGNAL:
                if not own:
                    raise LedgerError("event before entry signal")
                if ORDER.index(r.kind) < ORDER.index(own[-1]) and r.kind not in (Ev.EXIT_TRIGGER, Ev.EXIT_INTENT, Ev.EXIT_FILL):
                    raise LedgerError(f"{r.kind.value} out of order after {own[-1].value}")
                if Ev.POSITION_CLOSED in own:
                    raise LedgerError("position already closed")
                if r.kind == Ev.POSITION_CLOSED and Ev.EXIT_FILL not in own:
                    raise LedgerError("cannot close without an exit fill")
        self.records.append(r)
        return True

    def history(self, position_id: str) -> list[Record]:
        return [r for r in self.records if r.position_id == position_id]

    def original_plan(self, position_id: str) -> dict[str, str]:
        sig = [r for r in self.history(position_id) if r.kind == Ev.ENTRY_SIGNAL]
        if not sig:
            raise LedgerError("no entry signal")
        return dict(sig[0].snapshot)


@dataclass(frozen=True)
class MtmRow:
    position_id: str
    remaining: float
    last: float | None
    quote_age_s: float | None
    unrealized: float | None
    stop_distance: float
    target_distance: float
    cost_to_exit: float
    live: bool


def mtm(position_id: str, entry: float, remaining: float, stop: float, target: float,
        last: float | None, quote_at: datetime | None, now: datetime, max_age_s: float,
        cost_to_exit: float) -> MtmRow:
    """Live mark only when the quote is fresh; otherwise ``live`` is False and P&L is withheld."""
    age = (now - quote_at).total_seconds() if quote_at else None
    live = last is not None and age is not None and age <= max_age_s
    unreal = (last - entry) * remaining - cost_to_exit if live and last is not None else None
    ref = last if last is not None else entry
    return MtmRow(position_id, remaining, last, age, unreal, ref - stop, target - ref, cost_to_exit, live)


def by_exit_reason(trades: list[tuple[str, float]]) -> dict[str, dict[str, float]]:
    """Net performance grouped by exit reason (FB-0494)."""
    g: dict[str, list[float]] = defaultdict(list)
    for reason, pnl in trades:
        g[reason].append(pnl)
    return {k: {"n": len(v), "net": sum(v), "mean": statistics.fmean(v)} for k, v in sorted(g.items())}


def ablate(plain: list[float], variant: list[float]) -> float:
    """Mean difference of a variant exit rule against the frozen plain SL/TP on the same entries."""
    if len(plain) != len(variant) or not plain:
        raise LedgerError("ablation needs the same entries")
    return statistics.fmean(v - p for p, v in zip(plain, variant, strict=True))


def compare_sleeves(signals: list[str], auto: dict[str, float], approved: dict[str, float]) -> dict:
    """Paired comparison on the same eligible signals; selection effect separated (FB-1212/2106)."""
    both = [s for s in signals if s in auto and s in approved]
    skipped_by_human = [s for s in signals if s in auto and s not in approved]
    exec_effect = sum(approved[s] - auto[s] for s in both)
    selection_effect = -sum(auto[s] for s in skipped_by_human)
    return {"paired": len(both), "execution_effect": exec_effect, "selection_effect": selection_effect,
            "auto_total": sum(auto.get(s, 0.0) for s in signals),
            "approved_total": sum(approved.get(s, 0.0) for s in signals)}


def attribution_chain(ledger: PositionLedger, position_id: str) -> list[str]:
    """thesis -> entry signal -> fill -> SL/TP -> exit -> close, as recorded (FB-1109)."""
    return [f"{r.at.isoformat()} {r.kind.value} {dict(r.snapshot)}" for r in ledger.history(position_id)]
