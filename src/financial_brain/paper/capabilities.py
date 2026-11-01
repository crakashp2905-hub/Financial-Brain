"""Single inventory of algorithm surfaces available to paper trading.

This is deliberately an inventory, not an automatic strategy launcher.  Every entry
names the paper adapter and its evidence boundary so a new algorithm cannot be
mistaken for a live or broker-connected capability.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperCapability:
    name: str
    mode: str
    adapter: str
    paper_only: bool = True
    requires: tuple[str, ...] = ()
    version: str = "v1"


CAPABILITIES = (
    PaperCapability("portfolio_rebalance", "daily", "paper.engine.Run"),
    PaperCapability("strategy_zoo", "daily", "paper.zoo.run_day"),
    PaperCapability("intraday_orb", "intraday", "paper.intraday.DayEngine", requires=("OHLCV",)),
    PaperCapability("intraday_gap", "intraday", "paper.intraday.DayEngine", requires=("OHLCV",)),
    PaperCapability("intraday_custom_rules", "intraday", "paper.intraday.StrategyRule", requires=("quotes",)),
    PaperCapability("long_option_intent", "options", "paper.options_runner.run_intent", requires=("two_sided_quotes",)),
    PaperCapability("option_spread_intent", "options", "paper.options_spread", requires=("two_sided_quotes",)),
)


def inventory(*, mode: str | None = None) -> tuple[PaperCapability, ...]:
    """Return the immutable paper capability inventory, optionally by mode."""
    if mode is None:
        return CAPABILITIES
    if mode not in {"daily", "intraday", "options"}:
        raise ValueError(f"unknown paper mode: {mode}")
    return tuple(capability for capability in CAPABILITIES if capability.mode == mode)


def require(name: str) -> PaperCapability:
    """Resolve a capability or fail closed instead of silently falling back."""
    for capability in CAPABILITIES:
        if capability.name == name:
            return capability
    raise KeyError(f"unregistered paper capability: {name}")
