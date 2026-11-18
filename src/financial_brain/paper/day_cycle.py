"""Deterministic five-desk trading-day lifecycle (checklist section 10).

The day is a fixed sequence of phases. Each phase runs at most once per (day, phase) key; a re-run
reports SKIPPED_ALREADY_DONE instead of double-counting. A non-business day runs nothing and
fabricates no outcome. A failed pre-open check or a fail-closed condition (stale feed, data error,
unexpected state, drawdown) halts every later trading phase for that day and records why.
Phase handlers are injected, so the runner is testable without any market data.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Phase(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN_EQUITY_ETF = "OPEN_EQUITY_ETF"
    OPEN_OPTIONS = "OPEN_OPTIONS"
    INTRADAY_EXITS = "INTRADAY_EXITS"
    AFTER_MARKET_CANDIDATES = "AFTER_MARKET_CANDIDATES"
    MF_AFTER_NAV = "MF_AFTER_NAV"
    MF_BEFORE_CUTOFF = "MF_BEFORE_CUTOFF"
    DAILY_RECONCILE = "DAILY_RECONCILE"


ORDER = list(Phase)
#: Phases that place or manage paper trades and so stop when the day is halted.
TRADING = {Phase.OPEN_EQUITY_ETF, Phase.OPEN_OPTIONS, Phase.MF_BEFORE_CUTOFF}


class RunStatus(str, Enum):
    DONE = "DONE"
    FAILED = "FAILED"
    HALTED = "HALTED"
    SKIPPED_ALREADY_DONE = "SKIPPED_ALREADY_DONE"
    SKIPPED_NON_BUSINESS_DAY = "SKIPPED_NON_BUSINESS_DAY"


@dataclass
class DayCycle:
    is_business_day: Callable[[date], bool]
    handlers: dict[Phase, Callable[[date], None]]
    done: set[tuple[date, Phase]] = field(default_factory=set)
    halted: dict[date, str] = field(default_factory=dict)
    log: list[tuple[date, Phase, RunStatus, str]] = field(default_factory=list)

    def fail_closed(self, day: date, why: str) -> None:
        self.halted.setdefault(day, why)

    def run(self, day: date) -> list[tuple[Phase, RunStatus]]:
        out = []
        for phase in ORDER:
            status, why = self._one(day, phase)
            self.log.append((day, phase, status, why))
            out.append((phase, status))
        return out

    def _one(self, day: date, phase: Phase) -> tuple[RunStatus, str]:
        if not self.is_business_day(day):
            return RunStatus.SKIPPED_NON_BUSINESS_DAY, ""
        if (day, phase) in self.done:
            return RunStatus.SKIPPED_ALREADY_DONE, ""
        if day in self.halted and phase in TRADING:
            return RunStatus.HALTED, self.halted[day]
        handler = self.handlers.get(phase)
        try:
            if handler is not None:
                handler(day)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, then the day fails closed
            if phase is Phase.PRE_OPEN:
                self.fail_closed(day, f"pre-open failed: {exc}")
            return RunStatus.FAILED, str(exc)
        self.done.add((day, phase))
        return RunStatus.DONE, ""
