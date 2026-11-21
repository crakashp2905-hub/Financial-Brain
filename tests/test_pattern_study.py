"""Study protocol and controls (CP-0154..0188), offline and labelled synthetic where needed."""

from __future__ import annotations

import dataclasses

import pytest

from financial_brain.patterns.study import (
    BudgetExhausted,
    ProspectiveViolation,
    StudySpec,
    VariantRegistry,
    assert_prospective,
    benjamini_hochberg,
    block_bootstrap_ci,
    clock_panels,
    dry_run_report,
    entry_index,
    fixed_exit_index,
    incremental_value,
    matched_controls,
    purged_split,
    reality_check_adapter,
    report_record,
    require_budget,
    stress_matrix,
    synthetic_edge,
    synthetic_null,
    time_shift_placebo,
)
from financial_brain.patterns.detectors import Formation

SPEC = StudySpec(
    family="rectangle",
    variants=("rect_w20", "rect_w30"),
    periods=("2019-01-01", "2024-12-31"),
    entry="next_open",
    exit_horizons=(5, 20, 60),
    cost_bps=12.0,
    benchmarks=("cash", "momentum"),
)


def test_spec_hash_changes_with_any_parameter_and_family_size_counts_variants() -> None:
    other = dataclasses.replace(SPEC, cost_bps=13.0)
    assert SPEC.sha256 != other.sha256
    assert SPEC.trial_family_size == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"entry": "same_close"},
        {"exit_horizons": (20, 5)},
        {"benchmarks": ("ultimate_high",)},
        {"cost_bps": -1.0},
    ],
)
def test_spec_rejects_forbidden_designs(bad: dict) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(SPEC, **bad)


def test_protected_pnl_refused_while_budget_spent() -> None:
    protected = dataclasses.replace(SPEC, protected=True)
    require_budget(SPEC, evaluate_pnl=True)  # unprotected spec is fine
    with pytest.raises(BudgetExhausted):
        require_budget(protected, evaluate_pnl=True)
    require_budget(protected, evaluate_pnl=False)  # descriptive use is allowed


def test_dry_run_computes_no_returns() -> None:
    report = dry_run_report(SPEC, {"rect": 120, "tri": 0})
    assert report["returns_computed"] is False
    assert report["eligible"] is False


def test_entry_and_exit_timing() -> None:
    assert entry_index(10) == 11
    assert fixed_exit_index(11, 5) == 16
    with pytest.raises(ValueError):
        fixed_exit_index(11, 0)


def test_clock_panels_expose_unconfirmed_and_open_candidates() -> None:
    def f(known, conf=None, fail=None):
        return Formation("rectangle", "1", known - 3, known, confirmed_index=conf, failed_index=fail,
                         confirmed_direction="bullish" if conf is not None else None)

    panel = clock_panels([f(5, conf=8), f(6, fail=9), f(7)], at=20)
    assert (panel.candidates, panel.confirmed, panel.failed, panel.still_open) == (3, 1, 1, 1)
    assert panel.confirmed_rate_of_candidates == pytest.approx(1 / 3)


def test_purged_split_has_embargo_gap() -> None:
    train, test = purged_split(100, train_end=60, embargo=5)
    assert list(train)[-1] == 59 and test.start == 65
    with pytest.raises(ValueError):
        purged_split(100, train_end=99, embargo=5)


def test_block_bootstrap_interval_contains_point_on_clustered_data() -> None:
    clusters = {f"S{i}": [0.01 + 0.001 * i + 0.0005 * j for j in range(10)] for i in range(12)}
    point, lo, hi = block_bootstrap_ci(clusters, reps=400, seed=3)
    assert lo <= point <= hi


def test_benjamini_hochberg_keeps_only_surviving_hypotheses() -> None:
    survivors = benjamini_hochberg({"a": 0.001, "b": 0.02, "c": 0.5, "d": 0.6}, q=0.05)
    assert survivors == {"a", "b"}  # rank-2 threshold is 2*0.05/4 = 0.025 and p=0.02 passes
    assert benjamini_hochberg({"x": 0.9}, q=0.05) == set()


def test_matched_controls_are_nearest_and_none_without_pool() -> None:
    cands = [{"mom": 0.1, "vol": 0.2}]
    pool = [{"mom": 0.9, "vol": 0.9}, {"mom": 0.12, "vol": 0.21}]
    assert matched_controls(cands, pool, ("mom", "vol")) == [1]
    assert matched_controls(cands, [], ("mom", "vol")) == [None]


def test_placebo_stays_inside_eligible_dates() -> None:
    eligible = [1, 2, 3, 10]
    assert time_shift_placebo(eligible, 20, shift=1) == [2, 3]


def test_incremental_value_requires_matched_series() -> None:
    assert incremental_value([0.02, 0.0], [0.01, 0.01]) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        incremental_value([0.1], [0.1, 0.2])


def test_stress_matrix_only_gets_worse_with_spread() -> None:
    m = stress_matrix([0.01, 0.02], spreads_bps=(0.0, 10.0, 50.0))
    assert m[0.0] > m[10.0] > m[50.0]


def test_backfilled_observation_refused() -> None:
    assert_prospective("2026-02-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
    with pytest.raises(ProspectiveViolation):
        assert_prospective("2025-12-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")


def test_synthetic_harness_distinguishes_null_from_injected_edge() -> None:
    null_mean = sum(synthetic_null(1)) / 500
    edge_mean = sum(synthetic_edge(1)) / 500
    assert abs(null_mean) < 0.002
    assert edge_mean > 0.001


def test_report_requires_every_provenance_field() -> None:
    with pytest.raises(ValueError, match="missing"):
        report_record(statistic=0.1)
    full = report_record(statistic=0.1, denominator=100, benchmark="cash", eligibility="PIT",
                         caveats=["synthetic"], next_gate="forward paper", lineage={"spec": SPEC.sha256})
    assert full["next_gate"] == "forward paper"


def test_data_snooping_audit_counts_every_variant() -> None:
    reg = VariantRegistry()
    reg.add("rect_w20", window=20)
    reg.add("rect_w30", window=30)
    assert reg.audit()["family_size"] == 2


def test_reality_check_runs_on_existing_implementation() -> None:
    returns = {
        "v1": [0.001 * ((i * 7) % 5 - 2) for i in range(200)],
        "v2": [0.0005 * ((i * 3) % 4 - 1.5) for i in range(200)],
    }
    result = reality_check_adapter(returns, reps=50)
    assert isinstance(result, dict) and result
