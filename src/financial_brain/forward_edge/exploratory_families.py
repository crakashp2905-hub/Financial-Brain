"""Exploratory slow-factor specs separated from the failed cost-aware M1 trial (PE-0049).

These are registration metadata only: they do not claim an edge, create orders, or inherit M1's
trial verdict. They must start a new prospective charter before any confirmatory evaluation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .registry import Status, StrategySpec, parameter_sha256


@dataclass(frozen=True)
class ExploratoryFamily:
    strategy_id: str
    family: str
    feature: str
    direction: str
    horizon_sessions: int
    parameters: tuple[tuple[str, str], ...]
    source_lineage: str

    def to_spec(self) -> StrategySpec:
        params = dict(self.parameters)
        code_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        return StrategySpec(
            strategy_id=self.strategy_id, family=self.family, version=1, code_sha256=code_digest,
            asset_scope=("NSE_EQ",), decision_clock="daily_close", direction=self.direction,
            exit_rule=f"time_{self.horizon_sessions}", horizon_sessions=self.horizon_sessions,
            data_requirements=(self.feature, "adjusted_close", "survivorship_aware_universe"),
            source_lineage=self.source_lineage, status=Status.EXPLORATORY,
            parameter_sha256=parameter_sha256(params),
        )


EXPLORATORY_FAMILIES = (
    ExploratoryFamily(
        strategy_id="PE49-CS-MOM-12-1", family="cross_sectional_momentum_12_1",
        feature="mom_12_1", direction="LONG_SHORT", horizon_sessions=40,
        parameters=(("lookback_sessions", "252"), ("skip_recent_sessions", "21"),
                    ("selection", "cross_sectional_top_bottom_decile")),
        source_lineage="src/financial_brain/features/indicators.py::mom_12_1; exploratory PE-0049",
    ),
    ExploratoryFamily(
        strategy_id="PE49-CS-52W-HIGH", family="cross_sectional_52_week_high",
        feature="dist_52w_high", direction="LONG_SHORT", horizon_sessions=40,
        parameters=(("lookback_sessions", "250"), ("selection", "cross_sectional_top_bottom_decile")),
        source_lineage="src/financial_brain/features/indicators.py::dist_52w_high; exploratory PE-0049",
    ),
)


def exploratory_specs() -> tuple[StrategySpec, ...]:
    """Return unique, observation-only specs; neither inherits M1 nor qualifies for paper intents."""
    return tuple(family.to_spec() for family in EXPLORATORY_FAMILIES)
