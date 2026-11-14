"""Reproducible, timestamp-gated offline jobs for matured strategy outcomes."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class OfflineTrainingError(ValueError):
    """Invalid, immature, or unversioned offline training input."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp(value: Any, *, column: str, allow_missing: bool = False) -> pd.Timestamp | None:
    if pd.isna(value):
        if allow_missing:
            return None
        raise OfflineTrainingError(f"{column} timestamp is required")
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise OfflineTrainingError(f"{column} timestamps must include a timezone")
    return stamp.tz_convert("UTC")


def _canonical_dataset_sha256(frame: pd.DataFrame, *, features: list[str]) -> str:
    columns = ["d", "lineage", "decision_at_utc", "label_matured_at_utc",
               "dataset_version", "feature_schema_version", *features, "fwd20"]
    ordered = frame.sort_values(["d", "lineage"], kind="mergesort")
    rows: list[list[Any]] = []
    for row in ordered[columns].itertuples(index=False, name=None):
        day, lineage, decision, matured, dataset_version, schema_version, *values = row
        encoded: list[Any] = [
            day.isoformat() if hasattr(day, "isoformat") else str(day),
            str(lineage), decision.isoformat(), matured.isoformat() if matured is not None else None,
            str(dataset_version), str(schema_version),
        ]
        encoded.extend(None if pd.isna(value) else float(value).hex() for value in values)
        rows.append(encoded)
    body = json.dumps(rows, sort_keys=False, separators=(",", ":"), ensure_ascii=True)
    return _sha256(body.encode("utf-8"))


def _code_sha256(paths: tuple[Path, ...]) -> str:
    body = [{"name": path.name, "sha256": _sha256(path.read_bytes())}
            for path in sorted(paths, key=lambda item: item.name)]
    return _sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _manifest_sha256(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return _sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8"))


def run_offline_training_job(
    panel: pd.DataFrame,
    features: list[str],
    *,
    dataset_version: str,
    feature_schema_version: str,
    trained_as_of: datetime,
    model_path: Path,
    meta_path: Path,
    manifest_path: Path | None = None,
    seed: int = 0,
) -> dict:
    """Fit only on fully matured, versioned rows and write an auditable job manifest.

    Required input columns are ``d``, ``lineage``, ``decision_at_utc``,
    ``label_matured_at_utc``, dataset/schema version columns and ``fwd20``.
    Outcomes without a known maturity timestamp, outcomes maturing after ``trained_as_of``,
    and incomplete feature/label rows are quarantined and counted in the manifest.
    """
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise OfflineTrainingError("dataset_version is required")
    if not isinstance(feature_schema_version, str) or not feature_schema_version.strip():
        raise OfflineTrainingError("feature_schema_version is required")
    reserved = {"d", "lineage", "decision_at_utc", "label_matured_at_utc",
                "dataset_version", "feature_schema_version", "fwd20"}
    if (not features or len(features) != len(set(features))
            or any(not isinstance(f, str) or not f or f in reserved for f in features)):
        raise OfflineTrainingError("features must be non-empty, unique names")
    if type(seed) is not int:
        raise OfflineTrainingError("training seed must be an integer")
    if (not isinstance(trained_as_of, datetime) or trained_as_of.tzinfo is None
            or trained_as_of.utcoffset() is None):
        raise OfflineTrainingError("trained_as_of must be timezone-aware")
    as_of = pd.Timestamp(trained_as_of).tz_convert("UTC")
    required = {"d", "lineage", "decision_at_utc", "label_matured_at_utc",
                "dataset_version", "feature_schema_version", "fwd20", *features}
    missing = required - set(panel.columns)
    if missing:
        raise OfflineTrainingError(f"training panel missing columns: {sorted(missing)}")
    if panel.empty:
        raise OfflineTrainingError("training panel is empty")
    frame = panel[list(required)].copy()
    for column, expected in (("dataset_version", dataset_version),
                             ("feature_schema_version", feature_schema_version)):
        if frame[column].isna().any() or set(frame[column].astype(str)) != {expected}:
            raise OfflineTrainingError(f"{column} must match the requested frozen version")
    parsed_dates = pd.to_datetime(frame["d"], errors="coerce")
    if parsed_dates.isna().any() or frame["lineage"].isna().any():
        raise OfflineTrainingError("training rows require date and lineage identity")
    frame["d"] = parsed_dates.dt.date
    if frame["lineage"].astype(str).str.strip().eq("").any():
        raise OfflineTrainingError("training row lineage cannot be empty")
    if frame.duplicated(["d", "lineage"]).any():
        raise OfflineTrainingError("training rows must be unique by date and lineage")

    decisions = [_timestamp(value, column="decision_at_utc") for value in frame["decision_at_utc"]]
    maturities = [_timestamp(value, column="label_matured_at_utc", allow_missing=True)
                  for value in frame["label_matured_at_utc"]]
    decision_series = pd.Series(decisions, index=frame.index, dtype="datetime64[ns, UTC]")
    maturity_series = pd.Series(maturities, index=frame.index, dtype="datetime64[ns, UTC]")
    if ((maturity_series.notna()) & (maturity_series < decision_series)).any():
        raise OfflineTrainingError("outcome maturity cannot precede its decision timestamp")
    for column in [*features, "fwd20"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    finite = np.isfinite(frame[[*features, "fwd20"]].to_numpy(dtype=float)).all(axis=1)
    observed = decision_series <= as_of
    matured = maturity_series.notna() & (maturity_series <= as_of)
    eligible_mask = observed & matured & finite
    eligible = frame.loc[eligible_mask].copy()
    if eligible.empty:
        raise OfflineTrainingError("no complete outcome has matured by trained_as_of")
    eligible["decision_at_utc"] = decision_series.loc[eligible.index]
    eligible["label_matured_at_utc"] = maturity_series.loc[eligible.index]
    all_rows = frame.copy()
    all_rows["decision_at_utc"] = decision_series
    all_rows["label_matured_at_utc"] = maturity_series
    source_dataset_sha256 = _canonical_dataset_sha256(all_rows, features=features)
    fit_dataset_sha256 = _canonical_dataset_sha256(eligible, features=features)

    from . import learned_model

    manifest_file = Path(manifest_path) if manifest_path is not None else Path(meta_path).with_suffix(".training.json")
    if manifest_file.parent.resolve() != Path(meta_path).parent.resolve():
        raise OfflineTrainingError("training manifest and model metadata must share a directory")
    code_paths = (Path(learned_model.__file__).resolve(), Path(__file__).resolve(),
                  Path(learned_model.__file__).with_name("deep_library.py").resolve())
    code_sha256 = _code_sha256(code_paths)
    # The lower-level routine retains a one-calendar-day guard as a secondary buffer.
    fit_panel = eligible[["d", "lineage", *features, "fwd20"]].copy()
    model_meta = learned_model.train_final(
        fit_panel, features, embargo_days=1, model_path=Path(model_path),
        meta_path=Path(meta_path), seed=seed,
    )
    manifest = {
        "schema_version": "fb.offline-training-job.v1",
        "dataset_version": dataset_version,
        "dataset_sha256": source_dataset_sha256,
        "fit_dataset_sha256": fit_dataset_sha256,
        "feature_schema_version": feature_schema_version,
        "feature_schema_sha256": model_meta["feature_schema_sha256"],
        "features": list(features),
        "code_sha256": code_sha256,
        "seed": seed,
        "training_parameters": model_meta["params"],
        "environment": {
            "python": platform.python_version(), "numpy": version("numpy"),
            "pandas": version("pandas"), "lightgbm": version("lightgbm"),
            "threads": 1, "deterministic": True,
        },
        "trained_as_of_utc": as_of.isoformat(),
        "maturity_contract": "label_matured_at_utc is known and <= trained_as_of_utc",
        "input_rows": int(len(frame)),
        "eligible_matured_rows": int(len(eligible)),
        "quarantined_rows": int(len(frame) - len(eligible)),
        "max_eligible_label_matured_at_utc": max(eligible["label_matured_at_utc"]).isoformat(),
        "model_id": model_meta["model_id"],
        "model_sha256": model_meta["model_sha256"],
        "model_training_data_sha256": model_meta["training_data_sha256"],
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                             encoding="utf-8")
    model_meta["offline_training_manifest_file"] = manifest_file.name
    model_meta["offline_training_manifest_sha256"] = manifest["manifest_sha256"]
    model_meta["offline_training_code_sha256"] = code_sha256
    Path(meta_path).write_text(json.dumps(model_meta, indent=1, sort_keys=True), encoding="utf-8")
    return manifest
