from dataclasses import replace
from datetime import date

import json
import pytest

from financial_brain.forward_edge.pareto_selection import (
    ParetoSelectionError,
    TrainingCandidate,
    build_pareto_report,
    write_pareto_report,
)


def _candidate(strategy_id: str, code: str, *, net: float, drawdown: float,
               turnover: float, capacity: float, complexity: float) -> TrainingCandidate:
    return TrainingCandidate(strategy_id, code * 64, "TRAIN", "f" * 64, date(2025, 12, 31),
                             net, drawdown, turnover, capacity, complexity)


def _candidates() -> tuple[TrainingCandidate, ...]:
    return (
        _candidate("A", "a", net=0.10, drawdown=0.10, turnover=0.10, capacity=100, complexity=1),
        _candidate("B", "b", net=0.05, drawdown=0.15, turnover=0.15, capacity=80, complexity=2),
        _candidate("C", "c", net=0.15, drawdown=0.20, turnover=0.30, capacity=150, complexity=5),
    )


def test_training_pareto_report_archives_selected_dominated_and_alternative_candidates(tmp_path):
    report = build_pareto_report(_candidates(), selected_strategy_id="A",
                                 selection_reason="lower drawdown and turnover at acceptable training net return")
    rows = {row["strategy_id"]: row for row in report.candidates}
    assert rows["A"]["pareto_frontier"] is True
    assert rows["A"]["decision_reason"].startswith("SELECTED:")
    assert rows["B"]["decision_reason"] == "DOMINATED_BY:A"
    assert rows["C"]["decision_reason"].startswith("PARETO_ALTERNATIVE_NOT_SELECTED:")
    assert report.canonical()["scope"] == "TRAIN_ONLY"
    digest = write_pareto_report(tmp_path / "pareto.json", report)
    assert digest == report.sha256
    assert json.loads((tmp_path / "pareto.json").read_text())["candidates"] == list(report.candidates)
    assert write_pareto_report(tmp_path / "pareto.json", report) == digest


def test_training_pareto_refuses_validation_data_changed_training_identity_and_dominated_choice():
    rows = _candidates()
    with pytest.raises(ParetoSelectionError, match="TRAIN-only"):
        build_pareto_report((replace(rows[0], data_split="VALIDATION"),), selected_strategy_id="A",
                            selection_reason="bad")
    with pytest.raises(ParetoSelectionError, match="same frozen training data"):
        build_pareto_report((rows[0], replace(rows[2], training_data_sha256="e" * 64)),
                            selected_strategy_id="A", selection_reason="bad")
    with pytest.raises(ParetoSelectionError, match="is dominated"):
        build_pareto_report(rows, selected_strategy_id="B", selection_reason="cannot select dominated")


def test_training_pareto_refuses_rewriting_an_existing_decision_archive(tmp_path):
    path = tmp_path / "pareto.json"
    first = build_pareto_report(_candidates(), selected_strategy_id="A", selection_reason="reason A")
    second = build_pareto_report(_candidates(), selected_strategy_id="C", selection_reason="reason C")
    write_pareto_report(path, first)
    with pytest.raises(ParetoSelectionError, match="immutable"):
        write_pareto_report(path, second)
