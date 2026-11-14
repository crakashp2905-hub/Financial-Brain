"""Reproducible, timestamp-gated offline jobs for matured strategy outcomes."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd


class OfflineTrainingError(ValueError):
    """Invalid, immature, or unversioned offline training input."""


COMPLETE_OUTCOME_STATUSES = frozenset({"MATURED", "SETTLED"})
OUTCOME_COMPLETENESS_CONTRACT = (
    "only MATURED or SETTLED outcomes enter training; OPEN and PENDING_SETTLEMENT are quarantined"
)


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


def _required_timestamp(value: Any, *, column: str) -> pd.Timestamp:
    stamp = _timestamp(value, column=column)
    assert stamp is not None
    return stamp


def _canonical_dataset_sha256(frame: pd.DataFrame, *, features: list[str]) -> str:
    columns = ["d", "lineage", "decision_at_utc", "outcome_status", "label_matured_at_utc",
               "feature_available_at_utc", "feature_revision_at_utc", "label_revision_at_utc",
               "dataset_version", "feature_schema_version", *features, "fwd20"]
    ordered = frame.sort_values(["d", "lineage"], kind="mergesort")
    rows: list[list[Any]] = []
    for row in ordered[columns].itertuples(index=False, name=None):
        day, lineage, decision, outcome_status, matured, feature_available, feature_revision, label_revision, \
            dataset_version, schema_version, *values = row
        encoded: list[Any] = [
            day.isoformat() if hasattr(day, "isoformat") else str(day),
            str(lineage), decision.isoformat(), str(outcome_status),
            matured.isoformat() if matured is not None else None,
            feature_available.isoformat(), feature_revision.isoformat(),
            label_revision.isoformat() if label_revision is not None else None,
            str(dataset_version), str(schema_version),
        ]
        encoded.extend(None if pd.isna(value) else float(value).hex() for value in values)
        rows.append(encoded)
    body = json.dumps(rows, sort_keys=False, separators=(",", ":"), ensure_ascii=True)
    return _sha256(body.encode("utf-8"))


def purged_embargo_splits(
    panel: pd.DataFrame,
    *,
    trained_as_of: datetime,
    n_splits: int = 3,
    embargo_sessions: int = 20,
) -> list[dict[str, Any]]:
    """Describe expanding chronological folds with interval purging and a session embargo.

    Each fold trains only on decisions strictly before its validation block, removes the
    embargo-adjacent decision sessions, and admits a label only when both its maturity and
    latest revision were known before the first validation decision. Feature availability and
    feature revision timestamps must be no later than that row's own decision time.
    Returned row positions refer to ``panel`` and are suitable for an explicit fit/evaluation
    split; hashes make the split membership reproducible without exposing row contents.
    """
    if type(n_splits) is not int or n_splits < 1 or type(embargo_sessions) is not int or embargo_sessions < 0:
        raise OfflineTrainingError("split count must be positive and embargo_sessions nonnegative")
    if not isinstance(trained_as_of, datetime) or trained_as_of.tzinfo is None \
            or trained_as_of.utcoffset() is None:
        raise OfflineTrainingError("trained_as_of must be timezone-aware")
    required = {"d", "lineage", "decision_at_utc", "outcome_status", "label_matured_at_utc",
                "feature_available_at_utc", "feature_revision_at_utc", "label_revision_at_utc"}
    if missing := required - set(panel.columns):
        raise OfflineTrainingError(f"split panel missing columns: {sorted(missing)}")
    frame = panel.copy().reset_index(drop=True)
    parsed_dates = pd.to_datetime(frame["d"], errors="coerce")
    if parsed_dates.isna().any():
        raise OfflineTrainingError("split rows require valid session dates")
    frame["_session"] = parsed_dates.dt.date
    sessions = sorted(frame["_session"].unique())
    if len(sessions) < n_splits + 2:
        raise OfflineTrainingError("not enough distinct sessions for purged validation folds")
    decision = [_required_timestamp(x, column="decision_at_utc") for x in frame["decision_at_utc"]]
    feature_available = [_required_timestamp(x, column="feature_available_at_utc")
                         for x in frame["feature_available_at_utc"]]
    feature_revision = [_required_timestamp(x, column="feature_revision_at_utc")
                        for x in frame["feature_revision_at_utc"]]
    maturity = [_timestamp(x, column="label_matured_at_utc", allow_missing=True)
                for x in frame["label_matured_at_utc"]]
    label_revision = [_timestamp(x, column="label_revision_at_utc", allow_missing=True)
                      for x in frame["label_revision_at_utc"]]
    outcome_status = frame["outcome_status"].map(
        lambda value: "" if pd.isna(value) else str(value).strip().upper())
    as_of = pd.Timestamp(trained_as_of).tz_convert("UTC")
    for i, decided in enumerate(decision):
        if (feature_available[i] > decided or feature_revision[i] > decided
                or feature_revision[i] < feature_available[i]):
            raise OfflineTrainingError("feature availability or revision occurs after its decision time")
        matured_at, revised_at = maturity[i], label_revision[i]
        if matured_at is not None and (matured_at < decided or revised_at is None
                                       or revised_at < matured_at):
            raise OfflineTrainingError("label maturity/revision timestamps are inconsistent")

    positions_by_session = {session: [i for i, value in enumerate(frame["_session"]) if value == session]
                            for session in sessions}
    folds: list[dict[str, Any]] = []
    for fold_index in range(1, n_splits + 1):
        start_index = (len(sessions) * fold_index) // (n_splits + 1)
        end_index = (len(sessions) * (fold_index + 1)) // (n_splits + 1)
        if start_index <= 0 or end_index <= start_index:
            continue
        val_sessions = sessions[start_index:end_index]
        val_start_rows = positions_by_session[val_sessions[0]]
        val_start = min(decision[i] for i in val_start_rows)
        gap_start = max(0, start_index - embargo_sessions)
        train_sessions = sessions[:gap_start]
        candidate_train = [i for s in train_sessions for i in positions_by_session[s]]
        train_rows: list[int] = []
        for i in candidate_train:
            matured_at, revised_at = maturity[i], label_revision[i]
            if (outcome_status.iloc[i] in COMPLETE_OUTCOME_STATUSES
                    and matured_at is not None and revised_at is not None and matured_at < val_start
                    and revised_at < val_start and matured_at <= as_of and revised_at <= as_of):
                train_rows.append(i)
        validation_rows: list[int] = []
        for session in val_sessions:
            for i in positions_by_session[session]:
                matured_at, revised_at = maturity[i], label_revision[i]
                if (outcome_status.iloc[i] in COMPLETE_OUTCOME_STATUSES
                        and matured_at is not None and revised_at is not None
                        and matured_at <= as_of and revised_at <= as_of):
                    validation_rows.append(i)
        if not train_rows or not validation_rows:
            continue
        def membership_sha(indices: list[int]) -> str:
            rows = sorted((str(frame.loc[i, "d"]), str(frame.loc[i, "lineage"])) for i in indices)
            return _sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8"))
        folds.append({
            "fold": fold_index,
            "train_rows": train_rows,
            "validation_rows": validation_rows,
            "train_rows_sha256": membership_sha(train_rows),
            "validation_rows_sha256": membership_sha(validation_rows),
            "train_count": len(train_rows),
            "validation_count": len(validation_rows),
            "candidate_train_count": len(candidate_train),
            "purged_label_count": len(candidate_train) - len(train_rows),
            "embargoed_session_count": start_index - gap_start,
            "embargo_sessions": embargo_sessions,
            "train_last_session": str(train_sessions[-1]) if train_sessions else None,
            "validation_first_session": str(val_sessions[0]),
            "validation_last_session": str(val_sessions[-1]),
            "validation_start_decision_at_utc": val_start.isoformat(),
            "max_train_label_matured_at_utc": max(cast(pd.Timestamp, maturity[i])
                                                   for i in train_rows).isoformat(),
            "max_train_label_revision_at_utc": max(cast(pd.Timestamp, label_revision[i])
                                                   for i in train_rows).isoformat(),
        })
    if not folds:
        raise OfflineTrainingError("no purged/embargoed fold has mature training and validation outcomes")
    return folds


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
    n_splits: int = 3,
    embargo_sessions: int = 20,
) -> dict:
    """Fit only on fully matured, versioned rows and write an auditable job manifest.

    Required input columns include row decision time, feature availability/revision times,
    target maturity/revision times, dataset/schema versions and ``fwd20``.
    Outcomes without a known maturity timestamp, outcomes maturing after ``trained_as_of``,
    and incomplete feature/label rows are quarantined and counted in the manifest.
    """
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise OfflineTrainingError("dataset_version is required")
    if not isinstance(feature_schema_version, str) or not feature_schema_version.strip():
        raise OfflineTrainingError("feature_schema_version is required")
    reserved = {"d", "lineage", "decision_at_utc", "outcome_status", "label_matured_at_utc",
                "feature_available_at_utc", "feature_revision_at_utc", "label_revision_at_utc",
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
    required = {"d", "lineage", "decision_at_utc", "outcome_status", "label_matured_at_utc",
                "feature_available_at_utc", "feature_revision_at_utc", "label_revision_at_utc",
                "dataset_version", "feature_schema_version", "fwd20", *features}
    missing = required - set(panel.columns)
    if missing:
        raise OfflineTrainingError(f"training panel missing columns: {sorted(missing)}")
    if panel.empty:
        raise OfflineTrainingError("training panel is empty")
    frame = panel[list(required)].copy().reset_index(drop=True)
    for column, expected in (("dataset_version", dataset_version),
                             ("feature_schema_version", feature_schema_version)):
        if frame[column].isna().any() or set(frame[column].astype(str)) != {expected}:
            raise OfflineTrainingError(f"{column} must match the requested frozen version")
    parsed_dates = pd.to_datetime(frame["d"], errors="coerce")
    if parsed_dates.isna().any() or frame["lineage"].isna().any():
        raise OfflineTrainingError("training rows require date and lineage identity")
    frame["d"] = parsed_dates.dt.date
    frame["outcome_status"] = frame["outcome_status"].map(
        lambda value: "" if pd.isna(value) else str(value).strip().upper())
    if frame["lineage"].astype(str).str.strip().eq("").any():
        raise OfflineTrainingError("training row lineage cannot be empty")
    if frame.duplicated(["d", "lineage"]).any():
        raise OfflineTrainingError("training rows must be unique by date and lineage")

    decisions = [_timestamp(value, column="decision_at_utc") for value in frame["decision_at_utc"]]
    maturities = [_timestamp(value, column="label_matured_at_utc", allow_missing=True)
                  for value in frame["label_matured_at_utc"]]
    feature_availability = [_timestamp(value, column="feature_available_at_utc")
                            for value in frame["feature_available_at_utc"]]
    feature_revisions = [_timestamp(value, column="feature_revision_at_utc")
                         for value in frame["feature_revision_at_utc"]]
    label_revisions = [_timestamp(value, column="label_revision_at_utc", allow_missing=True)
                       for value in frame["label_revision_at_utc"]]
    decision_series = pd.Series(decisions, index=frame.index, dtype="datetime64[ns, UTC]")
    maturity_series = pd.Series(maturities, index=frame.index, dtype="datetime64[ns, UTC]")
    availability_series = pd.Series(feature_availability, index=frame.index, dtype="datetime64[ns, UTC]")
    feature_revision_series = pd.Series(feature_revisions, index=frame.index, dtype="datetime64[ns, UTC]")
    label_revision_series = pd.Series(label_revisions, index=frame.index, dtype="datetime64[ns, UTC]")
    if ((maturity_series.notna()) & (maturity_series < decision_series)).any():
        raise OfflineTrainingError("outcome maturity cannot precede its decision timestamp")
    if ((availability_series > decision_series).any() or (feature_revision_series > decision_series).any()
            or (feature_revision_series < availability_series).any()):
        raise OfflineTrainingError("feature availability or revision occurs after its decision time")
    if ((maturity_series.notna()) & (label_revision_series.isna() | (label_revision_series < maturity_series))).any():
        raise OfflineTrainingError("matured labels require a revision timestamp no earlier than maturity")
    for column in [*features, "fwd20"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    finite = np.isfinite(frame[[*features, "fwd20"]].to_numpy(dtype=float)).all(axis=1)
    observed = decision_series <= as_of
    matured = maturity_series.notna() & (maturity_series <= as_of)
    label_known = label_revision_series.notna() & (label_revision_series <= as_of)
    outcome_complete = frame["outcome_status"].isin(COMPLETE_OUTCOME_STATUSES)
    eligible_mask = observed & matured & label_known & finite & outcome_complete
    eligible = frame.loc[eligible_mask].copy()
    if eligible.empty:
        raise OfflineTrainingError("no complete outcome has matured by trained_as_of")
    eligible["decision_at_utc"] = decision_series.loc[eligible.index]
    eligible["label_matured_at_utc"] = maturity_series.loc[eligible.index]
    eligible["feature_available_at_utc"] = availability_series.loc[eligible.index]
    eligible["feature_revision_at_utc"] = feature_revision_series.loc[eligible.index]
    eligible["label_revision_at_utc"] = label_revision_series.loc[eligible.index]
    all_rows = frame.copy()
    all_rows["decision_at_utc"] = decision_series
    all_rows["label_matured_at_utc"] = maturity_series
    all_rows["feature_available_at_utc"] = availability_series
    all_rows["feature_revision_at_utc"] = feature_revision_series
    all_rows["label_revision_at_utc"] = label_revision_series
    source_dataset_sha256 = _canonical_dataset_sha256(all_rows, features=features)
    split_folds = purged_embargo_splits(
        all_rows, trained_as_of=trained_as_of,
        n_splits=n_splits, embargo_sessions=embargo_sessions,
    )
    final_fold = split_folds[-1]
    fit_indices = final_fold["train_rows"]
    fit_rows = eligible.loc[fit_indices].copy()
    validation_indices = set(final_fold["validation_rows"])
    if validation_indices.intersection(fit_indices):
        raise OfflineTrainingError("purged split placed a validation row in the training set")
    fit_dataset_sha256 = _canonical_dataset_sha256(fit_rows, features=features)

    from . import learned_model

    manifest_file = Path(manifest_path) if manifest_path is not None else Path(meta_path).with_suffix(".training.json")
    if manifest_file.parent.resolve() != Path(meta_path).parent.resolve():
        raise OfflineTrainingError("training manifest and model metadata must share a directory")
    code_paths = (Path(learned_model.__file__).resolve(), Path(__file__).resolve(),
                  Path(learned_model.__file__).with_name("deep_library.py").resolve())
    code_sha256 = _code_sha256(code_paths)
    # The lower-level routine retains a one-calendar-day guard as a secondary buffer.
    fit_panel = fit_rows[["d", "lineage", *features, "fwd20"]].copy()
    model_meta = learned_model.train_final(
        fit_panel, features, embargo_days=1, model_path=Path(model_path),
        meta_path=Path(meta_path), seed=seed,
    )
    manifest = {
        "schema_version": "fb.offline-training-job.v3",
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
        "outcome_completeness_contract": OUTCOME_COMPLETENESS_CONTRACT,
        "temporal_split_contract": "expanding folds; purge labels overlapping validation; exclude embargo-adjacent sessions; feature availability and revision <= row decision; label maturity and revision <= fit cutoff; only MATURED or SETTLED outcomes",
        "temporal_split_parameters": {"n_splits": n_splits, "embargo_sessions": embargo_sessions},
        "temporal_folds": [{key: value for key, value in fold.items()
                            if key not in {"train_rows", "validation_rows"}} for fold in split_folds],
        "input_rows": int(len(frame)),
        "eligible_matured_rows": int(len(eligible)),
        "fitted_rows": int(len(fit_rows)),
        "reserved_validation_rows": int(final_fold["validation_count"]),
        "final_training_fold": int(final_fold["fold"]),
        "quarantined_rows": int(len(frame) - len(eligible)),
        "complete_outcome_statuses": sorted(COMPLETE_OUTCOME_STATUSES),
        "open_outcome_rows": int((frame["outcome_status"] == "OPEN").sum()),
        "pending_settlement_rows": int((frame["outcome_status"] == "PENDING_SETTLEMENT").sum()),
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
