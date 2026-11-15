from __future__ import annotations

from datetime import date

import pytest

from financial_brain.forward_edge.beta_candidate import (
    BetaCandidateError,
    BetaCandidatePerformance,
    BetaCandidatePerformanceRegistry,
    beta_neutral_return,
    fit_beta_neutral_candidate,
)


def _rows(count: int = 40, *, split: str = "DEVELOPMENT", constant_market: bool = False):
    rows = []
    for i in range(count):
        market = 0.0 if constant_market else (i - count / 2) * 0.001
        rows.append({"split": split, "exchange_date": "2026-10-01",
                     "strategy_return": 0.0005 + 1.2 * market, "benchmark_return": market,
                     "decision_sha256": f"{i + 1:064x}"})
    return rows


def _candidate(*, rows=None, base="a" * 64):
    return fit_beta_neutral_candidate(
        base_strategy_sha256=base, benchmark_id="NIFTY50",
        observations=_rows() if rows is None else rows,
        validation_start=date(2026, 12, 1), calibration_protocol_sha256="b" * 64,
    )


def test_beta_candidate_is_development_fit_with_separate_frozen_strategy_identity():
    candidate = _candidate()
    assert candidate.beta == pytest.approx(1.2)
    assert candidate.strategy_sha256 != candidate.base_strategy_sha256
    assert beta_neutral_return(candidate, strategy_return=0.0125, benchmark_return=0.01) == pytest.approx(0.0005)
    assert _candidate(base="c" * 64).strategy_sha256 != candidate.strategy_sha256


def test_beta_candidate_rejects_validation_rows_duplicate_identity_and_zero_market_variance():
    with pytest.raises(BetaCandidateError, match="DEVELOPMENT"):
        _candidate(rows=_rows(split="VALIDATION"))
    duplicate = _rows()
    duplicate[1] = {**duplicate[1], "decision_sha256": duplicate[0]["decision_sha256"]}
    with pytest.raises(BetaCandidateError, match="unique SHA-256"):
        _candidate(rows=duplicate)
    with pytest.raises(BetaCandidateError, match="insufficient variance"):
        _candidate(rows=_rows(constant_market=True))


def test_candidate_performance_records_are_validation_only_and_not_coalesced(tmp_path):
    candidate = _candidate()
    registry = BetaCandidatePerformanceRegistry(tmp_path / "beta-performance.jsonl")
    registry.register(candidate, registered_on=date(2026, 11, 1))
    performance = BetaCandidatePerformance(
        candidate_strategy_sha256=candidate.strategy_sha256, candidate_sha256=candidate.sha256,
        benchmark_id="NIFTY50", session_date="2026-12-01", split="VALIDATION",
        gross_return=0.01, benchmark_return=0.004, fees=0.001, spread=0.0005,
        impact=0.0005, receipt_sha256="d" * 64,
    )
    assert performance.net_alpha == pytest.approx(0.004)
    receipt_sha = registry.record(candidate, performance)
    assert registry.record(candidate, performance) == receipt_sha
    assert registry.performances(candidate.strategy_sha256) == (performance,)
    assert registry.performances(candidate.base_strategy_sha256) == ()
    with pytest.raises(BetaCandidateError, match="VALIDATION"):
        BetaCandidatePerformance(
            candidate_strategy_sha256=candidate.strategy_sha256, candidate_sha256=candidate.sha256,
            benchmark_id="NIFTY50", session_date="2026-12-02", split="DEVELOPMENT",
            gross_return=0.01, benchmark_return=0.004, fees=0.001, spread=0.0005,
            impact=0.0005, receipt_sha256="e" * 64,
        )


def test_performance_record_must_match_candidate_and_follow_validation_start(tmp_path):
    candidate = _candidate()
    registry = BetaCandidatePerformanceRegistry(tmp_path / "beta-performance.jsonl")
    registry.register(candidate, registered_on=date(2026, 11, 1))
    with pytest.raises(BetaCandidateError, match="match candidate"):
        registry.record(candidate, BetaCandidatePerformance(
            candidate_strategy_sha256="f" * 64, candidate_sha256=candidate.sha256,
            benchmark_id="NIFTY50", session_date="2026-12-01", split="VALIDATION",
            gross_return=0.01, benchmark_return=0.004, fees=0.001, spread=0.0005,
            impact=0.0005, receipt_sha256="d" * 64,
        ))
    before = BetaCandidatePerformance(
        candidate_strategy_sha256=candidate.strategy_sha256, candidate_sha256=candidate.sha256,
        benchmark_id="NIFTY50", session_date="2026-11-30", split="VALIDATION",
        gross_return=0.01, benchmark_return=0.004, fees=0.001, spread=0.0005,
        impact=0.0005, receipt_sha256="e" * 64,
    )
    with pytest.raises(BetaCandidateError, match="predates"):
        registry.record(candidate, before)


def test_candidate_must_be_registered_before_validation_and_before_performance(tmp_path):
    candidate = _candidate()
    registry = BetaCandidatePerformanceRegistry(tmp_path / "beta-performance.jsonl")
    with pytest.raises(BetaCandidateError, match="registered before validation"):
        registry.register(candidate, registered_on=date(2026, 12, 1))
    performance = BetaCandidatePerformance(
        candidate_strategy_sha256=candidate.strategy_sha256, candidate_sha256=candidate.sha256,
        benchmark_id="NIFTY50", session_date="2026-12-01", split="VALIDATION",
        gross_return=0.01, benchmark_return=0.004, fees=0.001, spread=0.0005,
        impact=0.0005, receipt_sha256="d" * 64,
    )
    with pytest.raises(BetaCandidateError, match="registered"):
        registry.record(candidate, performance)
