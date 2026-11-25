"""MF portfolio research, cut-off rules, reconciliation and display records (sections 3, 4, 6, 7, 11, 12, 20).

* ``CutoffBook``: effective-dated cut-off rules per scheme class with scheme overrides; no universal
  default, an unknown class raises (FB-0704).
* ``reconcile_mf``: pending applications + allotted units + cash reserve + payouts must tie (FB-0716).
* Debt metrics with absent fields labelled (FB-0312); fund-of-funds lookthrough with coverage and
  double-count prevention (FB-0313); overlap and concentration (FB-0604).
* Cash-management comparison net of payout lag (FB-0606); a small constrained allocator that can
  choose cash (FB-0607); owner-controlled SIP parameters and versioned charter (FB-0613, FB-0614).
* Allocation recommendation and display records that say NAV_PENDING rather than a price
  (FB-0408, FB-1106, FB-1107, FB-1108); flow-aware MF return (FB-1206); tax-lot-aware action (FB-2007).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal


class MfResearchError(ValueError):
    pass


# ---------------------------------------------------------------- cut-offs (FB-0704)

@dataclass(frozen=True)
class CutoffRule:
    effective_from: date
    cutoff: time
    source: str
    same_day_requires_funds: bool = True


@dataclass
class CutoffBook:
    by_class: dict[str, list[CutoffRule]] = field(default_factory=dict)
    by_scheme: dict[str, list[CutoffRule]] = field(default_factory=dict)
    suspended: dict[str, list[tuple[date, date]]] = field(default_factory=dict)

    def add_class(self, cls: str, r: CutoffRule) -> None:
        self.by_class.setdefault(cls, []).append(r)
        self.by_class[cls].sort(key=lambda x: x.effective_from)

    def add_scheme(self, code: str, r: CutoffRule) -> None:
        self.by_scheme.setdefault(code, []).append(r)
        self.by_scheme[code].sort(key=lambda x: x.effective_from)

    def rule(self, code: str, cls: str, on: date) -> CutoffRule:
        for d, b in self.suspended.get(code, []):
            if d <= on <= b:
                raise MfResearchError(f"scheme {code} suspended on {on}")
        for src in (self.by_scheme.get(code, []), self.by_class.get(cls, [])):
            hits = [r for r in src if r.effective_from <= on]
            if hits:
                return hits[-1]
        raise MfResearchError(f"no cut-off rule for {code}/{cls} on {on}; none is assumed")


# ---------------------------------------------------------------- reconciliation (FB-0716)

def reconcile_mf(*, contributed: Decimal, cash_available: Decimal, pending_applications: Decimal,
                 allotted_cost: Decimal, redemption_pending: Decimal, paid_out: Decimal,
                 realized_pnl: Decimal) -> list[str]:
    """Every rupee is in exactly one place. Returns mismatches (empty when it ties)."""
    lhs = contributed + realized_pnl
    rhs = cash_available + pending_applications + allotted_cost + redemption_pending + paid_out
    return [] if lhs == rhs else [f"MF ledger off by {lhs - rhs}"]


# ---------------------------------------------------------------- portfolio analytics

DEBT_FIELDS = ("modified_duration", "ytm", "avg_maturity", "aaa_share", "below_aa_share")


def debt_metrics(disclosure: dict) -> dict:
    """Debt metrics with absent fields labelled ABSENT, never zero (FB-0312)."""
    return {k: disclosure.get(k, "ABSENT") for k in DEBT_FIELDS}


def lookthrough(holdings: dict[str, Decimal], fund_holdings: dict[str, dict[str, Decimal]]
                ) -> tuple[dict[str, Decimal], Decimal]:
    """Underlying exposure through funds (FoF too). Returns exposure and coverage share (FB-0313).

    A fund without disclosed holdings stays as itself and lowers coverage; nothing is double counted
    because each rupee is pushed down exactly once per level.
    """
    out: dict[str, Decimal] = {}
    total = sum(holdings.values(), Decimal(0))
    covered = Decimal(0)

    def push(name: str, amount: Decimal, depth: int) -> bool:
        if depth > 5:
            raise MfResearchError("fund-of-funds cycle")
        inner = fund_holdings.get(name)
        if not inner:
            out[name] = out.get(name, Decimal(0)) + amount
            return False
        w = sum(inner.values(), Decimal(0))
        for k, v in inner.items():
            push(k, amount * v / w, depth + 1)
        return True

    for name, amt in holdings.items():
        if push(name, amt, 0):
            covered += amt
    return out, (covered / total if total else Decimal(0))


def concentration(exposure: dict[str, Decimal]) -> Decimal:
    """Herfindahl index of exposure weights (FB-0604)."""
    total = sum(exposure.values(), Decimal(0))
    return sum(((v / total) ** 2 for v in exposure.values()), Decimal(0)) if total else Decimal(0)


def overlap(a: dict[str, Decimal], b: dict[str, Decimal]) -> Decimal:
    ta, tb = sum(a.values(), Decimal(0)), sum(b.values(), Decimal(0))
    return sum((min(a[k] / ta, b[k] / tb) for k in a.keys() & b.keys()), Decimal(0))


@dataclass(frozen=True)
class CashOption:
    name: str
    annual_yield: float
    payout_lag_days: int
    exit_load: float


def cash_management(options: list[CashOption], horizon_days: int) -> list[tuple[str, float]]:
    """Net return over the horizon after payout lag (money idle while it settles) and loads (FB-0606)."""
    rows = []
    for o in options:
        invested = max(horizon_days - o.payout_lag_days, 0)
        rows.append((o.name, o.annual_yield * invested / 365 - o.exit_load))
    return sorted(rows, key=lambda r: r[1], reverse=True)


def allocate(expected: dict[str, float], risk: dict[str, float], *, budget_risk: float,
             max_weight: float, locked: frozenset[str] = frozenset()) -> dict[str, float]:
    """Greedy risk-budgeted allocation; leftover goes to CASH, a valid answer (FB-0607)."""
    w: dict[str, float] = {}
    used = 0.0
    for k in sorted(expected, key=lambda k: expected[k] / max(risk[k], 1e-9), reverse=True):
        if expected[k] <= 0 or k in locked:
            continue
        room = (budget_risk - used) / max(risk[k], 1e-9)
        take = max(0.0, min(max_weight, room, 1 - sum(w.values())))
        if take > 0:
            w[k] = take
            used += take * risk[k]
    w["CASH"] = 1 - sum(w.values())
    return w


@dataclass(frozen=True)
class SipParams:
    """Owner-controlled; no live folio or transaction API is assumed (FB-0613)."""

    scheme_code: str
    amount: Decimal
    day: int
    start: date
    set_by: str


@dataclass(frozen=True)
class Charter:
    """Versioned MF strategy charter with a cash/no-action control (FB-0614)."""

    name: str
    version: str
    hypothesis: str
    control: str = "NO_ACTION_AND_CASH"
    review_days: int = 365


@dataclass(frozen=True)
class AllocationRecommendation:
    """Daily/weekly MF allocation, never an instant execution (FB-0408)."""

    scheme_code: str
    action: str
    amount: Decimal
    next_cutoff: str
    nav_status: str = "NAV_PENDING"
    lot_fees: str = "exit load by lot"
    benchmark: str = ""


def mf_display(scheme: str, plan: str, option: str, last_nav: Decimal, nav_date: date,
               pending_units: bool, units: Decimal, cost: Decimal, load: Decimal, tax_est: Decimal,
               cutoff: str) -> dict:
    """MF desk card fields (FB-1107/FB-1108): decisions, never a buy-at-current-price button."""
    return {"scheme": scheme, "plan": plan, "option": option, "published_nav": str(last_nav),
            "nav_date": nav_date.isoformat(), "allotment": "PENDING" if pending_units else "ALLOTTED",
            "units": str(units), "cost_basis": str(cost), "exit_load": str(load),
            "tax_estimate": str(tax_est), "cutoff": cutoff,
            "actions": ["SUBSCRIBE", "REDEEM", "SWITCH", "SIP", "HOLD"]}


def etf_display(symbol: str, benchmark: str, bid: Decimal, ask: Decimal, premium: Decimal | None,
                tracking_difference: Decimal | None, cost_bps: Decimal, tier: str) -> dict:
    """ETF desk card fields (FB-1106). A missing premium is shown as UNAVAILABLE."""
    return {"symbol": symbol, "benchmark": benchmark, "bid": str(bid), "ask": str(ask),
            "premium_vs_inav": "UNAVAILABLE" if premium is None else str(premium),
            "tracking_difference": "ABSENT" if tracking_difference is None else str(tracking_difference),
            "cost_bps": str(cost_bps), "liquidity": tier}


def mf_period_return(start_value: Decimal, end_value: Decimal, contributions: Decimal,
                     pending_redemption: Decimal, idle_cash: Decimal) -> Decimal:
    """Return on money actually at work: flows removed, pending redemption and idle cash counted (FB-1206)."""
    base = start_value + contributions
    if base <= 0:
        raise MfResearchError("non-positive base")
    return (end_value + pending_redemption + idle_cash - base) / base


def tax_lot_action(lots: list[tuple[date, Decimal, Decimal]], on: date, nav: Decimal,
                   long_term_days: int, load_days: int) -> str:
    """HOLD while any lot is short-term or in its load window and the gain is positive (FB-2007)."""
    young = [(d, u, c) for d, u, c in lots if (on - d).days < max(long_term_days, load_days)]
    gain = sum((u * nav - c for _, u, c in young), Decimal(0))
    if young and gain > 0:
        return "HOLD"
    return "REVIEW"
