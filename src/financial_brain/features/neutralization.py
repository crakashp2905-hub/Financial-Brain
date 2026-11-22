"""Past-fitted cross-sectional feature neutralization.

This is a transform for research features, not outcome evaluation. Coefficients and
numeric scaling are fit only on prior, point-in-time rows; the current session is then
scored with that frozen model. Sector one-hot levels are learned on the fit sample.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
DEFAULT_NUMERIC_CONTROLS = ("size", "liquidity", "beta", "corporate_action")


class NeutralizationError(ValueError):
    """Fit sample or current cross-section violates the causal contract."""


def _session_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _available_by_close(value: Any, session: date) -> bool:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise NeutralizationError("available_at timestamps must be timezone-aware")
        close = datetime.combine(session, time(15, 30), tzinfo=IST).astimezone(UTC)
        return value.astimezone(UTC) <= close
    if isinstance(value, date):
        return value <= session
    raise NeutralizationError("available_at must contain dates or timezone-aware timestamps")


@dataclass(frozen=True)
class NeutralizationModel:
    feature: str
    as_of: date
    sector_column: str
    numeric_controls: tuple[str, ...]
    sector_levels: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    training_rows: int
    training_sessions: int
    excluded_unavailable_rows: int
    model_sha256: str

    @staticmethod
    def _matrix(frame: pd.DataFrame, model: "NeutralizationModel") -> np.ndarray:
        columns = [np.ones(len(frame))]
        for i, name in enumerate(model.numeric_controls):
            x = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(x).all():
                raise NeutralizationError(f"current control {name!r} contains missing/non-finite values")
            columns.append((x - model.means[i]) / model.scales[i])
        sector = frame[model.sector_column].fillna("UNKNOWN").astype(str)
        for level in model.sector_levels:
            columns.append((sector == level).to_numpy(dtype=float))
        return np.column_stack(columns)

    @classmethod
    def fit(
        cls,
        training: pd.DataFrame,
        *,
        feature: str,
        as_of: date,
        sector_column: str = "sector",
        numeric_controls: tuple[str, ...] = DEFAULT_NUMERIC_CONTROLS,
        session_column: str = "session",
        availability_column: str = "available_at",
        min_training_sessions: int = 30,
    ) -> "NeutralizationModel":
        """Fit feature~sector+size+liquidity+beta+corporate-action on prior sessions only."""
        if min_training_sessions < 2:
            raise NeutralizationError("min_training_sessions must be at least two")
        required = {feature, sector_column, session_column, availability_column, *numeric_controls}
        missing = sorted(required - set(training.columns))
        if missing:
            raise NeutralizationError(f"training data missing required columns: {missing}")
        frame = training.copy()
        frame[session_column] = frame[session_column].map(_session_date)
        if (frame[session_column] >= as_of).any():
            raise NeutralizationError("fit sample must contain sessions strictly before as_of")
        available_mask = [
            _available_by_close(v, d) for v, d in zip(frame[availability_column], frame[session_column], strict=True)
        ]
        excluded = int(len(frame) - sum(available_mask))
        frame = frame.loc[available_mask].copy()
        value_columns = [feature, *numeric_controls]
        for name in value_columns:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
        valid = np.isfinite(frame[value_columns].to_numpy(dtype=float)).all(axis=1)
        valid &= frame[sector_column].notna().to_numpy()
        frame = frame.loc[valid].copy()
        session_count = frame[session_column].nunique()
        if session_count < min_training_sessions:
            raise NeutralizationError(
                f"only {session_count} eligible past sessions; need {min_training_sessions}"
            )
        sector_levels = tuple(sorted(frame[sector_column].astype(str).unique()))
        numeric = frame.loc[:, numeric_controls].to_numpy(dtype=float)
        means = numeric.mean(axis=0)
        scales = numeric.std(axis=0, ddof=0)
        scales = np.where(scales > 0, scales, 1.0)
        design = [np.ones(len(frame))]
        design.extend((numeric[:, i] - means[i]) / scales[i] for i in range(len(numeric_controls)))
        sectors = frame[sector_column].astype(str)
        design.extend((sectors == level).to_numpy(dtype=float) for level in sector_levels)
        matrix = np.column_stack(design)
        response = frame[feature].to_numpy(dtype=float)
        coefficients, _, _, _ = np.linalg.lstsq(matrix, response, rcond=None)
        payload = {
            "feature": feature,
            "as_of": as_of.isoformat(),
            "sector_column": sector_column,
            "numeric_controls": list(numeric_controls),
            "sector_levels": list(sector_levels),
            "means": means.tolist(),
            "scales": scales.tolist(),
            "coefficients": coefficients.tolist(),
            "training_rows": len(frame),
            "training_sessions": int(session_count),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
        return cls(
            feature=feature, as_of=as_of, sector_column=sector_column,
            numeric_controls=tuple(numeric_controls), sector_levels=sector_levels,
            means=tuple(float(x) for x in means), scales=tuple(float(x) for x in scales),
            coefficients=tuple(float(x) for x in coefficients), training_rows=len(frame),
            training_sessions=int(session_count), excluded_unavailable_rows=excluded,
            model_sha256=digest,
        )

    def transform(
        self,
        current: pd.DataFrame,
        *,
        session: date,
        session_column: str = "session",
        availability_column: str = "available_at",
    ) -> pd.DataFrame:
        """Apply the frozen past-fitted exposure model to one current session."""
        required = {self.feature, self.sector_column, session_column, availability_column,
                    *self.numeric_controls}
        missing = sorted(required - set(current.columns))
        if missing:
            raise NeutralizationError(f"current cross-section missing required columns: {missing}")
        frame = current.copy()
        dates = frame[session_column].map(_session_date)
        if session < self.as_of:
            raise NeutralizationError("cannot apply a model trained as of a later session")
        if frame.empty or not dates.eq(session).all():
            raise NeutralizationError("current input must be a non-empty single session matching session")
        if any(not _available_by_close(v, session) for v in frame[availability_column]):
            raise NeutralizationError("current feature/control was unavailable by the session close")
        values = pd.to_numeric(frame[self.feature], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise NeutralizationError("current feature contains missing/non-finite values")
        design = self._matrix(frame, self)
        if design.shape[1] != len(self.coefficients):
            raise NeutralizationError("current sector/control design differs from fitted schema")
        frame[f"{self.feature}_neutral"] = values - design @ np.asarray(self.coefficients)
        frame["neutralization_model_sha256"] = self.model_sha256
        frame["neutralization_fit_as_of"] = self.as_of
        return frame


__all__ = ["DEFAULT_NUMERIC_CONTROLS", "NeutralizationError", "NeutralizationModel"]
