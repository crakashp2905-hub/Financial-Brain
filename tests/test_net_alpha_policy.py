from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.net_alpha_policy import (
    NetAlphaPolicyError,
    NetAlphaThresholdRegistry,
    calibrate_threshold_policy,
)


STRATEGY = "a" * 64
PROTOCOL = "b" * 64


def _observations(count=40):
    return [
        {"exchange_date": "2026-10-20", "split": "DEVELOPMENT",
         "forecast_net_alpha": 0.01 + i / 1000,
         "realized_net_alpha": 0.005 + (i % 5) / 1000,
         "decision_sha256": f"{i + 1:064x}"}
        for i in range(count)
    ]


def _policy():
    return calibrate_threshold_policy(
        strategy_sha256=STRATEGY, cohort_id="momentum-v1", observations=_observations(),
        candidate_thresholds=(0.0, 0.01), validation_start=date(2026, 11, 14),
        frozen_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
        calibration_protocol_sha256=PROTOCOL, min_observations=20,
    )


def test_threshold_calibration_uses_development_slice_and_freezes_before_validation():
    policy = _policy()
    assert policy.development_end < policy.validation_start
    assert policy.frozen_at_utc < "2026-11-14"
    assert policy.observation_count >= 20
    assert len(policy.development_data_sha256) == len(policy.sha256) == 64


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(split="VALIDATION"),
    lambda row: row.update(exchange_date="2026-11-14"),
    lambda row: row.update(decision_sha256="bad"),
])
def test_threshold_calibration_rejects_holdout_or_invalid_development_rows(mutation):
    rows = _observations()
    mutation(rows[0])
    with pytest.raises(NetAlphaPolicyError):
        calibrate_threshold_policy(
            strategy_sha256=STRATEGY, cohort_id="momentum-v1", observations=rows,
            candidate_thresholds=(0.0, 0.01), validation_start=date(2026, 11, 14),
            frozen_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
            calibration_protocol_sha256=PROTOCOL, min_observations=20,
        )


def test_frozen_threshold_cannot_be_changed_after_forward_drawdown(tmp_path):
    registry = NetAlphaThresholdRegistry(tmp_path / "thresholds.jsonl")
    policy = _policy()
    registry.freeze(policy)
    registry.record_forward_drawdown(
        strategy_sha256=STRATEGY, cohort_id="momentum-v1", policy_sha256=policy.sha256,
        session_id="forward-2026-11-13", drawdown=0.04,
    )
    assert registry.resolve(STRATEGY, "momentum-v1") == policy
    with pytest.raises(NetAlphaPolicyError, match="cannot be changed"):
        registry.freeze(replace(policy, minimum_net_alpha=policy.minimum_net_alpha + 0.001))


def test_runner_checks_registry_policy_against_frozen_configuration(tmp_path):
    from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError

    policy = _policy()
    registry = NetAlphaThresholdRegistry(tmp_path / "thresholds.jsonl")
    registry.freeze(policy)
    config = ForwardRunConfig(
        session_id="policy-bound", exchange_date="2026-11-16", cohort_id="momentum-v1",
        strategy_sha256=STRATEGY, risk_policy_sha256="c" * 64, constraints_sha256="d" * 64,
        ledger_id="ledger", benchmark_id="NIFTY50", instruments=("NSE:INFY",), capital_inr="10000",
        authority_strategy_id="momentum", authority_account_id="paper-account",
        minimum_forecast_net_alpha=policy.minimum_net_alpha, net_alpha_policy_sha256=policy.sha256,
    )
    runner = ForwardSessionRunner(tmp_path / "session.jsonl", net_alpha_threshold_registry=registry)
    runner.begin(config)
    changed = replace(config, session_id="policy-tampered",
                      minimum_forecast_net_alpha=policy.minimum_net_alpha + 0.01)
    with pytest.raises(RunnerError, match="does not match its frozen cohort policy"):
        runner.begin(changed)


def test_same_forward_cohort_cannot_change_threshold_between_sessions(tmp_path):
    from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError

    base = ForwardRunConfig(
        session_id="threshold-day-1", exchange_date="2026-11-12", cohort_id="momentum-v1",
        strategy_sha256=STRATEGY, risk_policy_sha256="c" * 64, constraints_sha256="d" * 64,
        ledger_id="ledger", benchmark_id="NIFTY50", instruments=("NSE:INFY",), capital_inr="10000",
        authority_strategy_id="momentum", authority_account_id="paper-account",
        minimum_forecast_net_alpha=0.01,
    )
    first = ForwardSessionRunner(tmp_path / "runner.jsonl")
    first.begin(base)
    first.mark_incomplete(base.session_id, "synthetic forward loss for threshold-freeze test")
    changed = replace(base, session_id="threshold-day-2", exchange_date="2026-11-13",
                      minimum_forecast_net_alpha=0.02)
    second = ForwardSessionRunner(tmp_path / "runner.jsonl")
    with pytest.raises(RunnerError, match="hurdle is frozen"):
        second.begin(changed)
