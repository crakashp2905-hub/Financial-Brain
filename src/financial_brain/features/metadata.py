"""Availability and horizon metadata for the deep daily OHLCV feature library.

Signal half-life is deliberately left unestimated until measured on a frozen, prospective
cohort. Feature construction windows are not treated as evidence of predictive persistence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

OUTCOME_LABEL_HORIZONS = {"fwd5": 5, "fwd20": 20}


@dataclass(frozen=True)
class FeatureMetadata:
    name: str
    role: str
    construction_horizon_sessions: int | None
    economic_horizon_sessions: int | None
    economic_horizon_status: str
    availability_policy: str
    refresh_cadence: str
    expected_signal_half_life_sessions: float | None
    half_life_status: str

    def to_dict(self) -> dict:
        return asdict(self) | {"metadata_sha256": self.sha256()}

    def sha256(self) -> str:
        payload = asdict(self)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _construction_horizon(name: str) -> int | None:
    if name in {"mom_12_1", "dist_52w_high", "dist_52w_low", "days_since_high", "avwap_52w_low",
                "avwap_52w_high"}:
        return 252
    if name.startswith("dist_ma_") and name.endswith("w"):
        match = re.search(r"_(\d+)w$", name)
        return int(match.group(1)) * 5 if match else None
    if name.startswith("fvg_w_"):
        return 100
    if name.startswith("fvg_"):
        return 20
    windows = [int(value) for value in re.findall(r"(?:^|_)(\d+)(?=_|$)", name)]
    return max(windows) if windows else None


def metadata_for_feature(name: str) -> FeatureMetadata:
    """Describe one generated field without presenting an untested half-life as fact."""
    if not isinstance(name, str) or not name.strip() or name == "d":
        raise ValueError("feature metadata requires a named non-date field")
    horizon = OUTCOME_LABEL_HORIZONS.get(name)
    if horizon is not None:
        return FeatureMetadata(name, "OUTCOME_LABEL", horizon, horizon, "LABEL_MATURITY_DEFINED",
                               "AFTER_FUTURE_SESSIONS_MATURE", "DAILY_SESSION", None,
                               "NOT_APPLICABLE_OUTCOME_LABEL")
    if name == "adv20":
        return FeatureMetadata(name, "LIQUIDITY_CONTEXT", 20, None, "UNESTIMATED",
                               "DAILY_BAR_AND_SOURCE_AVAILABLE",
                               "EACH_DAILY_BAR", None, "UNMEASURED")
    cadence = "WEEKLY_FEATURE_ON_DAILY_BAR" if "_w_" in name or name.endswith("w") else "EACH_DAILY_BAR"
    return FeatureMetadata(name, "PREDICTOR", _construction_horizon(name), None, "UNESTIMATED",
                           "DAILY_BAR_AND_SOURCE_AVAILABLE", cadence, None, "UNMEASURED")


def metadata_for_columns(columns: tuple[str, ...] | list[str]) -> tuple[FeatureMetadata, ...]:
    """Return metadata for every non-date column and reject duplicate names."""
    names = [name for name in columns if name != "d"]
    if len(names) != len(set(names)):
        raise ValueError("feature columns must be unique")
    return tuple(metadata_for_feature(name) for name in names)
