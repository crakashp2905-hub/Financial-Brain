"""Training-only Pareto analysis with explicit, append-only candidate dispositions (PE-0073)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from financial_brain.forward_edge.durable import DurableJournal, JournalCorrupt


class ParetoTradeoffError(ValueError):
    pass


METRICS = ("net_performance", "max_drawdown", "turnover", "capacity_notional", "complexity")
MAXIMIZE = frozenset({"net_performance", "capacity_notional"})


@dataclass(frozen=True)
class CandidateMetrics:
    candidate_id: str
    sample_role: str
    training_data_sha256: str
    metrics: tuple[tuple[str, float], ...]

    def canonical(self) -> dict:
        return {"candidate_id": self.candidate_id, "sample_role": self.sample_role,
                "training_data_sha256": self.training_data_sha256,
                "metrics": {name: value for name, value in self.metrics}}


def pareto_tradeoff(*, candidates: Sequence[CandidateMetrics], training_data_sha256: str,
                    protocol_sha256: str, selected_candidate_id: str,
                    selection_rule: str, rejection_reasons: Mapping[str, str]) -> dict:
    """Return a content-addressed multi-objective comparison with no holdout access.

    The caller must name the already-declared selection rule and give an explicit reason for
    every non-selected candidate. Metrics are net performance, drawdown, turnover, capacity,
    and strategy complexity; the direction of each objective is fixed here.
    """
    def digest(value: str) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    if not candidates or not digest(training_data_sha256) or not digest(protocol_sha256):
        raise ParetoTradeoffError("candidates and frozen training-data/protocol hashes are required")
    ids = [row.candidate_id for row in candidates]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        raise ParetoTradeoffError("candidate ids must be nonempty and unique")
    if selected_candidate_id not in ids or not isinstance(selection_rule, str) or not selection_rule.strip():
        raise ParetoTradeoffError("selected candidate and predeclared selection rule are required")
    if set(rejection_reasons) != set(ids) - {selected_candidate_id} or any(
            not isinstance(reason, str) or not reason.strip() for reason in rejection_reasons.values()):
        raise ParetoTradeoffError("every non-selected candidate needs one explicit rejection reason")
    canonical: dict[str, dict] = {}
    for row in candidates:
        metric_values = dict(row.metrics)
        if (row.sample_role != "TRAINING_ONLY" or row.training_data_sha256 != training_data_sha256
                or set(metric_values) != set(METRICS)
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) for value in metric_values.values())
                or not 0 <= metric_values["max_drawdown"] <= 1
                or metric_values["turnover"] < 0 or metric_values["capacity_notional"] < 0
                or metric_values["complexity"] < 0):
            raise ParetoTradeoffError(f"candidate {row.candidate_id} is not valid frozen training-only evidence")
        canonical[row.candidate_id] = row.canonical()

    def dominates(left: dict, right: dict) -> bool:
        lvals, rvals = left["metrics"], right["metrics"]
        no_worse = all((lvals[key] >= rvals[key] if key in MAXIMIZE else lvals[key] <= rvals[key])
                       for key in METRICS)
        strictly = any((lvals[key] > rvals[key] if key in MAXIMIZE else lvals[key] < rvals[key])
                       for key in METRICS)
        return no_worse and strictly

    frontier = {candidate_id for candidate_id, row in canonical.items()
                if not any(dominates(other, row) for other_id, other in canonical.items()
                           if other_id != candidate_id)}
    if selected_candidate_id not in frontier:
        raise ParetoTradeoffError("selected candidate is dominated and cannot be chosen from the Pareto frontier")
    rows = []
    for candidate_id in ids:
        dominators = sorted(other_id for other_id, other in canonical.items()
                            if other_id != candidate_id and dominates(other, canonical[candidate_id]))
        disposition = "SELECTED" if candidate_id == selected_candidate_id else (
            "PARETO_ALTERNATIVE" if candidate_id in frontier else "DOMINATED")
        reason = selection_rule.strip() if candidate_id == selected_candidate_id else rejection_reasons[candidate_id].strip()
        rows.append({"candidate_id": candidate_id, "disposition": disposition, "reason": reason,
                     "dominated_by": dominators, "metrics": canonical[candidate_id]["metrics"]})
    body = {"schema_version": 1, "training_data_sha256": training_data_sha256,
            "protocol_sha256": protocol_sha256, "selection_rule": selection_rule.strip(),
            "pareto_frontier": sorted(frontier), "candidates": rows}
    body["report_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


class ParetoArchive:
    """Hash-chained local archive; it is append-only evidence, not independent attestation."""

    def __init__(self, path: Path) -> None:
        try:
            self.journal = DurableJournal(Path(path))
        except JournalCorrupt as exc:
            raise ParetoTradeoffError("Pareto archive journal chain is corrupt") from exc
        self.records = self.journal.records()
        self._verify()

    def _verify(self) -> None:
        previous = "0" * 64
        for row in self.records:
            report = row.get("report")
            if row.get("t") != "PARETO_REPORT" or not isinstance(report, dict) or row.get("previous_sha256") != previous:
                raise ParetoTradeoffError("Pareto archive chain is invalid")
            report_body = {key: value for key, value in report.items() if key != "report_sha256"}
            report_digest = hashlib.sha256(json.dumps(report_body, sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()
            if report_digest != report.get("report_sha256"):
                raise ParetoTradeoffError("Pareto report digest does not match its contents")
            digest = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if digest != row.get("record_sha256"):
                raise ParetoTradeoffError("Pareto archive report hash does not match")
            previous = digest

    def append(self, report: dict) -> None:
        if any(row["report"].get("report_sha256") == report.get("report_sha256") for row in self.records):
            raise ParetoTradeoffError("Pareto report is already archived")
        previous = self.records[-1]["record_sha256"] if self.records else "0" * 64
        digest = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = {"t": "PARETO_REPORT", "previous_sha256": previous,
               "record_sha256": digest, "report": report}
        self.journal.append(row)
        self.records = self.journal.records()
        self._verify()
