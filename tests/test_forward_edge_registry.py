"""F02 strategy specification and eligibility tests (FE-0023/0024)."""
from __future__ import annotations

import pytest

from financial_brain.kernel.registry import RegistryError, StrategyEligibility, StrategySpec


def test_forward_strategy_spec_validates_all_required_execution_metadata() -> None:
    spec = StrategySpec("orb", "1.0", {"range_bars": 15}, "owner", family="session_breakout",
                        code_sha256="a" * 64, asset_scope=("NSE_EQ",), decision_clock="5m_bar_close",
                        direction="LONG", stop_exit="range_low_or_eod", horizon="intraday",
                        data_requirements=("ohlcv_1m", "exchange_calendar"))
    spec.validate_forward()
    assert StrategySpec.from_json(spec.to_json()).hash() == spec.hash()
    with pytest.raises(RegistryError, match="code_sha256"):
        StrategySpec("orb", "1.0", {}, "owner", family="orb", code_sha256="bad", asset_scope=("NSE_EQ",),
                     decision_clock="bar", direction="LONG", stop_exit="stop", horizon="day",
                     data_requirements=("ohlcv",)).validate_forward()


def test_strategy_eligibility_cannot_omit_lineage_or_use_unrecognized_state() -> None:
    with pytest.raises(RegistryError, match="unknown forward strategy status"):
        StrategyEligibility("orb", "1", "WINNER", "registry", "a" * 64, "reason", "b" * 64)
    with pytest.raises(RegistryError, match="lineage"):
        StrategyEligibility("orb", "1", "FAILED", "", "a" * 64, "reason", "b" * 64)
