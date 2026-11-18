"""Five-desk Today screen, alerts, post-market report and session gate (checklist sections 11 and 23).

Everything here is a deterministic view over records the other modules produce; nothing creates
orders. Alerts are keyed so a reconnect cannot duplicate them. The Today screen orders exits and
risk notices before opportunities. ``session_gate`` refuses SESSION_COMPLETE while any open order,
pending MF item or unpriced mark is unreconciled and not explicitly flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Kind(str, Enum):
    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    STOP_HIT = "STOP_HIT"
    TP_HIT = "TP_HIT"
    EXIT_UNPRICED = "EXIT_UNPRICED"
    APPROVAL_NEEDED = "APPROVAL_NEEDED"
    BUY = "BUY"
    SELL = "SELL"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"
    MF_NAV_PENDING = "MF_NAV_PENDING"
    ENTRY_EXPIRED = "ENTRY_EXPIRED"


#: Risk and exit notices first (level 1), then actions, then information.
PRIORITY = {
    Kind.EXIT_UNPRICED: 0, Kind.STOP_HIT: 0, Kind.EXIT_TRIGGERED: 0, Kind.TP_HIT: 1,
    Kind.APPROVAL_NEEDED: 1, Kind.BUY: 2, Kind.SELL: 2, Kind.MF_NAV_PENDING: 2,
    Kind.WATCH: 3, Kind.ENTRY_EXPIRED: 3, Kind.NO_TRADE: 4,
}


@dataclass(frozen=True)
class Alert:
    key: str
    kind: Kind
    desk: str
    at: datetime
    text: str
    rule_version: str
    evidence_tier: str
    deadline: datetime | None = None

    @property
    def level(self) -> int:
        return 1 if PRIORITY[self.kind] <= 1 else 2


@dataclass
class AlertBook:
    alerts: dict[str, Alert] = field(default_factory=dict)

    def emit(self, a: Alert) -> bool:
        """Idempotent: an alert key is shown once, however often it is re-sent."""
        if a.key in self.alerts:
            return False
        self.alerts[a.key] = a
        return True

    def today(self, desk: str | None = None, search: str = "") -> list[Alert]:
        rows = [a for a in self.alerts.values()
                if (desk is None or a.desk == desk) and search.lower() in a.text.lower()]
        return sorted(rows, key=lambda a: (PRIORITY[a.kind], a.at))


@dataclass(frozen=True)
class Approval:
    signal_id: str
    approved_at: datetime
    expires_at: datetime


def approval_valid(a: Approval, now: datetime) -> bool:
    """An approval dies with the signal's TTL; it must be revalidated, not reused (FB-2303)."""
    return a.approved_at <= now < a.expires_at


@dataclass(frozen=True)
class SessionState:
    open_orders: int
    pending_mf: int
    unpriced_marks: int
    ledger_balanced: bool
    flagged_unresolved: frozenset[str] = frozenset()


def session_gate(s: SessionState) -> tuple[str, list[str]]:
    """SESSION_COMPLETE only when everything reconciles or is explicitly flagged (FB-2308)."""
    problems = []
    if not s.ledger_balanced:
        problems.append("ledger out of balance")
    for name, count in (("open_orders", s.open_orders), ("pending_mf", s.pending_mf),
                        ("unpriced_marks", s.unpriced_marks)):
        if count and name not in s.flagged_unresolved:
            problems.append(f"{count} {name} not reconciled or flagged")
    return ("SESSION_INCOMPLETE" if problems else "SESSION_COMPLETE"), problems


def post_market_report(desk: str, *, issued: int, eligible: int, missed: int, filled: int,
                       exits_by_reason: dict[str, int], net_pnl: float, fees: float,
                       skipped: int, breaches: list[str], obligations: list[str]) -> str:
    """Plain, deterministic desk report. Every number is passed in from the ledger; none is inferred."""
    lines = [f"# {desk} post-market report",
             f"signals issued {issued}; eligible {eligible}; missed {missed}; filled {filled}; "
             f"skipped {skipped}",
             "exits: " + (", ".join(f"{k} {v}" for k, v in sorted(exits_by_reason.items())) or "none"),
             f"net P&L {net_pnl:,.2f} after fees {fees:,.2f}",
             "risk breaches: " + ("; ".join(breaches) or "none"),
             "next-session obligations: " + ("; ".join(obligations) or "none")]
    return "\n".join(lines)
