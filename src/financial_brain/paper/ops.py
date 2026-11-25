"""Paper operations glue (FB-1003, FB-1006, FB-1007, FB-1012, FB-1102, FB-1702, FB-1801, FB-1802,
FB-1808, FB-2422).

* ``SignalTimeline``: per-signal timestamps from source to exit, with latencies.
* ``FeedMode``: STREAM vs labelled POLLING/REPLAY fallback; only STREAM is evidence-grade for instants.
* ``replay``: feed immutable recorded events through the same decision function used live.
* ``CapitalGateway``: the single spending authority; existing allocators (``risk/allocator.py``,
  ``portfolio/optimizer.py``, ``portfolio/next_capital.py``) only *propose* weights.
* ``forecast_to_order``: an eligible index forecast becomes a paper order through the canonical OMS.
* Day-cycle handlers for options-open, after-NAV and before-cutoff phases.
* ``desk_metrics``: latency, quote-to-fill, drops and downtime per desk.
* ``desk_pages``: the five desk pages with each desk's own clock label.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, PaperOrder
from financial_brain.risk.capital_allocator import CapitalAllocator

STAGES = ("source", "receipt", "features", "decision", "approval", "fill", "stop", "exit")


@dataclass
class SignalTimeline:
    signal_id: str
    stamps: dict[str, datetime] = field(default_factory=dict)

    def mark(self, stage: str, at: datetime) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage}")
        if stage in self.stamps:
            return
        prev = [self.stamps[s] for s in STAGES[:STAGES.index(stage)] if s in self.stamps]
        if prev and at < max(prev):
            raise ValueError(f"{stage} stamped before an earlier stage")
        self.stamps[stage] = at

    def latency_ms(self, a: str, b: str) -> float | None:
        if a in self.stamps and b in self.stamps:
            return (self.stamps[b] - self.stamps[a]).total_seconds() * 1000
        return None


class FeedMode(str, Enum):
    STREAM = "STREAM"
    POLLING = "POLLING_FALLBACK"
    REPLAY = "REPLAY"


def evidence_grade(mode: FeedMode) -> str:
    return "INSTANT_OK" if mode is FeedMode.STREAM else "DEGRADED_" + mode.value


def replay(events: Iterable[dict], decide: Callable[[dict], object]) -> list[object]:
    """Run recorded events, in recorded order, through the live decision function."""
    ordered = sorted(events, key=lambda e: (e["at"], e.get("seq", 0)))
    return [decide(e) for e in ordered]


@dataclass
class CapitalGateway:
    allocator: CapitalAllocator

    def apply(self, proposer: str, proposals: dict[tuple[str, str], Decimal]) -> dict[str, bool]:
        """Turn proposed rupee amounts per (desk, underlying) into reservations; none can overspend."""
        out = {}
        for (desk, und), amt in sorted(proposals.items()):
            key = f"{proposer}:{desk}:{und}"
            out[key] = self.allocator.reserve(key, desk, und, amt)
        return out


def forecast_to_order(oms: ExchangePaperOms, *, forecast_id: str, eligible: bool, token: str,
                      lots: int, lot_size: int, limit: Decimal, expires_at: datetime) -> PaperOrder | None:
    if not eligible:
        return None
    o = PaperOrder(f"{forecast_id}:O", forecast_id, Account.AUTONOMOUS, "OPTIONS", token, "BUY",
                   lots * lot_size, limit, expires_at, lot_size)
    return oms.submit(o)


def options_open_handler(chain_fresh: Callable[[], bool], run: Callable[[], None]) -> Callable[[date], None]:
    """Signal only when contract data is fresh (FB-1003)."""
    def h(_d: date) -> None:
        if not chain_fresh():
            raise RuntimeError("option chain not fresh: no options signals")
        run()
    return h


def after_nav_handler(nav_published: Callable[[date], bool], steps: list[Callable[[date], None]]
                      ) -> Callable[[date], None]:
    """Update NAV history, mark holdings, then candidates — only after publication (FB-1006)."""
    def h(d: date) -> None:
        if not nav_published(d):
            raise RuntimeError("AMFI NAV not yet published")
        for s in steps:
            s(d)
    return h


def before_cutoff_handler(approved: Callable[[date], list[str]], submit: Callable[[str], None],
                          now: Callable[[], datetime], cutoff_at: Callable[[date], datetime]
                          ) -> Callable[[date], None]:
    """Submit approved MF requests only before the scheme cut-off (FB-1007)."""
    def h(d: date) -> None:
        if now() >= cutoff_at(d):
            raise RuntimeError("past cut-off: requests roll to the next scheme day")
        for r in approved(d):
            submit(r)
    return h


def desk_metrics(latencies_ms: list[float], quote_to_fill_ms: list[float], dropped: int, received: int,
                 downtime: timedelta, session: timedelta) -> dict[str, float]:
    return {"median_latency_ms": statistics.median(latencies_ms) if latencies_ms else 0.0,
            "median_quote_to_fill_ms": statistics.median(quote_to_fill_ms) if quote_to_fill_ms else 0.0,
            "drop_rate": dropped / max(received, 1),
            "downtime_share": downtime / session if session else 0.0}


CLOCK = {"SWING": "daily after close", "INTRADAY": "live ticks/bars", "OPTIONS": "live ticks + 15m forecasts",
         "ETF": "live ticks/bars", "MUTUAL_FUND": "daily NAV (not live)"}


def desk_pages(state: dict[str, dict]) -> dict[str, dict]:
    return {d: {"clock": CLOCK[d], **state.get(d, {"status": "NO_DATA"})} for d in CLOCK}
