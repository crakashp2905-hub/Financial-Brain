import json

import pytest

from financial_brain.forward_edge.pareto_tradeoff import (
    CandidateMetrics,
    ParetoArchive,
    ParetoTradeoffError,
    pareto_tradeoff,
)


TRAINING_SHA = "a" * 64
PROTOCOL_SHA = "b" * 64


def _candidate(candidate_id, values, *, role="TRAINING_ONLY", digest=TRAINING_SHA):
    return CandidateMetrics(candidate_id, role, digest,
                            tuple(zip(("net_performance", "max_drawdown", "turnover",
                                       "capacity_notional", "complexity"), values)))


def _report():
    return pareto_tradeoff(
        candidates=[_candidate("balanced", (0.12, 0.10, 0.5, 1_000_000, 5)),
                    _candidate("low-risk", (0.10, 0.08, 0.4, 800_000, 4)),
                    _candidate("dominated", (0.08, 0.20, 0.7, 500_000, 6))],
        training_data_sha256=TRAINING_SHA, protocol_sha256=PROTOCOL_SHA,
        selected_candidate_id="balanced", selection_rule="Predeclared utility with drawdown cap",
        rejection_reasons={"low-risk": "Lower return under the frozen drawdown-capped rule",
                           "dominated": "Dominated by balanced across all five objectives"})


def test_training_only_pareto_report_archives_selected_and_every_rejection_reason():
    report = _report()
    rows = {row["candidate_id"]: row for row in report["candidates"]}
    assert report["pareto_frontier"] == ["balanced", "low-risk"]
    assert rows["balanced"]["disposition"] == "SELECTED"
    assert rows["low-risk"]["disposition"] == "PARETO_ALTERNATIVE"
    assert rows["dominated"]["disposition"] == "DOMINATED"
    assert rows["dominated"]["dominated_by"] == ["balanced", "low-risk"]
    assert all(row["reason"] for row in rows.values())
    assert len(report["report_sha256"]) == 64


def test_pareto_report_rejects_holdout_rows_dominated_selection_and_missing_reasons():
    with pytest.raises(ParetoTradeoffError, match="training-only"):
        pareto_tradeoff(candidates=[_candidate("x", (0.1, 0.1, 0.1, 100, 1), role="HOLDOUT")],
                        training_data_sha256=TRAINING_SHA, protocol_sha256=PROTOCOL_SHA,
                        selected_candidate_id="x", selection_rule="fixed", rejection_reasons={})
    with pytest.raises(ParetoTradeoffError, match="rejection reason"):
        pareto_tradeoff(candidates=[_candidate("x", (0.1, 0.1, 0.1, 100, 1)),
                                    _candidate("y", (0.09, 0.2, 0.2, 50, 2))],
                        training_data_sha256=TRAINING_SHA, protocol_sha256=PROTOCOL_SHA,
                        selected_candidate_id="x", selection_rule="fixed", rejection_reasons={})
    with pytest.raises(ParetoTradeoffError, match="dominated"):
        pareto_tradeoff(candidates=[_candidate("x", (0.1, 0.1, 0.1, 100, 1)),
                                    _candidate("y", (0.2, 0.05, 0.05, 200, 0.5))],
                        training_data_sha256=TRAINING_SHA, protocol_sha256=PROTOCOL_SHA,
                        selected_candidate_id="x", selection_rule="fixed",
                        rejection_reasons={"y": "Rejected"})


def test_pareto_archive_is_append_only_and_detects_report_tampering(tmp_path):
    path = tmp_path / "pareto.jsonl"
    archive = ParetoArchive(path)
    archive.append(_report())
    with pytest.raises(ParetoTradeoffError, match="already archived"):
        archive.append(_report())
    assert len(ParetoArchive(path).records) == 1

    row = json.loads(path.read_text(encoding="utf-8"))
    row["rec"]["report"]["candidates"][0]["reason"] = "retrospective rewrite"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ParetoTradeoffError, match="corrupt"):
        ParetoArchive(path)
