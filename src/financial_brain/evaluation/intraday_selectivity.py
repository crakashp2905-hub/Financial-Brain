"""Training-only intraday selectivity with independent same-time controls (PE-0161).

Threshold selection consumes training rows only. A selected policy contains no
forward-session schedule and can be applied to scores without inspecting outcomes.
This is research evaluation plumbing, not a paper or live strategy adapter.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re


class SelectivityError(ValueError):
    """Training/control evidence is malformed or leaks beyond the frozen window."""


def _finite(value: object, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SelectivityError(f"{name} must be finite") from exc
    if not math.isfinite(out):
        raise SelectivityError(f"{name} must be finite")
    return out


def _date(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SelectivityError(f"{name} must be a canonical ISO exchange date")
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise SelectivityError(f"{name} must be a canonical ISO exchange date") from exc
    if normalized != value:
        raise SelectivityError(f"{name} must be a canonical ISO exchange date")
    return normalized


def _rows(rows: object, *, kind: str, start: str, end: str) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise SelectivityError(f"{kind} training rows must be a nonempty list")
    out, ids = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise SelectivityError(f"{kind} rows must be objects")
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in ids:
            raise SelectivityError(f"{kind} event IDs must be nonempty and unique")
        ids.add(event_id)
        if row.get("partition") != "TRAIN":
            raise SelectivityError("selectivity fitting accepts TRAIN partition rows only")
        session_date = _date(row.get("session_date"), f"{kind} session_date")
        if not start <= session_date <= end:
            raise SelectivityError("row falls outside the frozen training window")
        for field in ("time_bucket", "stratum", "symbol"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise SelectivityError(f"{kind} row needs {field}")
        gross = _finite(row.get("gross_return_bps"), f"{kind} gross_return_bps")
        costs = _finite(row.get("round_trip_cost_bps"), f"{kind} round_trip_cost_bps")
        if costs < 0:
            raise SelectivityError("round-trip costs cannot be negative")
        normalized = dict(row)
        normalized["gross_return_bps"] = gross
        normalized["round_trip_cost_bps"] = costs
        normalized["net_return_bps"] = gross - costs
        if kind == "candidate":
            normalized["score"] = _finite(row.get("score"), "candidate score")
        else:
            normalized["covariate_distance"] = _finite(row.get("covariate_distance"),
                                                        "control covariate_distance")
            if normalized["covariate_distance"] < 0:
                raise SelectivityError("control covariate distance cannot be negative")
        out.append(normalized)
    return out


def _digest(rows: list[dict]) -> str:
    body = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelectivityPolicy:
    policy_id: str
    threshold: float
    training_start: str
    training_end: str
    training_candidate_sha256: str
    training_control_sha256: str
    candidate_source_sha256: str
    control_source_sha256: str
    matched_event_count: int
    mean_net_excess_bps: float
    selected_event_count: int
    sessions_with_selected_events: int
    evidence_class: str = "TRAINING_ONLY_EXPLORATORY"
    forward_schedule_sha256: None = None

    @property
    def sha256(self) -> str:
        body = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def select(self, score: object) -> bool:
        return _finite(score, "score") >= self.threshold


def fit_selectivity_policy(*, candidates: list[dict], controls: list[dict],
                           thresholds: list[float], training_start: str, training_end: str,
                           candidate_source_sha256: str, control_source_sha256: str) -> SelectivityPolicy:
    """Choose a threshold on a frozen training partition against distinct same-time controls.

    Candidate and control rows are supplied as separate datasets. A control can
    match only one event and must share session date, time bucket and stratum;
    its symbol must differ from the candidate symbol. Controls are chosen by
    lowest precomputed covariate distance with a deterministic event-ID tie break.
    The eventual forward session schedule is deliberately not an input.
    """
    start, end = _date(training_start, "training_start"), _date(training_end, "training_end")
    if start > end:
        raise SelectivityError("training window is reversed")
    for value, name in ((candidate_source_sha256, "candidate_source_sha256"),
                        (control_source_sha256, "control_source_sha256")):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise SelectivityError(f"{name} must be a SHA-256")
    if candidate_source_sha256 == control_source_sha256:
        raise SelectivityError("candidate and independent-control source manifests must differ")
    candidate_rows = _rows(candidates, kind="candidate", start=start, end=end)
    control_rows = _rows(controls, kind="control", start=start, end=end)
    if {row["event_id"] for row in candidate_rows} & {row["event_id"] for row in control_rows}:
        raise SelectivityError("candidate and control event IDs must be independent")
    if not isinstance(thresholds, list) or not thresholds:
        raise SelectivityError("a frozen threshold grid is required")
    grid = sorted({_finite(value, "threshold") for value in thresholds})
    by_match: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for control in control_rows:
        by_match[(control["session_date"], control["time_bucket"], control["stratum"])].append(control)
    scores = []
    for threshold in grid:
        used: set[str] = set()
        excess = []
        selected_count = 0
        selected_sessions: set[str] = set()
        for candidate in sorted(candidate_rows, key=lambda row: (row["session_date"], row["event_id"])):
            if candidate["score"] < threshold:
                continue
            selected_count += 1
            selected_sessions.add(candidate["session_date"])
            eligible = [control for control in by_match.get(
                (candidate["session_date"], candidate["time_bucket"], candidate["stratum"]), [])
                if control["symbol"] != candidate["symbol"] and control["event_id"] not in used]
            if not eligible:
                continue
            control = min(eligible, key=lambda row: (row["covariate_distance"], row["event_id"]))
            used.add(control["event_id"])
            excess.append(candidate["net_return_bps"] - control["net_return_bps"])
        if not excess or len(excess) != selected_count:
            continue
        scores.append({
            "threshold": threshold,
            "mean_net_excess_bps": sum(excess) / len(excess),
            "matched_event_count": len(excess), "selected_event_count": selected_count,
            "sessions_with_selected_events": len(selected_sessions),
        })
    if not scores:
        raise SelectivityError("no threshold has a complete set of independent same-time controls")
    # Deterministic training-only choice: maximize matched net excess; ties prefer fewer selected events,
    # then a stricter threshold. This is exploratory and does not select on validation/forward outcomes.
    best = max(scores, key=lambda row: (row["mean_net_excess_bps"], -row["selected_event_count"], row["threshold"]))
    policy_body = {
        "training_candidate_sha256": _digest(candidate_rows),
        "training_control_sha256": _digest(control_rows),
        "threshold": best["threshold"], "training_start": start, "training_end": end,
        "candidate_source_sha256": candidate_source_sha256,
        "control_source_sha256": control_source_sha256,
        "selection_rule": "max_training_matched_net_excess_then_lowest_selected_count_then_strictest_threshold",
    }
    policy_id = hashlib.sha256(json.dumps(policy_body, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
    return SelectivityPolicy(
        policy_id=policy_id, threshold=best["threshold"], training_start=start, training_end=end,
        training_candidate_sha256=policy_body["training_candidate_sha256"],
        training_control_sha256=policy_body["training_control_sha256"],
        candidate_source_sha256=candidate_source_sha256,
        control_source_sha256=control_source_sha256,
        matched_event_count=best["matched_event_count"],
        mean_net_excess_bps=best["mean_net_excess_bps"],
        selected_event_count=best["selected_event_count"],
        sessions_with_selected_events=best["sessions_with_selected_events"],
    )
