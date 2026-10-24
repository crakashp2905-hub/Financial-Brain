from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
import duckdb

from financial_brain.execution.algos import Day, Order
from financial_brain.research.causal_regime import CausalGaussianHMM, compare_regimes
from financial_brain.research.counterfactual_store import (
    get_context, record_context, record_rebalance_context,
)
from financial_brain.research.diagnostics import (
    TrainingDistanceGate, TrainingDriftGate, action_path_error, calibration_report,
    regime_transitions, selective_performance,
)
from financial_brain.research.ensemble_walkforward import compare_ensembles, evaluate_ensemble_paper
from financial_brain.research.execution_compare import compare_execution
from financial_brain.research.history_audit import audit_existing_history
from financial_brain.research.inverse_strategy import (
    FEATURES, POLICIES, extract_rules, from_existing_decisions, infer_policy, infer_sequence_policy, regime_report,
    synthetic_history, validate_history,
)
from financial_brain.research.rea_evidence import review_export
from financial_brain.research.trader_contracts import (
    OpportunityEstimate, ReliabilityGate, ensemble_forecast, feasible_regret, rank_opportunities,
    reliability_curve,
)
from financial_brain.research.trader_report import opportunity_report, strategy_report
from financial_brain.research.workstation import compare_reports, main as workstation_main
from financial_brain.storage.db import SCHEMA


@pytest.mark.parametrize("policy", POLICIES)
def test_synthetic_inverse_pipeline(policy):
    data = synthetic_history(policy, n=300, seed=11)
    result = infer_policy(data, models=("logistic", "tree"), seed=11)
    rules = extract_rules(result, data)
    assert result.holdout["n"] > 40
    assert result.split["train_end"] < result.split["validation_start"]
    assert result.split["validation_end"] < result.split["holdout_start"]
    assert 0 <= rules["validation_fidelity"] <= 1
    assert rules["rules"] and rules["version_hash"]
    assert regime_report(data)["status"] == "outcomes_unavailable"


def test_rejects_future_information_and_unauthorized_import():
    data = synthetic_history("momentum", n=180)
    with pytest.raises(PermissionError):
        validate_history(data)
    data.loc[0, "state_asof"] = data.loc[0, "decision_ts"] + np.timedelta64(1, "m")
    with pytest.raises(ValueError, match="future"):
        validate_history(data, authorized=True)
    data["state_asof"] = data.state_asof.astype(object)
    data.loc[0, "state_asof"] = "2016-01-01 00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        validate_history(data, authorized=True)


def test_rejects_future_regime_label():
    data = synthetic_history("momentum", n=180)
    data["net_return"] = 0.01
    data["regime_asof"] = data.decision_ts + np.timedelta64(1, "m")
    with pytest.raises(ValueError, match="future"):
        regime_report(data)


def test_ensemble_requires_validation_and_convexity():
    assert ensemble_forecast({"a": 0.1}, validated=set())["abstain"]
    out = ensemble_forecast({"a": 0.1, "b": -0.1}, validated={"a", "b"}, weights={"a": 3, "b": 1})
    assert out["forecast"] == pytest.approx(0.05)
    assert sum(out["weights"].values()) == pytest.approx(1)
    with pytest.raises(ValueError):
        ensemble_forecast({"a": 0.1}, validated={"a"}, weights={"a": -1})


def test_reliability_abstains_until_calibrated():
    gate = ReliabilityGate(0.7, min_samples=5)
    assert gate.assess(np.ones(2), np.ones(2), 0.9)["abstain"]
    good = gate.assess(np.full(5, 0.8), np.ones(5), 0.9)
    assert not good["abstain"]
    with pytest.raises(ValueError, match="future"):
        gate.assess(np.ones(5), np.ones(5), 0.9,
                    asof=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    fitted_through=datetime(2020, 1, 2, tzinfo=timezone.utc))
    assert reliability_curve(np.array([0.6, 0.8]), np.array([False, True]))[1]["coverage"] == 0.5


def test_alpha_separate_from_utility_and_feasible_regret():
    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    item = OpportunityEstimate("TEST", "LONG", now, 5, "candidate", "bull", .04, .01,
                               .002, .001, .003, .004, .02, -.005, .8, True,
                               calibration_status="calibrated")
    assert item.net_alpha == pytest.approx(.02)
    assert item.utility() == pytest.approx(-.005)
    assert rank_opportunities([item]) == [item]
    assert rank_opportunities([OpportunityEstimate("TEST", "LONG", now, 5, "candidate", "bull", .04, .01,
                                               .002, .001, .003, .004, .02, -.005, .8, True)]) == []
    chosen = {"net_return": .01, "feasible_at_decision": True, "decision_ts": now}
    alternate = {"net_return": .05, "feasible_at_decision": False, "decision_ts": now,
                 "known_at_decision": True}
    assert feasible_regret(chosen, [alternate])["descriptive_hindsight_regret"] == 0


def test_execution_comparison_is_labeled_proxy():
    n = 12
    prices = np.full(n, 100.0)
    day = Day(np.arange(n), prices, prices + .1, prices - .1, prices,
              np.full(n, 1000.0), np.full(n, 1 / n), .02, .0002)
    rows = compare_execution(day, Order(100, 0, n - 1), fee_bp=5)
    assert len(rows) == 6
    assert all(0 <= row["fill_ratio"] <= 1 and row["unfilled_orders_modeled"] for row in rows)
    assert any(row["policy"] == "passive_limit" for row in rows)
    forced = compare_execution(day, Order(100, 0, n - 1), fee_bp=5, force_completion=True)
    assert all(row["fill_ratio"] == 1 and not row["unfilled_orders_modeled"] for row in forced)


def test_rea_disabled_and_redacts(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"hypothesis":"token=secret123"}', encoding="utf-8")
    with pytest.raises(PermissionError):
        review_export(path)
    report = review_export(path, enabled=True, authorized=True)
    assert "secret123" not in str(report)
    assert report["review_required"]


def test_ensemble_walkforward_uses_only_known_outcomes():
    n = 90
    dates = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    panel = pd.DataFrame({"decision_ts": dates, "estimate_asof": dates - pd.Timedelta(minutes=1),
                          "observed_at": dates + pd.Timedelta(days=2),
                          "regime": np.arange(n) % 2, "realized": np.sin(np.arange(n)) / 100,
                          "a": np.zeros(n), "b": np.ones(n) / 100})
    first = compare_ensembles(panel, ("a", "b"), validated={"a", "b"}, min_train=20, window=30)
    assert set(first.policy) == {"equal", "static", "regime", "online"}
    assert (first.training_observed_through < first.decision_ts).all()
    panel.loc[89, "realized"] = 1000
    second = compare_ensembles(panel, ("a", "b"), validated={"a", "b"}, min_train=20, window=30)
    assert first.forecast.tolist() == second.forecast.tolist()
    with pytest.raises(ValueError, match="unvalidated"):
        compare_ensembles(panel, ("a", "b"), validated={"a"})
    future_estimate = panel.copy()
    future_estimate.loc[0, "estimate_asof"] = dates[0] + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="future estimate"):
        compare_ensembles(future_estimate, ("a", "b"), validated={"a", "b"})
    panel.loc[89, "realized"] = np.sin(89) / 100
    panel["cost_expected"] = .001
    panel["cost_realized"] = .001
    panel["benchmark_expected"] = 0.0
    panel["benchmark_realized"] = 0.0
    panel["turnover"] = 1.0
    paper = evaluate_ensemble_paper(panel, ("a", "b"), validated={"a", "b"},
                                    min_train=20, window=30, min_expected_alpha=.002)
    assert paper["status"] == "descriptive_paper_only"
    assert set(paper["policies"]) == {"equal", "static", "regime", "online"}
    changed_benchmark = panel.copy()
    changed_benchmark.loc[89, "benchmark_realized"] = 99.0
    changed_paper = evaluate_ensemble_paper(changed_benchmark, ("a", "b"),
                                            validated={"a", "b"}, min_train=20, window=30,
                                            min_expected_alpha=.002)
    assert all(changed_paper["policies"][name]["trades"] == paper["policies"][name]["trades"]
               for name in paper["policies"])
    changed_cost = panel.copy()
    changed_cost.loc[89, "cost_realized"] = .5
    changed_paper = evaluate_ensemble_paper(changed_cost, ("a", "b"),
                                            validated={"a", "b"}, min_train=20, window=30,
                                            min_expected_alpha=.002)
    assert all(changed_paper["policies"][name]["trades"] == paper["policies"][name]["trades"]
               for name in paper["policies"])


def test_immutable_counterfactual_context():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text(encoding="utf-8"))
    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    kwargs = {"decision_ts": now, "state_asof": now, "instrument": "TEST", "action": "HOLD",
              "state": {"price": 100}, "constraints": {"cash": 0},
              "alternatives": [{"instrument": "ALT", "feasible_at_decision": False,
                                "reason": "no available cash"}], "provenance": "paper-test"}
    context_id = record_context(con, **kwargs)
    assert get_context(con, context_id)["constraints"]["cash"] == 0
    with pytest.raises(duckdb.ConstraintException):
        record_context(con, **kwargs)
    with pytest.raises(ValueError, match="as-of"):
        record_context(con, **{**kwargs, "state_asof": now + pd.Timedelta(minutes=1)})


def test_selective_performance_and_calibration():
    dates = pd.date_range("2020-01-01", periods=4, freq="D", tz="UTC")
    paper = pd.DataFrame({"decision_ts": dates, "reliability": [.6, .8, .9, .4],
                          "correct": [False, True, True, False],
                          "gross_return": [-.02, .03, .02, -.01],
                          "cost": [.005] * 4, "turnover": [1] * 4})
    report = selective_performance(paper, thresholds=(0.5, 0.7))
    assert report[1]["n"] == 2
    assert report[1]["mean_net_return"] == pytest.approx(.02)
    assert report[1]["max_drawdown"] == pytest.approx(0)
    calibrated = calibration_report(np.array([.2, .8]), np.array([False, True]), bins=2)
    assert calibrated["brier"] == pytest.approx(.04)
    assert calibrated["bins"][1]["wilson_95pct"][1] == 1


def test_regime_and_action_path_causality():
    dates = pd.date_range("2020-01-01", periods=3, freq="D", tz="UTC")
    labels = pd.DataFrame({"decision_ts": dates, "regime_asof": dates - pd.Timedelta(minutes=1),
                           "regime": ["bull", "bull", "bear"]})
    assert regime_transitions(labels)["transitions"]["bull->bear"] == 1
    labels.loc[0, "regime_asof"] = dates[0] + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="future"):
        regime_transitions(labels)
    path = pd.DataFrame({"decision_ts": dates, "actual_action": ["BUY", "HOLD", "EXIT"],
                         "predicted_action": ["HOLD", "BUY", "EXIT"],
                         "actual_position": [1, 1, 0], "predicted_position": [0, 1, 0]})
    assert action_path_error(path)["position_mae"] == pytest.approx(1 / 3)


def test_causal_hidden_regime_filter():
    rng = np.random.default_rng(22)
    train = np.r_[rng.normal(-1, .2, (40, 2)), rng.normal(0, .2, (40, 2)),
                  rng.normal(1, .2, (40, 2))]
    future = rng.normal(0, .4, (20, 2))
    result = compare_regimes(train, future, feature_names=("trend", "volatility"))
    assert result["hmm_probabilities"].shape == (20, 3)
    assert np.allclose(result["transition"].sum(axis=1), 1)
    changed = future.copy()
    changed[-1] = 100
    model = result["model"]
    prior = model.filter(train)[-1]
    assert np.allclose(model.filter(future, prior=prior)[:-1], model.filter(changed, prior=prior)[:-1])
    with pytest.raises(ValueError):
        CausalGaussianHMM.fit(train[:10], feature_names=("trend", "volatility"))


def test_sequence_baseline_has_chronological_split():
    data = synthetic_history("momentum", n=300)
    result = infer_sequence_policy(data, lags=2)
    assert result.model_name == "lagged_forest"
    assert result.split["train_end"] < result.split["validation_start"]
    rules = extract_rules(result, data)
    assert "prior_1_BUY" in rules["features"]
    assert 0 <= rules["validation_fidelity"] <= 1


def test_trader_reports_restrict_unvalidated_claims():
    data = synthetic_history("momentum", n=180)
    result = infer_policy(data, models=("tree",))
    rules = extract_rules(result, data)
    report = strategy_report(result, rules, regime_report(data))
    assert report["strategy"]["health"] == "requires_market_validation"
    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    estimate = OpportunityEstimate("TEST", "LONG", now, 5, "candidate", "bull", .04, .01,
                                   .002, .001, .003, .004, .02, -.005, .8, True,
                                   invalidation=("close below prior low",))
    card = opportunity_report(estimate, supporters=["candidate"], opponents=[])
    assert card["expected_net_alpha"] == pytest.approx(.02)
    assert "guaranteed" in card["warning"]


def test_history_audit_does_not_relabel_rejections_as_actions():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE decisions (action VARCHAR)")
    con.execute("CREATE TABLE opportunity_memory (disposition VARCHAR)")
    con.execute("INSERT INTO decisions VALUES ('BUY'), ('WATCH')")
    con.execute("INSERT INTO opportunity_memory VALUES ('TAKEN'), ('REJECTED_RANK')")
    report = audit_existing_history(con)
    assert report["recognized_decision_actions"] == 1
    assert report["explicit_paper_action_rows"] == 1
    assert report["rejected_candidate_rows"] == 1
    assert not report["training_ready"]


def test_train_frozen_ood_gate():
    rng = np.random.default_rng(3)
    train = rng.normal(size=(100, 2))
    gate = TrainingDistanceGate().fit(train)
    result = gate.assess(np.array([[0., 0.], [100., 100.]]))
    assert not result["ood"][0]
    assert result["ood"][1]
    threshold = gate.threshold
    gate.assess(np.array([[1_000., 1_000.]]))
    assert gate.threshold == threshold


def test_train_frozen_multivariate_drift_gate():
    rng = np.random.default_rng(5)
    training = rng.normal(size=(300, 2))
    gate = TrainingDriftGate(reference_size=40, resamples=30, quantile=.95).fit(training)
    stable = gate.assess(rng.normal(size=(50, 2)))
    shifted = gate.assess(rng.normal(loc=4, size=(50, 2)))
    assert shifted["drift"]
    assert shifted["mmd2"] > stable["mmd2"]
    threshold = gate.threshold
    gate.assess(rng.normal(loc=10, size=(50, 2)))
    assert gate.threshold == threshold


def test_existing_decisions_require_matching_asof_snapshots():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE decisions (decision_id VARCHAR, isin VARCHAR, action VARCHAR, created_at TIMESTAMPTZ)")
    con.execute("INSERT INTO decisions VALUES ('d1', 'TEST', 'BUY', '2020-01-02T10:00:00+00:00')")
    source = synthetic_history("momentum", n=90).iloc[:1]
    state = source[["state_asof", *FEATURES]].copy()
    state["decision_id"] = "d1"
    state["state_asof"] = pd.Timestamp("2020-01-02T09:59:00Z")
    joined = from_existing_decisions(con, state)
    assert len(joined) == 1 and joined.action.iloc[0] == "BUY"
    state["state_asof"] = pd.Timestamp("2020-01-02T10:01:00Z")
    with pytest.raises(ValueError, match="future"):
        from_existing_decisions(con, state)


def test_simultaneous_instruments_stay_in_same_chronological_fold():
    original = synthetic_history("momentum", n=180)
    other = original.copy()
    other["instrument"] = "SECOND"
    data = pd.concat([original, other], ignore_index=True)
    result = infer_policy(data, models=("tree",))
    assert result.split["train_end"] < result.split["validation_start"]
    assert result.split["validation_end"] < result.split["holdout_start"]


def test_workstation_compare_and_rule_inspection(tmp_path, capsys):
    import json

    data = synthetic_history("momentum", n=180)
    result = infer_policy(data, models=("tree",))
    rules = extract_rules(result, data)
    report = strategy_report(result, rules, regime_report(data))
    report_path, rules_path = tmp_path / "report.json", tmp_path / "rules.json"
    report_path.write_text(json.dumps(report, default=str), encoding="utf-8")
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    assert compare_reports([report_path])[0]["health"] == "requires_market_validation"
    assert workstation_main(["rules", str(rules_path)]) == 0
    assert "validation_fidelity" in capsys.readouterr().out
    assert workstation_main(["compare", str(report_path)]) == 0
    assert "Never select" in capsys.readouterr().out


def test_workstation_ensemble_paper_cli(tmp_path, capsys):
    n = 90
    dates = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    panel = pd.DataFrame({"decision_ts": dates, "estimate_asof": dates - pd.Timedelta(minutes=1),
                          "observed_at": dates + pd.Timedelta(days=2),
                          "regime": np.arange(n) % 2, "realized": np.sin(np.arange(n)) / 100,
                          "a": np.zeros(n), "b": np.ones(n) / 100,
                          "cost_expected": .001, "cost_realized": .001,
                          "benchmark_expected": 0., "benchmark_realized": 0., "turnover": 1.})
    path = tmp_path / "forecasts.csv"
    panel.to_csv(path, index=False)
    assert workstation_main(["ensemble", str(path), "--strategies", "a", "b",
                             "--validated", "a", "b", "--min-train", "20", "--paper"]) == 0
    assert "descriptive_paper_only" in capsys.readouterr().out


def test_rebalance_context_idempotent_but_immutable():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text(encoding="utf-8"))
    kwargs = {"run_id": "paper-1", "signal_session": pd.Timestamp("2020-01-01").date(),
              "execution_session": pd.Timestamp("2020-01-02").date(),
              "candidates": [{"lineage": "TEST", "rank": 1, "score": .3, "filtered": False}],
              "constraints": {"cash": 100},
              "portfolio": {"known_before_execution": {"held": []},
                            "realized_after_execution": {"held": []}}}
    first = record_rebalance_context(con, **kwargs)
    assert record_rebalance_context(con, **kwargs) == first
    with pytest.raises(ValueError, match="immutable"):
        record_rebalance_context(con, **{**kwargs, "constraints": {"cash": 0}})
