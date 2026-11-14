from __future__ import annotations

import hashlib
import json
import pytest

from financial_brain.forward_edge.horizon_search import (
    HORIZON_GRID,
    HorizonSearchError,
    HorizonSearchPlan,
    HorizonSearchRegistry,
    evaluate_development_horizons,
)


def _rows():
    returns = {20: 0.01, 40: 0.018, 60: 0.025}
    return [{"split": "DEVELOPMENT", "exchange_date": "2026-10-01",
             "horizon_sessions": horizon, "net_alpha": value,
             "decision_sha256": f"{decision:064x}"}
            for decision in range(1, 6) for horizon, value in returns.items()]


def _plan(rows=None, **changes):
    rows = _rows() if rows is None else rows
    normalized = sorted(rows, key=lambda row: (row["exchange_date"], row["horizon_sessions"],
                                               row["decision_sha256"]))
    data_sha = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    args = dict(search_id="horizon-dev-q4", strategy_sha256="a" * 64,
                data_sha256=data_sha, protocol_sha256="b" * 64,
                development_start="2026-10-01", development_end="2026-10-31",
                confirmatory_start="2026-12-01", frozen_at_utc="2026-09-30T23:00:00+00:00",
                horizons=HORIZON_GRID, search_budget=3)
    args.update(changes)
    return HorizonSearchPlan(**args), rows


def test_fixed_budget_compares_every_horizon_on_matched_development_decisions():
    plan, rows = _plan()
    result = evaluate_development_horizons(plan=plan, observations=rows)
    assert result.tried_horizons == (20, 40, 60)
    assert [row[0] for row in result.evaluations] == [20, 40, 60]
    assert all(row[1] == 5 for row in result.evaluations)
    assert result.exploratory_best_horizon == 20  # annualized, not cumulative multi-horizon return


def test_search_rejects_holdout_rows_extra_trials_and_unmatched_cohorts():
    plan, rows = _plan()
    holdout = [{**row, "split": "VALIDATION"} for row in rows]
    with pytest.raises(HorizonSearchError, match="DEVELOPMENT"):
        evaluate_development_horizons(plan=plan, observations=holdout)
    extra = [{**rows[0], "horizon_sessions": 120}]
    with pytest.raises(HorizonSearchError, match="outside the declared"):
        evaluate_development_horizons(plan=plan, observations=extra)
    unmatched = [row for row in rows if not (row["horizon_sessions"] == 60
                                              and row["decision_sha256"] == f"{5:064x}")]
    altered_plan, _ = _plan(unmatched)
    with pytest.raises(HorizonSearchError, match="matched decision"):
        evaluate_development_horizons(plan=altered_plan, observations=unmatched)


def test_search_rejects_data_not_matching_frozen_digest_and_unbudgeted_grid():
    plan, rows = _plan()
    altered = [*rows, *[{**next(row for row in rows if row["horizon_sessions"] == horizon),
                          "decision_sha256": f"{99:064x}", "net_alpha": 0.9}
                         for horizon in HORIZON_GRID]]
    with pytest.raises(HorizonSearchError, match="data digest"):
        evaluate_development_horizons(plan=plan, observations=altered)
    with pytest.raises(HorizonSearchError, match="three-horizon budget"):
        _plan(horizons=(20, 40, 60, 120), search_budget=4)


def test_registry_records_plan_and_all_trials_immutably(tmp_path):
    plan, rows = _plan()
    result = evaluate_development_horizons(plan=plan, observations=rows)
    registry = HorizonSearchRegistry(tmp_path / "horizon-search.jsonl")
    assert registry.record(plan, result) == result.sha256
    assert registry.record(plan, result) == result.sha256
    incomplete = type(result)(**{**result.__dict__, "tried_horizons": (20, 40)})
    with pytest.raises(HorizonSearchError, match="every trial"):
        registry.record(plan, incomplete)
