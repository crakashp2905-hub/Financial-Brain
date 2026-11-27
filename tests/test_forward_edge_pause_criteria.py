from __future__ import annotations

from datetime import date

import pytest

from financial_brain.forward_edge.pause_criteria import (
    PauseCriteria, PauseCriteriaError, PauseCriteriaLedger, PauseMetrics,
)


def _criteria(version: int = 1, **updates) -> PauseCriteria:
    values = {"strategy_id": "momentum-v1", "version": version,
              "benchmark_window_sessions": 20, "minimum_benchmark_excess": -0.02,
              "maximum_drawdown": 0.15, "maximum_cost_to_gross_ratio": 0.35,
              "minimum_source_quality": 0.95, "maximum_quote_age_seconds": 60.0}
    values.update(updates)
    return PauseCriteria(**values)


def _metrics(**updates) -> PauseMetrics:
    values = {"benchmark_excess": 0.01, "benchmark_window_sessions": 20,
              "drawdown": 0.05, "cost_to_gross_ratio": 0.20,
              "source_quality": 0.99, "quote_age_seconds": 10.0,
              "evidence_sha256": "a" * 64}
    values.update(updates)
    return PauseMetrics(**values)


def test_frozen_pause_criteria_continue_or_pause_on_each_domain():
    ledger = PauseCriteriaLedger(":memory:")
    criteria = _criteria()
    ledger.register(criteria)
    healthy = ledger.evaluate(criteria=criteria, session=date(2026, 11, 27), metrics=_metrics())
    assert healthy.state == "CONTINUE"
    assert ledger.append_decision(healthy) is True
    assert ledger.append_decision(healthy) is False
    deteriorated = ledger.evaluate(criteria=criteria, session=date(2026, 11, 30), metrics=_metrics(
        benchmark_excess=-0.04, drawdown=0.20, cost_to_gross_ratio=0.5,
        source_quality=0.80, quote_age_seconds=120.0))
    assert deteriorated.state == "PAUSE"
    assert len(deteriorated.reasons) == 5
    assert ledger.append_decision(deteriorated) is True
    ledger.close()


def test_missing_source_or_cost_evidence_fails_closed():
    ledger = PauseCriteriaLedger(":memory:")
    criteria = _criteria()
    ledger.register(criteria)
    decision = ledger.evaluate(criteria=criteria, session=date(2026, 11, 27),
                               metrics=_metrics(source_quality=None, cost_to_gross_ratio=None))
    assert decision.state == "PAUSE"
    assert "MISSING_OR_INVALID_SOURCE_QUALITY" in decision.reasons
    assert "MISSING_OR_INVALID_COST_TO_GROSS_RATIO" in decision.reasons


def test_criteria_versions_are_immutable_and_cannot_relax_thresholds():
    ledger = PauseCriteriaLedger(":memory:")
    first = _criteria()
    ledger.register(first)
    with pytest.raises(PauseCriteriaError, match="cannot automatically relax"):
        ledger.register(_criteria(version=2, maximum_drawdown=0.20))
    with pytest.raises(PauseCriteriaError, match="cannot automatically relax"):
        ledger.register(_criteria(version=2, minimum_benchmark_excess=-0.05))
    stricter = _criteria(version=2, maximum_drawdown=0.10, minimum_source_quality=0.98)
    ledger.register(stricter)
    with pytest.raises(PauseCriteriaError, match="immutable"):
        ledger.register(_criteria(version=2, maximum_drawdown=0.08, minimum_source_quality=0.98))
    with pytest.raises(PauseCriteriaError, match="exact registered"):
        ledger.evaluate(criteria=_criteria(version=3), session=date(2026, 11, 27), metrics=_metrics())
    ledger.close()


def test_pause_decision_conflict_for_same_strategy_and_session_is_rejected():
    ledger = PauseCriteriaLedger(":memory:")
    criteria = _criteria()
    ledger.register(criteria)
    day = date(2026, 11, 27)
    first = ledger.evaluate(criteria=criteria, session=day, metrics=_metrics())
    ledger.append_decision(first)
    changed = ledger.evaluate(criteria=criteria, session=day, metrics=_metrics(drawdown=0.12))
    with pytest.raises(PauseCriteriaError, match="immutable"):
        ledger.append_decision(changed)


def test_out_of_domain_metrics_pause_even_when_the_numeric_comparison_looks_safe():
    ledger = PauseCriteriaLedger(":memory:")
    criteria = _criteria()
    ledger.register(criteria)
    decision = ledger.evaluate(
        criteria=criteria, session=date(2026, 11, 27),
        metrics=_metrics(drawdown=-0.1, source_quality=1.2, quote_age_seconds=-1.0))
    assert decision.state == "PAUSE"
    assert "INVALID_DRAWDOWN_RANGE" in decision.reasons
    assert "INVALID_SOURCE_QUALITY_RANGE" in decision.reasons
    assert "INVALID_QUOTE_AGE_RANGE" in decision.reasons
