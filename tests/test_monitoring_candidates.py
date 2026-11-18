from __future__ import annotations

import pytest

from financial_brain.forward_edge.monitoring_candidates import (
    MonitoringCandidateError,
    candidate_specs,
    monitoring_signal,
)
from financial_brain.forward_edge.registry import (MechanismHypothesis, Registry, RegistryError,
                                                   Status, StrategySpec, is_monitoring_family)


HYPOTHESIS = MechanismHypothesis(
    "Opening order flow may create a temporary imbalance.", "Slow execution can extend a move.",
    "Late entrants can pay.", ("liquid continuous session",),
    "No costed excess remains against matched controls.")


def test_orb_gap_directional_and_chart_specs_are_shadow_monitoring_only():
    registry = Registry()
    specs = candidate_specs()
    assert {spec.strategy_id for spec in specs} == {
        "MON-ORB", "MON-GAP-CONT", "MON-GAP-FADE", "MON-DIRECTIONAL-CHART", "MON-CHART-PATTERN",
    }
    for spec in specs:
        registry.register(spec)
        assert spec.status is Status.SHADOW_ONLY
        assert spec.mechanism_hypothesis is not None
        assert spec.mechanism_hypothesis.sha256()
        assert spec.mechanism_hypothesis.falsifier.strip()
        assert not registry.can_place_paper_intents(spec.strategy_id)
        obs = monitoring_signal(spec, as_of="2026-11-14T15:30:00+05:30", signal="LONG", registry=registry)
        assert obs.state == "MONITORING_ONLY" and not hasattr(obs, "quantity")
        with pytest.raises(RegistryError, match="monitoring-only"):
            registry.transition(spec.strategy_id, Status.PAPER_ELIGIBLE, reason="try promotion")


@pytest.mark.parametrize("family", ["ORB", "opening-range-breakout", "gap_fill", "chart pattern",
                                     "directional_chart_pattern", "candlestick_reversal"])
def test_registry_policy_normalizes_monitoring_family_names(family: str):
    assert is_monitoring_family(family)


def test_monitoring_signal_rejects_non_monitor_family_and_unregistered_spec():
    registry = Registry()
    normal = StrategySpec(strategy_id="MOM", family="momentum", version=1, code_sha256="a" * 64,
                          asset_scope=("NSE_EQ",), decision_clock="close", direction="LONG",
                          exit_rule="time", horizon_sessions=5, data_requirements=("close",),
                          source_lineage="test", status=Status.SHADOW_ONLY,
                          mechanism_hypothesis=HYPOTHESIS)
    with pytest.raises(MonitoringCandidateError, match="not on the monitoring"):
        monitoring_signal(normal, as_of="d1", signal="LONG", registry=registry)
    orb = next(spec for spec in candidate_specs() if spec.strategy_id == "MON-ORB")
    with pytest.raises(MonitoringCandidateError, match="must be registered"):
        monitoring_signal(orb, as_of="d1", signal="LONG", registry=registry)


def test_monitoring_family_cannot_be_constructed_as_paper_eligible():
    with pytest.raises(RegistryError, match="monitoring-only"):
        StrategySpec(strategy_id="ORB-paper", family="orb", version=1, code_sha256="a" * 64,
                     asset_scope=("NSE_EQ",), decision_clock="close", direction="LONG",
                     exit_rule="time", horizon_sessions=5, data_requirements=("close",),
                     source_lineage="test", status=Status.PAPER_ELIGIBLE, benchmark_id="NIFTY",
                     mechanism_hypothesis=HYPOTHESIS)
