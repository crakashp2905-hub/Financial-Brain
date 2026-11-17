"""Mutual-fund plans, flows and realism on top of ``mf_orders`` (checklist sections 3, 4A and 7).

Covers request acknowledgement, payment evidence, SIP/SWP/STP and switches as separate dated legs,
minimums and lock-ins, IDCW payout and reinvestment as ledger flows, NAV-threshold redemption
*requests* (never instant fills), mark-to-market only at published NAV, and a paper-realism score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import Enum


class PlanError(ValueError):
    pass


# ------------------------------------------------------------------ requests and payment (0702/0703/0717)

class Ack(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Evidence(str, Enum):
    ASSUMED = "ASSUMED"       # simulated paper assumption
    OBSERVED = "OBSERVED"     # independently observed (bank/RTA statement)


@dataclass
class Payment:
    initiated_at: datetime
    credited_at: datetime | None = None
    available_at: datetime | None = None
    evidence: Evidence = Evidence.ASSUMED

    def funds_available(self) -> datetime | None:
        """Never inferred from initiation or credit; only the explicit available time counts."""
        return self.available_at


@dataclass
class Request:
    request_id: str
    submitted_at: datetime
    state: Ack = Ack.SUBMITTED
    accepted_at: datetime | None = None
    reason: str = ""

    def acknowledge(self) -> None:
        if self.state is not Ack.SUBMITTED:
            raise PlanError("only a submitted request can be acknowledged")
        self.state = Ack.ACKNOWLEDGED

    def accept(self, at: datetime) -> None:
        if self.state is not Ack.ACKNOWLEDGED:
            raise PlanError("acceptance follows acknowledgement")
        if at < self.submitted_at:
            raise PlanError("acceptance cannot precede submission")
        self.state, self.accepted_at = Ack.ACCEPTED, at

    def reject(self, reason: str) -> None:
        if self.state in (Ack.ACCEPTED, Ack.REJECTED):
            raise PlanError("request already decided")
        self.state, self.reason = Ack.REJECTED, reason


# ------------------------------------------------------------------ scheme rules (0713/0315)

@dataclass(frozen=True)
class SchemeRules:
    min_purchase: Decimal
    min_additional: Decimal
    min_redemption: Decimal
    lock_in_days: int = 0            # e.g. ELSS 3 years = 1095
    gated: bool = False              # special redemption restriction in force
    closed_for_purchase: bool = False


def check_purchase(rules: SchemeRules, amount: Decimal, first: bool) -> None:
    if rules.closed_for_purchase:
        raise PlanError("scheme closed for purchase")
    floor = rules.min_purchase if first else rules.min_additional
    if amount < floor:
        raise PlanError(f"amount below minimum {floor}")


def redeemable_units(rules: SchemeRules, lots: list[tuple[date, Decimal]], on: date) -> Decimal:
    """Units free of lock-in on ``on``; a gated scheme allows none."""
    if rules.gated:
        return Decimal(0)
    return sum((u for d, u in lots if (on - d).days >= rules.lock_in_days), Decimal(0))


def check_redemption(rules: SchemeRules, units: Decimal, value: Decimal,
                     lots: list[tuple[date, Decimal]], on: date) -> None:
    if units > redeemable_units(rules, lots, on):
        raise PlanError("units locked in, gated or not held")
    if value < rules.min_redemption:
        raise PlanError("below minimum redemption")


# ------------------------------------------------------------------ SIP / SWP / STP / switch (0710-0712)

@dataclass(frozen=True)
class Installment:
    due: date
    amount: Decimal
    paid: bool
    kind: str               # SIP, SWP, STP_OUT, STP_IN, SWITCH_OUT, SWITCH_IN

    @property
    def status(self) -> str:
        return "EXECUTED" if self.paid else "MISSED"


def schedule(start: date, months: int, day: int) -> list[date]:
    out, y, m = [], start.year, start.month
    for _ in range(months):
        d = date(y, m, min(day, 28))
        if d >= start:
            out.append(d)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def sip(start: date, months: int, day: int, amount: Decimal, paid: dict[date, bool]) -> list[Installment]:
    """Each installment is its own subscription; a missed payment stays missed (FB-0711)."""
    return [Installment(d, amount, paid.get(d, False), "SIP") for d in schedule(start, months, day)]


def switch(out_on: date, amount: Decimal, settle_days: int) -> tuple[Installment, Installment]:
    """Switch-out then switch-in after settlement; no same-minute redeployment (FB-0710)."""
    if settle_days < 1:
        raise PlanError("switch-in must wait for switch-out settlement")
    return (Installment(out_on, amount, True, "SWITCH_OUT"),
            Installment(out_on + timedelta(days=settle_days), amount, True, "SWITCH_IN"))


def stp(start: date, months: int, day: int, amount: Decimal, settle_days: int) -> list[Installment]:
    legs: list[Installment] = []
    for d in schedule(start, months, day):
        legs.append(Installment(d, amount, True, "STP_OUT"))
        legs.append(Installment(d + timedelta(days=settle_days), amount, True, "STP_IN"))
    return legs


def swp(start: date, months: int, day: int, amount: Decimal) -> list[Installment]:
    return [Installment(d, amount, True, "SWP") for d in schedule(start, months, day)]


# ------------------------------------------------------------------ IDCW (0715/0316)

@dataclass(frozen=True)
class IdcwResult:
    cash: Decimal
    new_units: Decimal
    total_value_before: Decimal
    total_value_after: Decimal


def idcw(units: Decimal, nav_cum: Decimal, per_unit: Decimal, reinvest: bool, places: int = 3
         ) -> IdcwResult:
    """IDCW drops NAV by the payout; value moves to cash or new units, never vanishes."""
    if per_unit <= 0 or per_unit >= nav_cum:
        raise PlanError("payout must be positive and below NAV")
    nav_ex = nav_cum - per_unit
    payout = units * per_unit
    before = units * nav_cum
    if reinvest:
        q = Decimal(1).scaleb(-places)
        add = (payout / nav_ex).quantize(q, rounding=ROUND_DOWN)
        return IdcwResult(Decimal(0), add, before, (units + add) * nav_ex)
    return IdcwResult(payout, Decimal(0), before, units * nav_ex + payout)


# ------------------------------------------------------------------ NAV thresholds (0480/0482)

@dataclass(frozen=True)
class NavPolicy:
    target_return: Decimal
    review_drawdown: Decimal     # negative fraction
    action_on_target: str = "REDUCE"
    action_on_drawdown: str = "REDEEM"


@dataclass(frozen=True)
class RedemptionRequestSignal:
    """A dated request, not a fill. Units and cash are final only after the applicable NAV."""

    on_nav_date: date
    nav_seen: Decimal
    action: str
    reason: str
    state: str = "NAV_PENDING"


def nav_trigger(policy: NavPolicy, cost_nav: Decimal, nav: Decimal, nav_date: date,
                published_at: datetime, now: datetime) -> RedemptionRequestSignal | None:
    if published_at > now:
        return None
    ret = nav / cost_nav - 1
    if ret >= policy.target_return:
        return RedemptionRequestSignal(nav_date, nav, policy.action_on_target, "target return reached")
    if ret <= policy.review_drawdown:
        return RedemptionRequestSignal(nav_date, nav, policy.action_on_drawdown, "drawdown threshold")
    return None


# ------------------------------------------------------------------ marks and realism (0714/0906/0718)

@dataclass(frozen=True)
class MfMark:
    value: Decimal
    nav_date: date
    stale: bool


def mark(units: Decimal, navs: list[tuple[date, datetime, Decimal]], now: datetime,
         today: date) -> MfMark | None:
    """Units at the last NAV *published* by ``now``; stale when that NAV is not today's."""
    known = [(d, n) for d, p, n in navs if p <= now]
    if not known:
        return None
    d, n = max(known)
    return MfMark(units * n, d, d < today)


@dataclass
class Realism:
    nav_date_known: bool
    payment: Evidence
    loads_complete: bool
    settlement_observed: bool
    notes: list[str] = field(default_factory=list)

    def score(self) -> float:
        """0..1. Each component weighs equally; assumed inputs score zero for their part."""
        parts = [self.nav_date_known, self.payment is Evidence.OBSERVED, self.loads_complete,
                 self.settlement_observed]
        return sum(parts) / len(parts)
