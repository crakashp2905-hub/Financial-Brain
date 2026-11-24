"""Append-only causal journal for prequential prediction and shadow updates."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PrequentialError(ValueError):
    """Invalid causal sequence, evidence hash, or shadow update."""


def _time(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrequentialError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: str, name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise PrequentialError(f"{name} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class PredictionSeal:
    prediction_id: str
    model_version: str
    feature_version: str
    predicted_at_utc: datetime
    data_cutoff_utc: datetime
    forecast_sha256: str
    decision_sha256: str
    evidence_status: str = "PREDICTION_SEALED"

    def __post_init__(self) -> None:
        if not self.prediction_id.strip() or not self.model_version.strip() or not self.feature_version.strip():
            raise PrequentialError("prediction, model and feature versions are required")
        predicted, cutoff = _time(self.predicted_at_utc, "prediction time"), _time(self.data_cutoff_utc, "data cutoff")
        if cutoff > predicted:
            raise PrequentialError("prediction cannot use data after its decision time")
        _hash(self.forecast_sha256, "forecast hash")
        _hash(self.decision_sha256, "decision hash")
        if self.evidence_status != "PREDICTION_SEALED":
            raise PrequentialError("prediction evidence status is fixed")
        object.__setattr__(self, "predicted_at_utc", predicted)
        object.__setattr__(self, "data_cutoff_utc", cutoff)


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    outcome_id: str
    prediction_id: str
    observed_at_utc: datetime
    net_reward: float
    fill_sha256: str
    mark_sha256: str
    realized_cost_sha256: str
    final_horizon_sha256: str

    def __post_init__(self) -> None:
        if not self.outcome_id.strip() or not self.prediction_id.strip():
            raise PrequentialError("outcome and prediction identities are required")
        object.__setattr__(self, "observed_at_utc", _time(self.observed_at_utc, "outcome time"))
        if not math.isfinite(self.net_reward):
            raise PrequentialError("net reward must be finite")
        for name, value in (("fill", self.fill_sha256), ("mark", self.mark_sha256),
                            ("realized costs", self.realized_cost_sha256),
                            ("final horizon", self.final_horizon_sha256)):
            _hash(value, f"{name} hash")


@dataclass(frozen=True, slots=True)
class ShadowUpdate:
    update_id: str
    parent_model_version: str
    model_version: str
    updated_at_utc: datetime
    parent_checkpoint_sha256: str
    changed_parameters_sha256: str
    settled_outcome_ids: tuple[str, ...]
    reason: str
    mode: str = "SHADOW_ONLY"

    def __post_init__(self) -> None:
        if not self.update_id.strip() or not self.parent_model_version.strip() or not self.model_version.strip():
            raise PrequentialError("update and model version identities are required")
        if self.parent_model_version == self.model_version:
            raise PrequentialError("an update must create a new model version")
        object.__setattr__(self, "updated_at_utc", _time(self.updated_at_utc, "update time"))
        _hash(self.parent_checkpoint_sha256, "parent checkpoint hash")
        _hash(self.changed_parameters_sha256, "changed parameters hash")
        if not self.settled_outcome_ids or len(set(self.settled_outcome_ids)) != len(self.settled_outcome_ids):
            raise PrequentialError("update must cite unique settled outcome IDs")
        if not self.reason.strip():
            raise PrequentialError("update reason is required")
        if self.mode != "SHADOW_ONLY":
            raise PrequentialError("online updates are restricted to SHADOW_ONLY")


class PrequentialJournal:
    """SQLite-backed prediction→outcome→shadow-update event log with immutable rows."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.executescript("""
        CREATE TABLE IF NOT EXISTS prequential_predictions (
            prediction_id TEXT PRIMARY KEY, model_version TEXT NOT NULL,
            predicted_at TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prequential_outcomes (
            outcome_id TEXT PRIMARY KEY, prediction_id TEXT UNIQUE NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            FOREIGN KEY(prediction_id) REFERENCES prequential_predictions(prediction_id));
        CREATE TABLE IF NOT EXISTS prequential_updates (
            update_id TEXT PRIMARY KEY, model_version TEXT UNIQUE NOT NULL,
            updated_at TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
        """)
        for table in ("prequential_predictions", "prequential_outcomes", "prequential_updates"):
            self._con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                              f"BEGIN SELECT RAISE(ABORT,'prequential events are immutable'); END")
            self._con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                              f"BEGIN SELECT RAISE(ABORT,'prequential events are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    @staticmethod
    def _payload(value: object) -> tuple[str, str]:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return body, hashlib.sha256(body.encode()).hexdigest()

    def seal_prediction(self, prediction: PredictionSeal) -> str:
        payload, digest = self._payload({
            "prediction_id": prediction.prediction_id, "model_version": prediction.model_version,
            "feature_version": prediction.feature_version,
            "predicted_at_utc": prediction.predicted_at_utc.isoformat(),
            "data_cutoff_utc": prediction.data_cutoff_utc.isoformat(),
            "forecast_sha256": prediction.forecast_sha256,
            "decision_sha256": prediction.decision_sha256,
            "evidence_status": prediction.evidence_status,
        })
        try:
            self._con.execute("INSERT INTO prequential_predictions VALUES (?,?,?,?,?)", (
                prediction.prediction_id, prediction.model_version,
                prediction.predicted_at_utc.isoformat(), payload, digest))
            self._con.commit()
        except sqlite3.IntegrityError as exc:
            self._con.rollback()
            raise PrequentialError("prediction identity already sealed") from exc
        return digest

    def observe(self, outcome: ResolvedOutcome) -> str:
        row = self._con.execute("SELECT predicted_at FROM prequential_predictions WHERE prediction_id = ?",
                                (outcome.prediction_id,)).fetchone()
        if row is None:
            raise PrequentialError("outcome cannot precede a sealed prediction")
        if outcome.observed_at_utc.isoformat() <= row[0]:
            raise PrequentialError("outcome must be observed after its prediction")
        payload, digest = self._payload({
            "outcome_id": outcome.outcome_id, "prediction_id": outcome.prediction_id,
            "observed_at_utc": outcome.observed_at_utc.isoformat(), "net_reward": outcome.net_reward,
            "fill_sha256": outcome.fill_sha256, "mark_sha256": outcome.mark_sha256,
            "realized_cost_sha256": outcome.realized_cost_sha256,
            "final_horizon_sha256": outcome.final_horizon_sha256,
        })
        try:
            self._con.execute("INSERT INTO prequential_outcomes VALUES (?,?,?,?,?)", (
                outcome.outcome_id, outcome.prediction_id, outcome.observed_at_utc.isoformat(), payload, digest))
            self._con.commit()
        except sqlite3.IntegrityError as exc:
            self._con.rollback()
            raise PrequentialError("outcome or prediction already settled") from exc
        return digest

    def record_shadow_update(self, update: ShadowUpdate) -> str:
        rows = self._con.execute(
            "SELECT outcome_id, observed_at FROM prequential_outcomes WHERE outcome_id IN (%s)" %
            ",".join("?" for _ in update.settled_outcome_ids), update.settled_outcome_ids).fetchall()
        by_id = {row[0]: row[1] for row in rows}
        if set(by_id) != set(update.settled_outcome_ids):
            raise PrequentialError("shadow update can use only already settled outcomes")
        if any(update.updated_at_utc.isoformat() < by_id[outcome_id]
               for outcome_id in update.settled_outcome_ids):
            raise PrequentialError("update cannot precede a cited outcome")
        payload, digest = self._payload({
            "update_id": update.update_id, "parent_model_version": update.parent_model_version,
            "model_version": update.model_version, "updated_at_utc": update.updated_at_utc.isoformat(),
            "parent_checkpoint_sha256": update.parent_checkpoint_sha256,
            "changed_parameters_sha256": update.changed_parameters_sha256,
            "settled_outcome_ids": list(update.settled_outcome_ids), "reason": update.reason,
            "mode": update.mode,
        })
        try:
            self._con.execute("INSERT INTO prequential_updates VALUES (?,?,?,?,?)", (
                update.update_id, update.model_version, update.updated_at_utc.isoformat(), payload, digest))
            self._con.commit()
        except sqlite3.IntegrityError as exc:
            self._con.rollback()
            raise PrequentialError("update identity or model version already recorded") from exc
        return digest

    def event_counts(self) -> dict[str, int]:
        return {table.removeprefix("prequential_"): self._con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("prequential_predictions", "prequential_outcomes", "prequential_updates")}


__all__ = ["PrequentialError", "PrequentialJournal", "PredictionSeal", "ResolvedOutcome", "ShadowUpdate"]
