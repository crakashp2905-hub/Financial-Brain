"""Closures of further partial items (FB-0091, FB-0113, FB-0208, FB-0221, FB-0240, FB-0242, FB-0249, FB-0259, FB-0261,
FB-0268, FB-0273, FB-0275, FB-0359, FB-0390, FB-0427, FB-0460).

Each function is a small deterministic piece of its item's acceptance. Items whose acceptance is only partly met keep
their partial status, and the ledger note says what remains.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from financial_brain.kernel.closures_p import adjust_for_dividends
from financial_brain.kernel.patterns import PatternLifecycle, PatternState, replay
from financial_brain.kernel.batch20_ops import equal_weight_series


# FB-0113: streamed pattern states, one per event, and a check that the stream matches the replay.
def stream_states(events: Sequence[PatternState]) -> Iterator[PatternState]:
    life = PatternLifecycle()
    for event in events:
        life.move(event)
        yield life.state


def stream_matches_replay(events: Sequence[PatternState]) -> bool:
    streamed = list(stream_states(events))
    return bool(streamed) and streamed[-1] == replay(events)


# FB-0221: VWAP slices sized by a volume profile, summing exactly to the parent quantity.
def vwap_slices(total: int, profile: Sequence[float]) -> list[int]:
    if total <= 0 or not profile or any(p < 0 for p in profile) or sum(profile) <= 0:
        raise ValueError("total must be positive and profile non-negative with positive sum")
    raw = [total * p / sum(profile) for p in profile]
    floors = [int(x) for x in raw]
    remainder = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    return floors


# FB-0221: POV slices: each bar takes a fixed share of its volume until the parent is filled.
def pov_slices(total: int, rate: float, bar_volumes: Sequence[float]) -> list[int]:
    if total <= 0 or not (0 < rate <= 1):
        raise ValueError("total must be positive and rate in (0, 1]")
    out: list[int] = []
    remaining = total
    for volume in bar_volumes:
        if remaining <= 0:
            break
        take = min(remaining, int(volume * rate))
        out.append(take)
        remaining -= take
    return out


# FB-0240: reconciliation exceptions block trading.
def reconcile_gate(exceptions: Sequence[str]) -> str:
    return "BLOCK" if exceptions else "CLEAR"


# FB-0242: return on capital.
def return_on_capital(net_profit: Decimal, capital: Decimal) -> Decimal:
    if capital <= 0:
        raise ValueError("capital must be positive")
    return net_profit / capital


# FB-0261: adjusted performance from a price path and cash dividends.
def adjusted_performance(prices: Sequence[float], dividends: Mapping[int, float]) -> float:
    adjusted = adjust_for_dividends(prices, dividends)
    return adjusted[-1] / adjusted[0] - 1.0


# FB-0268: named macro scenarios. Shocks are illustrative, not calibrated to history.
MACRO_SCENARIOS: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "inflation": MappingProxyType({"BOND": -0.05, "EQ": -0.03}),
        "rates_up": MappingProxyType({"BOND": -0.08, "EQ": -0.02}),
        "currency_shock": MappingProxyType({"FX": -0.10}),
        "market_crash": MappingProxyType({"EQ": -0.30, "CREDIT": -0.10}),
    }
)


# FB-0273: losing-streak latch: true once a run of losses reaches the limit.
def losing_streak_latch(pnls: Sequence[float], max_streak: int) -> bool:
    if max_streak < 1:
        raise ValueError("max streak must be positive")
    streak = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        if streak >= max_streak:
            return True
    return False


# FB-0275: stress ladder: portfolio loss at each uniform shock level, weights summing to the same scale.
def stress_ladder(weights: Mapping[str, float], levels: Sequence[float]) -> dict[float, float]:
    total = sum(weights.values())
    return {level: level * total for level in levels}


# FB-0359: factor long-short harness: mean return of the top fraction minus the bottom fraction by score.
def factor_long_short(scores: Mapping[str, float], returns: Mapping[str, float], fraction: float) -> float:
    if not (0 < fraction <= 0.5):
        raise ValueError("fraction must be in (0, 0.5]")
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    k = max(1, int(len(ranked) * fraction))
    top = [returns[s] for s in ranked[:k]]
    bottom = [returns[s] for s in ranked[-k:]]
    return sum(top) / len(top) - sum(bottom) / len(bottom)


# FB-0390: presets for every persona in the charter.
PERSONA_PRESETS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "quant_researcher": ("research", "replay"),
        "quantitative_trader": ("research", "paper"),
        "quantitative_developer": ("research", "replay", "ci"),
        "trading_systems_engineer": ("ops", "replay", "paper"),
        "discretionary_day_trader": ("chart", "watchlist", "paper"),
        "swing_trader": ("chart", "screener", "paper"),
        "long_term_investor": ("portfolio", "rebalance"),
        "short_term_investor": ("portfolio", "chart"),
        "options_trader": ("option_chain", "greeks", "paper"),
        "futures_trader": ("futures_curve", "margin", "paper"),
        "fx_trader": ("fx_pairs", "sessions", "paper"),
        "index_trader": ("index_chart", "breadth", "paper"),
    }
)


# FB-0427: multistrategy benchmark report: per-strategy total return and the equal-weight combination.
def multistrategy_report(series: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    def total(rs: Sequence[float]) -> float:
        out = 1.0
        for r in rs:
            out *= 1.0 + r
        return out - 1.0

    per = {name: total(rs) for name, rs in sorted(series.items())}
    equal = total(equal_weight_series(series))
    return {"per_strategy": per, "equal_weight": equal}


# FB-0091: bonus issue: quantity rises by the ratio and price falls so notional is unchanged.
def adjust_for_bonus(quantity: int, price: float, ratio: float) -> tuple[float, float]:
    if ratio <= 0:
        raise ValueError("bonus ratio must be positive")
    return quantity * (1.0 + ratio), price / (1.0 + ratio)


# FB-0208: pre-trade margin check against initial margin rate.
def pretrade_margin_check(notional: Decimal, equity: Decimal, initial_rate: Decimal) -> str:
    return "OK" if equity >= notional * initial_rate else "INSUFFICIENT"


# FB-0259: beta-neutral check: sum of weight times beta within a tolerance.
def beta_neutral_check(weights: Mapping[str, float], betas: Mapping[str, float], tolerance: float) -> bool:
    exposure = sum(w * betas[a] for a, w in weights.items())
    return abs(exposure) <= tolerance


# FB-0249: interest on retained cash for a number of days (simple, Actual/365).
def retained_cash_interest(cash: Decimal, annual_rate: Decimal, days: int) -> Decimal:
    if cash < 0 or days < 0:
        raise ValueError("cash and days must be non-negative")
    return cash * annual_rate * Decimal(days) / Decimal(365)


# FB-0460: default weights are frozen; a mutation attempt raises.
FROZEN_DEFAULT_WEIGHTS: Mapping[str, float] = MappingProxyType({"EQ": 0.6, "BOND": 0.4})


def weights_fixed_before_search(weights_before: Mapping[str, float], weights_after: Mapping[str, float]) -> bool:
    return dict(weights_before) == dict(weights_after)
