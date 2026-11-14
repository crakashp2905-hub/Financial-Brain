"""Frozen dependence-robust inference policy for forward return series."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

import numpy as np

from .evidence_stats import StatsError, naive_se, newey_west_se, stationary_bootstrap_ci


class DependenceError(ValueError):
    """Invalid dependence policy or return sample."""


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DependenceError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DependenceError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DependencePolicy:
    policy_id: str
    hac_lags: int
    bootstrap_mean_block: float
    bootstrap_resamples: int
    bootstrap_seed: int
    alpha: float
    frozen_at_utc: str
    first_observation_at_utc: str

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise DependenceError("policy id is required")
        if type(self.hac_lags) is not int or self.hac_lags < 0:
            raise DependenceError("HAC lag count must be a nonnegative integer")
        if not math.isfinite(self.bootstrap_mean_block) or self.bootstrap_mean_block < 1:
            raise DependenceError("bootstrap mean block must be finite and at least one")
        if type(self.bootstrap_resamples) is not int or self.bootstrap_resamples < 100:
            raise DependenceError("at least 100 bootstrap resamples are required")
        if type(self.bootstrap_seed) is not int:
            raise DependenceError("bootstrap seed must be an integer")
        if not 0 < self.alpha < 1:
            raise DependenceError("alpha must be between zero and one")
        frozen = _utc(self.frozen_at_utc, "frozen_at_utc")
        first = _utc(self.first_observation_at_utc, "first_observation_at_utc")
        if frozen >= first:
            raise DependenceError("dependence policy must be frozen before the first observation")
        object.__setattr__(self, "frozen_at_utc", frozen.isoformat())
        object.__setattr__(self, "first_observation_at_utc", first.isoformat())

    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarize_dependence(returns: list[float], policy: DependencePolicy) -> dict:
    """Report HAC and stationary-bootstrap uncertainty using the frozen choices."""
    values = np.asarray(returns, dtype=float)
    n = len(values)
    if n < 3 or not np.isfinite(values).all():
        raise DependenceError("returns need at least three finite observations")
    if policy.hac_lags >= n:
        raise DependenceError("frozen HAC lag count must be smaller than the sample")
    try:
        se_iid = naive_se(values.tolist())
        se_hac = newey_west_se(values.tolist(), policy.hac_lags)
        low, high = stationary_bootstrap_ci(
            values.tolist(), resamples=policy.bootstrap_resamples,
            mean_block=policy.bootstrap_mean_block, seed=policy.bootstrap_seed,
            alpha=policy.alpha,
        )
    except StatsError as exc:
        raise DependenceError(str(exc)) from exc
    effective_n = float(n if se_hac == 0 else min(n, max(1.0, n * (se_iid / se_hac) ** 2)))
    return {
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observations": n,
        "effective_sample_size": effective_n,
        "mean_return": float(values.mean()),
        "iid_standard_error": se_iid,
        "newey_west_standard_error": se_hac,
        "hac_lags": policy.hac_lags,
        "bootstrap_mean_block": policy.bootstrap_mean_block,
        "bootstrap_resamples": policy.bootstrap_resamples,
        "bootstrap_seed": policy.bootstrap_seed,
        "confidence_level": 1 - policy.alpha,
        "stationary_bootstrap_interval": [low, high],
        "policy_frozen_at_utc": policy.frozen_at_utc,
        "first_observation_at_utc": policy.first_observation_at_utc,
    }
