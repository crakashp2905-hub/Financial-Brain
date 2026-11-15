"""Development-only calibration and immutable freezing of net-alpha entry hurdles."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Iterable

from .durable import DurableJournal


class NetAlphaPolicyError(ValueError):
    """A threshold policy is invalid, out of sample, or already frozen differently."""


@dataclass(frozen=True)
class NetAlphaThresholdPolicy:
    strategy_sha256: str
    cohort_id: str
    minimum_net_alpha: float
    development_data_sha256: str
    calibration_protocol_sha256: str
    development_start: str
    development_end: str
    validation_start: str
    observation_count: int
    selected_lower_confidence_bound: float
    frozen_at_utc: str

    def __post_init__(self) -> None:
        for name in ("strategy_sha256", "development_data_sha256", "calibration_protocol_sha256"):
            value = getattr(self, name)
            if (not isinstance(value, str) or len(value) != 64
                    or any(ch not in "0123456789abcdef" for ch in value)):
                raise NetAlphaPolicyError(f"{name} must be a SHA-256 digest")
        if (not isinstance(self.cohort_id, str) or not self.cohort_id.strip()
                or isinstance(self.minimum_net_alpha, bool)
                or not math.isfinite(self.minimum_net_alpha) or self.minimum_net_alpha < 0):
            raise NetAlphaPolicyError("cohort and finite non-negative threshold are required")
        if self.observation_count < 2 or not math.isfinite(self.selected_lower_confidence_bound):
            raise NetAlphaPolicyError("calibration requires at least two observations and a finite score")
        try:
            development_start = date.fromisoformat(self.development_start)
            development_end = date.fromisoformat(self.development_end)
            validation_start = date.fromisoformat(self.validation_start)
            frozen_at = datetime.fromisoformat(self.frozen_at_utc)
        except ValueError as exc:
            raise NetAlphaPolicyError("calibration dates and freeze timestamp must be canonical") from exc
        if development_start > development_end or development_end >= validation_start:
            raise NetAlphaPolicyError("development data must end before validation starts")
        if frozen_at.tzinfo is None or frozen_at.utcoffset() is None or frozen_at.date() >= validation_start:
            raise NetAlphaPolicyError("threshold must be frozen with an aware timestamp before validation")

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def sha256(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def calibrate_threshold_policy(*, strategy_sha256: str, cohort_id: str,
                               observations: Iterable[dict], candidate_thresholds: Iterable[float],
                               validation_start: date, frozen_at: datetime,
                               calibration_protocol_sha256: str, min_observations: int = 30
                               ) -> NetAlphaThresholdPolicy:
    """Choose a hurdle on development observations by maximum 95% lower bound of realized net alpha.

    The threshold grid and minimum sample size are supplied before evaluation. Any validation/test
    row, future development row, duplicate decision, or non-finite input invalidates the calibration.
    This is a development selection procedure; confirmatory validation must use untouched sessions.
    """
    rows = list(observations)
    if min_observations < 2 or len(rows) < min_observations:
        raise NetAlphaPolicyError("development calibration has too few observations")
    normalized: list[dict[str, object]] = []
    development_dates: list[str] = []
    forecast_values: list[float] = []
    realized_values: list[float] = []
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise NetAlphaPolicyError("threshold calibration may consume DEVELOPMENT rows only")
        try:
            observed_on = date.fromisoformat(row["exchange_date"])
            forecast = float(row["forecast_net_alpha"])
            realized = float(row["realized_net_alpha"])
            decision_sha = row["decision_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise NetAlphaPolicyError("development observation is missing typed forecast/outcome provenance") from exc
        if observed_on >= validation_start or not math.isfinite(forecast) or not math.isfinite(realized):
            raise NetAlphaPolicyError("development observations must be finite and strictly precede validation")
        if (not isinstance(decision_sha, str) or len(decision_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in decision_sha) or decision_sha in ids):
            raise NetAlphaPolicyError("decision identities must be unique SHA-256 digests")
        ids.add(decision_sha)
        development_dates.append(observed_on.isoformat())
        forecast_values.append(forecast)
        realized_values.append(realized)
        normalized.append({"exchange_date": observed_on.isoformat(), "split": "DEVELOPMENT",
                           "forecast_net_alpha": forecast, "realized_net_alpha": realized,
                           "decision_sha256": decision_sha})
    thresholds = sorted(set(float(value) for value in candidate_thresholds))
    if not thresholds or any(not math.isfinite(value) or value < 0 for value in thresholds):
        raise NetAlphaPolicyError("predeclared threshold candidates must be finite, non-negative, and nonempty")
    scored: list[tuple[float, float, int]] = []
    for threshold in thresholds:
        eligible = [realized for forecast, realized in zip(forecast_values, realized_values)
                    if forecast > threshold]
        if len(eligible) < min_observations:
            continue
        lower_bound = mean(eligible) - 1.96 * stdev(eligible) / math.sqrt(len(eligible))
        scored.append((lower_bound, threshold, len(eligible)))
    if not scored:
        raise NetAlphaPolicyError("no predeclared threshold has the required development sample")
    score, threshold, selected_count = max(scored, key=lambda item: (item[0], item[1]))
    normalized.sort(key=lambda row: (row["exchange_date"], row["decision_sha256"]))
    data_body = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    data_sha = hashlib.sha256(data_body.encode("utf-8")).hexdigest()
    policy = NetAlphaThresholdPolicy(
        strategy_sha256=strategy_sha256, cohort_id=cohort_id, minimum_net_alpha=threshold,
        development_data_sha256=data_sha, calibration_protocol_sha256=calibration_protocol_sha256,
        development_start=min(development_dates),
        development_end=max(development_dates),
        validation_start=validation_start.isoformat(), observation_count=selected_count,
        selected_lower_confidence_bound=score, frozen_at_utc=frozen_at.isoformat(),
    )
    return policy


class NetAlphaThresholdRegistry:
    """Hash-chained store; a strategy/cohort threshold is immutable once frozen."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def freeze(self, policy: NetAlphaThresholdPolicy) -> str:
        prior = self.resolve(policy.strategy_sha256, policy.cohort_id)
        if prior is not None:
            if prior.sha256 != policy.sha256:
                raise NetAlphaPolicyError("frozen cohort threshold cannot be changed or recalibrated")
            return prior.sha256
        self.journal.append({"t": "NET_ALPHA_POLICY_FROZEN", "policy": policy.as_dict(),
                             "policy_sha256": policy.sha256})
        return policy.sha256

    def resolve(self, strategy_sha256: str, cohort_id: str) -> NetAlphaThresholdPolicy | None:
        rows = [row for row in self.journal.records() if row.get("t") == "NET_ALPHA_POLICY_FROZEN"
                and row.get("policy", {}).get("strategy_sha256") == strategy_sha256
                and row.get("policy", {}).get("cohort_id") == cohort_id]
        if not rows:
            return None
        first = rows[0]
        if any(row != first for row in rows[1:]):
            raise NetAlphaPolicyError("threshold registry contains conflicting frozen policy records")
        policy = NetAlphaThresholdPolicy(**first["policy"])
        if policy.sha256 != first.get("policy_sha256"):
            raise NetAlphaPolicyError("frozen threshold policy digest mismatch")
        return policy

    def record_forward_drawdown(self, *, strategy_sha256: str, cohort_id: str,
                                policy_sha256: str, session_id: str, drawdown: float) -> None:
        policy = self.resolve(strategy_sha256, cohort_id)
        if policy is None or policy.sha256 != policy_sha256:
            raise NetAlphaPolicyError("forward drawdown must reference the cohort's frozen threshold policy")
        if not session_id.strip() or not math.isfinite(drawdown) or not 0 <= drawdown < 1:
            raise NetAlphaPolicyError("drawdown receipt requires session identity and fraction in [0, 1)")
        record = {"t": "FORWARD_DRAWDOWN", "strategy_sha256": strategy_sha256,
                  "cohort_id": cohort_id, "policy_sha256": policy_sha256,
                  "session_id": session_id, "drawdown": drawdown}
        prior = next((row for row in self.journal.records() if row.get("t") == record["t"]
                      and row.get("session_id") == session_id), None)
        if prior is not None:
            if prior != record:
                raise NetAlphaPolicyError("forward drawdown receipt is immutable")
            return
        self.journal.append(record)
