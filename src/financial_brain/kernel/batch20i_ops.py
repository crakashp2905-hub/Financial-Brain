"""Futures, screening, exits and examples (FB-0326, FB-0332, FB-0339, FB-0343, FB-0351, FB-0353, FB-0382, FB-0383,
FB-0384, FB-0385, FB-0386, FB-0387, FB-0390, FB-0391, FB-0392, FB-0429, FB-0430).

* ``futures_breakout`` reuses the prior-bar Donchian signal as the futures breakout baseline.
* ``reconcile`` lists symbols whose local and broker quantities differ, with the difference.
* ``session_breakout`` is long above the session high and short below the session low, using the prior range.
* ``spread_spike_loss`` is the extra cost of a spread that widens by a multiplier.
* ``screen_stocks`` keeps stocks meeting liquidity, valuation and momentum minimums and maximums.
* ``parse_earnings`` reads "SYMBOL,YYYY-MM-DD" fixture lines and rejects invalid dates.
* ``volume_heatmap`` sums volume by time bucket and day.
* ``reason_stack`` gives an ordered, numbered list of trade reasons; an empty stack is refused.
* ``exit_resolution`` decides which exit fires first in a bar, and fills a gap through a stop at the open.
* ``top_k_watchlist`` returns the k highest scores, ties broken by name.
* ``is_half_day`` flags a half-trading day from a fixture set.
* ``role_presets`` maps a persona to its workspace presets; unknown personas are refused.
* ``search_instruments`` returns an exact match first and flags ambiguity when several prefixes match.
* ``market_overview`` ranks symbols by percentage change.
* ``api_doc`` writes a Markdown section per route from typed example dictionaries.
* ``deterministic_example`` returns a digest of a seeded example run; the same seed gives the same digest.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from financial_brain.kernel.batch20c_strategies import donchian_signal
from financial_brain.kernel.batch20e_strategies import bootstrap_paths

PRESETS: dict[str, tuple[str, ...]] = {
    "quant_researcher": ("research", "replay"),
    "discretionary_day_trader": ("chart", "watchlist", "paper"),
    "long_term_investor": ("portfolio", "rebalance"),
    "options_trader": ("option_chain", "greeks", "paper"),
}


def futures_breakout(close: Sequence[float], high: Sequence[float], low: Sequence[float], n: int) -> list[int]:
    return donchian_signal(close, high, low, n)


def reconcile(local: Mapping[str, int], broker: Mapping[str, int]) -> dict[str, int]:
    diffs = {}
    for sym in set(local) | set(broker):
        d = local.get(sym, 0) - broker.get(sym, 0)
        if d != 0:
            diffs[sym] = d
    return dict(sorted(diffs.items()))


def session_breakout(close: float, session_high: float, session_low: float) -> int:
    if close > session_high:
        return 1
    if close < session_low:
        return -1
    return 0


def spread_spike_loss(quantity: int, normal_spread: float, spike_multiplier: float) -> float:
    if spike_multiplier < 1:
        raise ValueError("spike multiplier must be at least 1")
    return quantity * normal_spread * (spike_multiplier - 1)


def screen_stocks(
    stocks: Sequence[Mapping[str, Any]],
    min_liquidity: float,
    max_pe: float,
    min_momentum: float,
) -> list[str]:
    keep = [
        s["symbol"]
        for s in stocks
        if s["liquidity"] >= min_liquidity and s["pe"] <= max_pe and s["momentum"] >= min_momentum
    ]
    return sorted(keep)


def parse_earnings(lines: Sequence[str]) -> dict[str, dt.date]:
    out: dict[str, dt.date] = {}
    for line in lines:
        symbol, _, day = line.partition(",")
        if not symbol or not day:
            raise ValueError(f"malformed earnings line {line!r}")
        out[symbol] = dt.date.fromisoformat(day)
    return out


def volume_heatmap(bars: Sequence[tuple[int, int, float]]) -> dict[int, dict[int, float]]:
    grid: dict[int, dict[int, float]] = {}
    for bucket, day, volume in bars:
        grid.setdefault(bucket, {})
        grid[bucket][day] = grid[bucket].get(day, 0.0) + volume
    return grid


def reason_stack(reasons: Sequence[str]) -> str:
    if not reasons:
        raise ValueError("a trade needs at least one reason")
    return " ".join(f"{i}) {r}" for i, r in enumerate(reasons, start=1))


def exit_resolution(
    side: str, stop: float, target: float, open_: float, high: float, low: float
) -> tuple[str, float] | None:
    if side == "LONG":
        if open_ <= stop:
            return ("STOP_GAP", open_)
        if low <= stop:
            return ("STOP", stop)
        if high >= target:
            return ("TARGET", target)
        return None
    if side == "SHORT":
        if open_ >= stop:
            return ("STOP_GAP", open_)
        if high >= stop:
            return ("STOP", stop)
        if low <= target:
            return ("TARGET", target)
        return None
    raise ValueError("side must be LONG or SHORT")


def top_k_watchlist(scores: Mapping[str, float], k: int) -> list[str]:
    if k < 1:
        raise ValueError("k must be positive")
    return [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def is_half_day(day: str, half_days: set[str]) -> str:
    return "HALF_DAY" if day in half_days else "NORMAL"


def role_presets(persona: str) -> tuple[str, ...]:
    if persona not in PRESETS:
        raise KeyError(f"unknown persona {persona!r}")
    return PRESETS[persona]


def search_instruments(instruments: Sequence[str], query: str) -> dict[str, Any]:
    q = query.upper()
    exact = next((i for i in instruments if i.upper() == q), None)
    matches = sorted(i for i in instruments if i.upper().startswith(q))
    return {"exact": exact, "matches": matches, "ambiguous": exact is None and len(matches) > 1}


def market_overview(changes: Mapping[str, float]) -> list[tuple[str, float]]:
    return sorted(changes.items(), key=lambda kv: (-kv[1], kv[0]))


def api_doc(routes: Mapping[str, Mapping[str, Any]]) -> str:
    sections = []
    for path in sorted(routes):
        example = routes[path]
        if not example:
            raise ValueError(f"route {path} needs a typed example")
        body = "\n".join(f"  {k}: {type(v).__name__} = {v!r}" for k, v in sorted(example.items()))
        sections.append(f"## {path}\n{body}")
    return "\n\n".join(sections)


def deterministic_example(seed: int) -> str:
    paths = bootstrap_paths([0.01, -0.02, 0.03, 0.0], n_paths=50, horizon=10, seed=seed)
    return hashlib.sha256(repr(paths).encode()).hexdigest()
