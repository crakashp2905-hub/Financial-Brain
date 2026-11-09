"""Closures of 24 logic-only partial items.

Items: FB-0153, FB-0262, FB-0275, FB-0286, FB-0303, FB-0307, FB-0312, FB-0313, FB-0320, FB-0327, FB-0330, FB-0335,
FB-0345, FB-0346, FB-0351, FB-0357, FB-0365, FB-0379, FB-0381, FB-0443, FB-0447, FB-0450, FB-0451, FB-0452.

Each function composes or extends existing kernel modules. The ledger note states what remains.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import mean, pstdev

from financial_brain.kernel.batch20_accounting import fifo_realised
from financial_brain.kernel.batch20b_research import beta_and_residuals, correlation_matrix
from financial_brain.kernel.batch20e_strategies import fx_session_reversion
from financial_brain.kernel.batch20h_equity import factor_decay_exit
from financial_brain.kernel.closures_p import dividend_reinvestment
from financial_brain.kernel.closures_r import trend_signal
from financial_brain.kernel.intraday_tools import ReplayControl
from financial_brain.kernel.ops_extra import tail_report
from financial_brain.kernel.options_extra import short_option_margin
from financial_brain.kernel.portfolio_planning import rebalance_preview
from financial_brain.kernel.strategy_primitives import zscore_prior


# FB-0153: mean return grouped by year, from dated returns.
def returns_by_year(dated: Sequence[tuple[int, float]]) -> dict[int, float]:
    groups: dict[int, list[float]] = {}
    for year, r in dated:
        groups.setdefault(year, []).append(r)
    return {y: mean(v) for y, v in sorted(groups.items())}


# FB-0262: per-lot realised gain and tax drag for a sale.
def lot_tax_drag(
    lots: Sequence[tuple[Decimal, Decimal]], sell_qty: Decimal, sell_price: Decimal, rate: Decimal
) -> dict[str, Decimal]:
    gain, _ = fifo_realised(lots, sell_qty, sell_price)
    tax = max(gain, Decimal(0)) * rate
    return {"gain": gain, "tax": tax, "after_tax_gain": gain - tax}


# FB-0275: stress ladder by asset: portfolio change at each level from per-asset shocks.
def stress_ladder_by_asset(
    weights: Mapping[str, float], shocks_by_level: Mapping[float, Mapping[str, float]]
) -> dict[float, float]:
    return {level: sum(w * shocks.get(a, 0.0) for a, w in weights.items()) for level, shocks in shocks_by_level.items()}


# FB-0286: debit-vertical max loss including fees and settlement cost.
def max_loss_with_settlement(debit: Decimal, fees: Decimal, settlement: Decimal) -> Decimal:
    if debit <= 0 or fees < 0 or settlement < 0:
        raise ValueError("debit must be positive; fees and settlement non-negative")
    return debit + fees + settlement


# FB-0303: short-option margin for a portfolio: sum over short positions, no netting across positions.
def portfolio_short_margin(positions: Sequence[tuple[Decimal, Decimal, Decimal, str, int]]) -> Decimal:
    total = Decimal(0)
    for spot, strike, premium, kind, qty in positions:
        if qty < 0:
            total += short_option_margin(spot, strike, premium, kind, abs(qty))
    return total


# FB-0307: cost of rolling a position from one premium to another.
def adjust_position_cost(quantity: int, old_premium: Decimal, new_premium: Decimal) -> Decimal:
    return (new_premium - old_premium) * Decimal(quantity)


# FB-0312: daily P&L of a paper options position from successive marks.
def paper_daily_pnl(positions: Mapping[str, int], marks: Sequence[Mapping[str, float]]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(marks, marks[1:], strict=False):
        out.append(sum(q * (cur[s] - prev[s]) for s, q in positions.items()))
    return out


# FB-0313: evidence by regime: tail loss and count per regime.
def evidence_report(returns: Sequence[float], regimes: Sequence[str], alpha: float) -> dict[str, object]:
    counts: dict[str, int] = {}
    for reg in regimes:
        counts[reg] = counts.get(reg, 0) + 1
    return {"tail_loss": tail_report(returns, regimes, alpha), "counts": dict(sorted(counts.items()))}


# FB-0320: basis split into carry (cost of carry) and a residual.
def carry_components(
    futures: Decimal, spot: Decimal, rate: Decimal, dividend_yield: Decimal, days: int, storage: Decimal
) -> dict[str, Decimal]:
    basis = futures - spot
    carry = spot * (rate - dividend_yield + storage) * Decimal(days) / Decimal(365)
    return {"basis": basis, "carry": carry, "residual": basis - carry}


# FB-0327: strongest pairwise correlation among a set of return series.
def max_pairwise_correlation(series: Mapping[str, Sequence[float]]) -> tuple[str, str, float]:
    matrix = correlation_matrix(series)
    pairs = [(a, b, c) for (a, b), c in matrix.items() if a < b]
    if not pairs:
        raise ValueError("need at least two series")
    return max(pairs, key=lambda t: (t[2], t[0], t[1]))


# FB-0330 and FB-0346: hedge drift = actual hedge ratio minus target ratio.
def hedge_drift(target_ratio: float, hedge_units: float, position_units: float) -> float:
    if position_units == 0:
        raise ValueError("position units must be non-zero")
    return hedge_units / position_units - target_ratio


# FB-0335: base-currency P&L from quote-currency P&L at a rate.
def base_currency_pnl(pnl_quote: Decimal, quote_to_base: Decimal) -> Decimal:
    if quote_to_base <= 0:
        raise ValueError("rate must be positive")
    return pnl_quote * quote_to_base


# FB-0345: pip journal in pips and money: money uses each trade's value per pip.
def journal_pips_money(trades: Sequence[tuple[float, float]]) -> dict[str, float]:
    pips = sum(p for p, _ in trades)
    money = sum(p * v for p, v in trades)
    return {"pips": pips, "money": money}


# FB-0351: technical screen: close above the mean of the last window.
def technical_pass(closes: Sequence[float], window: int) -> bool:
    return trend_signal(closes, window) == 1


# FB-0357: residual return report: stock minus beta times index, per period.
def residual_report(stock: Sequence[float], index: Sequence[float]) -> dict[str, object]:
    beta, residuals = beta_and_residuals(stock, index)
    return {"beta": beta, "residuals": residuals}


# FB-0365: thesis exit: price through the invalidation level, or factor decay.
def thesis_exit(
    invalidation_level: float, price: float, scores: Sequence[float], threshold: float, consecutive: int
) -> bool:
    return price <= invalidation_level or factor_decay_exit(scores, threshold, consecutive)


# FB-0379: replay driver: consumes events while the control is not paused, up to a step limit.
def run_replay(events: Sequence[object], control: ReplayControl, max_steps: int) -> list[object]:
    if max_steps < 0:
        raise ValueError("max_steps must be non-negative")
    processed: list[object] = []
    for event in events:
        if control.paused or len(processed) >= max_steps:
            break
        processed.append(event)
    return processed


# FB-0381: per-phase mean and volatility.
def regime_metrics(returns_by_phase: Mapping[str, Sequence[float]]) -> dict[str, dict[str, float]]:
    return {
        phase: {"mean": mean(rs), "volatility": pstdev(rs) if len(rs) > 1 else 0.0}
        for phase, rs in sorted(returns_by_phase.items())
    }


# FB-0443: streamed MA crossover, one value per bar, equal to the batch result with the one-bar shift.
def stream_ma_crossover(closes: Sequence[float], fast: int, slow: int) -> list[int]:
    if fast >= slow:
        raise ValueError("fast window must be shorter than slow window")
    raw: list[int] = []
    out: list[int] = []
    for i in range(len(closes)):
        f = mean(closes[i + 1 - fast : i + 1]) if i + 1 >= fast else None
        s = mean(closes[i + 1 - slow : i + 1]) if i + 1 >= slow else None
        raw.append(1 if (f is not None and s is not None and f > s) else 0)
        out.append(raw[i - 1] if i > 0 else 0)
    return out


# FB-0447: beta-weighted index hedge for a basket.
def hedged_basket_notional(weights: Mapping[str, float], betas: Mapping[str, float], portfolio_notional: float) -> float:
    return -portfolio_notional * sum(w * betas[a] for a, w in weights.items())


# FB-0450: spread z-score entry on far minus near, applied from the next bar.
def spread_zscore_signal(near: Sequence[float], far: Sequence[float], window: int, entry_z: float) -> list[int]:
    if len(near) != len(far):
        raise ValueError("legs must align")
    spread: list[float | None] = [f - n for n, f in zip(near, far, strict=True)]
    z = zscore_prior(spread, window)
    raw = [1 if (v is not None and v <= -entry_z) else 0 for v in z]
    return [0] + raw[:-1]


# FB-0451: FX signals: trend on closes and session-mean reversion on the latest close.
def fx_signals(closes: Sequence[float], window: int, session_mean: float, deviation: float) -> dict[str, int]:
    return {
        "trend": trend_signal(closes, window),
        "session_reversion": fx_session_reversion(closes[-1], session_mean, deviation),
    }


# FB-0452: dividend reinvestment followed by a rebalance to target weights.
def dividend_rebalance(
    units: int,
    price: float,
    dividend_per_unit: float,
    current: Mapping[str, int],
    targets: Mapping[str, float],
    prices: Mapping[str, float],
    equity: float,
) -> dict[str, object]:
    return {
        "units_after_dividend": dividend_reinvestment(units, price, dividend_per_unit),
        "trades": rebalance_preview(dict(current), targets, prices, equity),
    }
