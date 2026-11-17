"""Five-desk paper routing and cross-asset adapters (FB-0475, FB-0484, FB-1704, FB-2420, FB-2423,
FB-2424, FB-2527, FB-2530, FB-2531, FB-2532).

* ``route``: every admissible signal goes to the AUTONOMOUS account; a human-approved copy goes to
  TRADER_APPROVED as a separate order. Exits route to the account that holds the position.
* ``etf_style_allowed``: ETFs trade long only unless a style is separately validated.
* ``tradable``: only supported, data-checked instruments get trades; others are research only.
* ``premium_exits``: underlying invalidation maps to option-premium SL/TP via delta, never by copying
  index points into premium.
* ``FuturesSpec`` / ``ETFSpec`` sizing with multiplier and margin; paper only.
* ``delta_exposure``: delta-equivalent rupee exposure by underlying across stocks, ETFs, funds, options.
* ``expiry_guard``: blocks new long options near expiry and fails closed when legs cannot be synced.
* ``RiskClock``: protective exits are checked on every tick, independently of the 15m/M1 signal clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, PaperOrder


class RouteError(ValueError):
    pass


def route(oms: ExchangePaperOms, base: PaperOrder, approved: bool) -> list[PaperOrder]:
    """Autonomous order always; a separate approved-account order only if a human approved it."""
    out = [oms.submit(base)]
    if approved:
        twin = PaperOrder(base.order_id + ":A", base.signal_id, Account.TRADER_APPROVED, base.desk,
                          base.instrument, base.side, base.units, base.limit, base.expires_at,
                          base.lot_size)
        out.append(oms.submit(twin))
    return out


VALIDATED_ETF_STYLES = {"LONG"}


def etf_style_allowed(style: str, validated: set[str] = VALIDATED_ETF_STYLES) -> bool:
    return style in validated


@dataclass(frozen=True)
class Tradability:
    supported: bool
    data_ok: bool
    liquid: bool


def tradable(t: Tradability) -> str:
    if not t.supported:
        return "RESEARCH_ONLY"
    if not t.data_ok or not t.liquid:
        return "NO_TRADE"
    return "TRADABLE"


def premium_exits(entry_premium: float, delta: float, underlying_entry: float,
                  underlying_invalidation: float, underlying_target: float, tick: float = 0.05
                  ) -> tuple[float, float]:
    """Premium SL/TP from underlying levels through delta, rounded to tick, floored above zero."""
    if delta == 0:
        raise RouteError("zero delta: underlying levels cannot map to premium")
    sl = entry_premium + delta * (underlying_invalidation - underlying_entry)
    tp = entry_premium + delta * (underlying_target - underlying_entry)
    sl, tp = max(round(sl / tick) * tick, tick), round(tp / tick) * tick
    if not sl < entry_premium < tp:
        raise RouteError("mapped premium levels are not on the right sides of entry")
    return sl, tp


@dataclass(frozen=True)
class FuturesSpec:
    lot_size: int
    margin_rate: float


def futures_lots(capital_at_risk: float, entry: float, stop: float, spec: FuturesSpec,
                 cash: float) -> int:
    per_lot_risk = abs(entry - stop) * spec.lot_size
    by_risk = int(capital_at_risk // per_lot_risk) if per_lot_risk else 0
    by_margin = int(cash // (entry * spec.lot_size * spec.margin_rate))
    return max(0, min(by_risk, by_margin))


def etf_units(capital_at_risk: float, entry: float, stop: float, cash: float) -> int:
    per = abs(entry - stop)
    return max(0, min(int(capital_at_risk // per) if per else 0, int(cash // entry)))


@dataclass(frozen=True)
class Position:
    underlying: str
    kind: str          # STOCK, ETF, MF, OPTION, FUTURE
    quantity: float
    price: float
    delta: float = 1.0
    lookthrough: bool = True


def delta_exposure(positions: list[Position]) -> tuple[dict[str, float], list[str]]:
    """Delta-equivalent exposure by underlying, plus positions whose lookthrough is unavailable."""
    out: dict[str, float] = {}
    uncovered = []
    for p in positions:
        if not p.lookthrough:
            uncovered.append(f"{p.kind}:{p.underlying}")
            continue
        out[p.underlying] = out.get(p.underlying, 0.0) + p.quantity * p.price * p.delta
    return out, uncovered


def expiry_guard(expiry: date, today: date, *, min_days: int, legs_synced: bool,
                 margin_modelled: bool, structure: str) -> str | None:
    """Reason to block, or None (FB-2420)."""
    if (expiry - today).days < min_days:
        return "too close to expiry"
    if structure != "LONG" and not legs_synced:
        return "multi-leg synchronization not modelled"
    if structure.startswith("SHORT") and not margin_modelled:
        return "margin not modelled"
    return None


@dataclass
class RiskClock:
    """Protective checks on every tick; signals on their own slower clock (FB-2423, FB-2532)."""

    signal_every: timedelta
    last_signal: datetime | None = None
    checks: int = 0
    signals: int = 0
    log: list[str] = field(default_factory=list)

    def on_tick(self, at: datetime) -> tuple[bool, bool]:
        self.checks += 1
        fire = self.last_signal is None or at - self.last_signal >= self.signal_every
        if fire:
            self.last_signal = at
            self.signals += 1
        return True, fire
