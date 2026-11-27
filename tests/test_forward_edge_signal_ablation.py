from __future__ import annotations

from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.signal_ablation import (
    SignalAblationCase,
    SignalAblationError,
    evaluate_signal_ablation,
)


def _case(**overrides):
    values = dict(
        ablation_id="remove-confirmation-v1", removed_signal_id="volume-confirmation",
        session_dates=tuple(date(2026, 10, 1) + timedelta(days=i) for i in range(4)),
        full_net_returns=(0.03, -0.01, 0.02, -0.005),
        ablated_net_returns=(0.01, -0.01, 0.005, -0.005),
        equal_cost_null_net_returns=(0.0, 0.0, 0.0, 0.0),
        full_cost_schedule_sha256="a" * 64,
        ablated_cost_schedule_sha256="a" * 64,
        null_cost_schedule_sha256="a" * 64,
        research_look_id="look-17",
        registered_research_look_ids=("look-1", "look-17", "look-42"),
        predeclared=True,
    )
    values.update(overrides)
    return SignalAblationCase(**values)


def test_paired_ablation_compares_signal_and_equal_cost_null_and_counts_all_looks():
    report = evaluate_signal_ablation(_case())
    assert report["with_signal_vs_ablated"]["mean_paired_net_return_difference"] == pytest.approx(0.00875)
    assert report["with_signal_vs_equal_cost_null"]["mean_paired_net_return_difference"] == pytest.approx(0.00875)
    assert report["registered_research_look_count"] == 3
    assert report["with_signal_vs_ablated"]["bonferroni_adjusted_p"] == pytest.approx(
        min(1.0, report["with_signal_vs_ablated"]["normal_approximation_two_sided_p"] * 3))
    assert report["promotion_authorized"] is False
    assert len(report["evidence_sha256"]) == 64


def test_ablation_rejects_post_hoc_looks_different_costs_and_misaligned_series():
    with pytest.raises(SignalAblationError, match="predeclared"):
        evaluate_signal_ablation(_case(predeclared=False))
    with pytest.raises(SignalAblationError, match="cost schedule"):
        evaluate_signal_ablation(_case(null_cost_schedule_sha256="b" * 64))
    with pytest.raises(SignalAblationError, match="align"):
        evaluate_signal_ablation(_case(equal_cost_null_net_returns=(0.0,)))


def test_constant_paired_difference_does_not_emit_a_fake_significance_p_value():
    report = evaluate_signal_ablation(_case(
        full_net_returns=(0.01, 0.01, 0.01, 0.01),
        ablated_net_returns=(0.0, 0.0, 0.0, 0.0),
        equal_cost_null_net_returns=(0.0, 0.0, 0.0, 0.0)))
    assert report["with_signal_vs_ablated"]["t_statistic"] is None
    assert report["with_signal_vs_ablated"]["bonferroni_adjusted_p"] is None
