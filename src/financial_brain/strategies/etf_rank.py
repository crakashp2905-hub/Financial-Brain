"""ETF cross-sectional ranking with liquidity tiers and a shadow gate (checklist section 5).

Ranks only within a comparability group (e.g. broad equity, sector, gold) on a fixed lookback of
executable *market-price* returns. Each ETF gets a liquidity tier from its median traded value and
spread at intended size; untradeable tiers are excluded, not ranked. The output is ``ETF_SHADOW_ONLY``
unless the cost and confidence gates both pass, even when a ranking exists. Products that are not
in the listed universe (e.g. inverse or leveraged ETFs absent in India) cannot be ranked.
No result here is a backtest verdict; studies must be preregistered through the trial ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EtfRankError(ValueError):
    pass


class Tier(str, Enum):
    LIQUID = "LIQUID"
    THIN = "THIN"
    UNTRADEABLE = "UNTRADEABLE"


class Output(str, Enum):
    ETF_RANKED = "ETF_RANKED"
    ETF_SHADOW_ONLY = "ETF_SHADOW_ONLY"


@dataclass(frozen=True)
class EtfObs:
    uid: str
    group: str
    listed: bool
    closes: tuple[float, ...]          # executable market-price closes, oldest first
    median_traded_value: float          # rupees per session
    spread_bps: float                   # observed effective spread


def tier(o: EtfObs, order_value: float, *, max_participation: float = 0.01,
         max_spread_bps: float = 50.0) -> Tier:
    if o.median_traded_value <= 0 or o.spread_bps > max_spread_bps:
        return Tier.UNTRADEABLE
    if order_value > max_participation * o.median_traded_value:
        return Tier.THIN
    return Tier.LIQUID


def momentum(o: EtfObs, lookback: int, skip: int = 0) -> float:
    if lookback <= 0 or len(o.closes) < lookback + skip + 1:
        raise EtfRankError(f"{o.uid}: not enough history for lookback {lookback}")
    end = len(o.closes) - 1 - skip
    return o.closes[end] / o.closes[end - lookback] - 1


@dataclass(frozen=True)
class Ranked:
    output: Output
    group: str
    order: tuple[tuple[str, float], ...]
    excluded: tuple[tuple[str, str], ...]


def rank_group(obs: list[EtfObs], group: str, *, lookback: int, order_value: float,
               cost_bps: float, min_edge_bps: float, confidence_ok: bool) -> Ranked:
    """Rank one comparability group; shadow unless top-minus-cost clears the edge and confidence."""
    scored, excluded = [], []
    for o in obs:
        if o.group != group:
            continue
        if not o.listed:
            excluded.append((o.uid, "not in the listed universe"))
            continue
        t = tier(o, order_value)
        if t is not Tier.LIQUID:
            excluded.append((o.uid, f"liquidity tier {t.value}"))
            continue
        try:
            scored.append((o.uid, momentum(o, lookback)))
        except EtfRankError as e:
            excluded.append((o.uid, str(e)))
    scored.sort(key=lambda x: x[1], reverse=True)
    out = Output.ETF_SHADOW_ONLY
    if scored and confidence_ok and scored[0][1] * 1e4 - cost_bps >= min_edge_bps:
        out = Output.ETF_RANKED
    return Ranked(out, group, tuple(scored), tuple(excluded))
