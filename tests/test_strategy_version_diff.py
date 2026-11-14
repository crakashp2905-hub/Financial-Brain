from dataclasses import replace

import pytest

from financial_brain.forward_edge.version_diff import (
    StrategyVersionDiffError,
    StrategyVersionSnapshot,
    diff_strategy_versions,
)


def _snapshot(**changes):
    values = dict(strategy_id="swing-momentum", version=1, code_sha256="a" * 64,
                  parameters_sha256="b" * 64, filters_sha256="c" * 64, horizon_sessions=20,
                  universe_sha256="d" * 64, cost_model_sha256="e" * 64,
                  benchmark_sha256="f" * 64, asset_scope=("NSE_EQ",))
    values.update(changes)
    return StrategyVersionSnapshot(**values)


def test_diff_exposes_every_checklist_identity_dimension():
    before = _snapshot()
    after = replace(before, version=2, code_sha256="1" * 64, parameters_sha256="2" * 64,
                    filters_sha256="3" * 64, horizon_sessions=40, universe_sha256="4" * 64,
                    cost_model_sha256="5" * 64, benchmark_sha256="6" * 64,
                    asset_scope=("NSE_EQ", "NSE_FUTURES"))
    diff = diff_strategy_versions(before, after)
    assert {item["category"] for item in diff["changes"]} == {
        "strategy_code", "parameters", "filters", "economic_horizon", "universe",
        "execution_cost_model", "benchmark", "asset_scope",
    }
    assert diff["scientific_change"] is True and diff["version_only"] is False
    assert len(diff["diff_sha256"]) == 64


def test_version_increment_without_identity_change_is_explicit():
    before = _snapshot()
    result = diff_strategy_versions(before, replace(before, version=2))
    assert result["changes"] == []
    assert result["version_only"] is True


@pytest.mark.parametrize("current, message", [
    (_snapshot(strategy_id="another", version=2), "same strategy id"),
    (_snapshot(version=3), "exactly one"),
])
def test_diff_refuses_cross_strategy_or_skipped_versions(current, message):
    with pytest.raises(StrategyVersionDiffError, match=message):
        diff_strategy_versions(_snapshot(), current)


def test_snapshot_rejects_missing_dimension_digest():
    with pytest.raises(StrategyVersionDiffError, match="cost_model_sha256"):
        _snapshot(cost_model_sha256="missing")
