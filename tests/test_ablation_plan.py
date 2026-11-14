from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from financial_brain.evaluation.ablation_plan import (
    AblationJournal,
    AblationPlan,
    AblationPlanError,
    evaluate_ablations,
)


def _plan(**changes):
    data = {
        "search_id": "entry-exit-ablation-v1",
        "strategy_sha256": "a" * 64,
        "development_data_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "development_start": "2026-01-02",
        "development_end": "2026-01-03",
        "protected_final_start": "2026-01-04",
        "protected_final_end": "2026-02-01",
        "frozen_at_utc": "2026-01-01T00:00:00Z",
        "axes": (
            ("entry_rule", ("breakout", "pullback")),
            ("exit_rule", ("time_exit",)),
            ("holding_horizon", (5,)),
            ("portfolio_width", (10,)),
            ("turnover_limit", (0.2,)),
        ),
        "max_combinations": 2,
    }
    data.update(changes)
    return AblationPlan(**data)


def _outcomes(plan=None):
    plan = plan or _plan()
    rows = []
    for combo in plan.combinations:
        rows.append({"split": "TRAINING", "combination": combo, "session_date": "2026-01-02",
                     "decision_sha256": "1" * 64, "net_alpha": -0.01 if combo["entry_rule"] == "breakout" else 0.02})
        rows.append({"split": "TRAINING", "combination": combo, "session_date": "2026-01-03",
                     "decision_sha256": "2" * 64, "net_alpha": 0.0})
    return rows


def test_full_grid_retains_negative_zero_and_positive_outcomes_for_every_cell():
    plan = _plan()
    result = evaluate_ablations(plan=plan, outcomes=_outcomes(plan))
    assert result.combination_count == 2 and result.session_count == 2
    assert len(result.outcomes) == 4
    assert all(item["disposition"] == "TRAINING_ONLY_DIAGNOSTIC" for item in result.evaluations)
    breakout = next(item for item in result.evaluations if item["combination"]["entry_rule"] == "breakout")
    assert breakout["negative_sessions"] == 1 and breakout["zero_sessions"] == 1
    assert breakout["mean_net_alpha"] == pytest.approx(-0.005)


def test_every_combination_uses_same_schedule_and_missing_cells_are_rejected():
    plan = _plan()
    rows = _outcomes(plan)
    with pytest.raises(AblationPlanError, match="same frozen"):
        evaluate_ablations(plan=plan, outcomes=rows[:-1])
    with pytest.raises(AblationPlanError, match="every predeclared"):
        evaluate_ablations(plan=plan, outcomes=rows[:2])


def test_holdout_access_nontraining_splits_and_unknown_grid_cells_are_rejected():
    plan = _plan()
    rows = _outcomes(plan)
    rows[0]["session_date"] = "2026-01-04"
    with pytest.raises(AblationPlanError, match="outside development"):
        evaluate_ablations(plan=plan, outcomes=rows)
    rows = _outcomes(plan)
    rows[0]["split"] = "FINAL"
    with pytest.raises(AblationPlanError, match="TRAINING rows only"):
        evaluate_ablations(plan=plan, outcomes=rows)


def test_plan_requires_exact_axes_full_budget_and_predeclared_freeze():
    plan = _plan()
    with pytest.raises(AblationPlanError, match="smaller"):
        _plan(max_combinations=1)
    with pytest.raises(AblationPlanError, match="exactly these axes"):
        _plan(axes=plan.axes[:-1])
    with pytest.raises(AblationPlanError, match="freeze"):
        _plan(frozen_at_utc="2026-01-03T00:00:00Z")


def test_hash_chain_archives_all_results_and_rejects_retroactive_replacement(tmp_path):
    plan = _plan()
    result = evaluate_ablations(plan=plan, outcomes=_outcomes(plan))
    registry = AblationJournal(tmp_path / "ablations.jsonl")
    assert registry.record(plan, result) == result.sha256
    assert registry.record(plan, result) == result.sha256
    altered = replace(result, outcome_sha256=hashlib.sha256(b"changed").hexdigest())
    with pytest.raises(AblationPlanError, match="does not reconcile"):
        registry.record(plan, altered)
