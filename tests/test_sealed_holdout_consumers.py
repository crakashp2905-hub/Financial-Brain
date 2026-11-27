from datetime import datetime, timedelta, timezone
from dataclasses import replace
import base64
import hashlib
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.evaluation import labels, models
from financial_brain.forward_edge.approvals import Approval
from financial_brain.research.holdout_access import (
    HoldoutAccessLedger, holdout_approval_message, holdout_scope_sha256,
)
from financial_brain.research.sealed_dataset import SealedDatasetError, SealedDatasetReader

NOW = datetime(2026, 11, 26, 10, tzinfo=timezone.utc)
OWNER = "owner:ramesh"
PREREG = "b" * 64
OWNER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
OWNER_PUBLIC_KEY = OWNER_PRIVATE_KEY.public_key().public_bytes_raw()


def _session(tmp_path, filename: str, dataset_id: str):
    source = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / filename
    root = tmp_path / "fixtures"
    root.mkdir(exist_ok=True)
    target = root / filename
    shutil.copyfile(source, target)
    training_name = filename.replace("_holdout", "")
    training_source = source.parent / training_name
    if training_source.exists():
        shutil.copyfile(training_source, root / training_name)
    for common_training in ("announcement_labels.json", "sentiment_labels.json"):
        common_source = source.parent / common_training
        if common_source.exists():
            shutil.copyfile(common_source, root / common_training)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    run_id = f"run-{dataset_id}"
    scope = holdout_scope_sha256(dataset_id=dataset_id, dataset_sha256=digest,
                                 run_id=run_id, preregistration_sha256=PREREG)
    approval = Approval("HOLDOUT_ACCESS", run_id, scope, OWNER, "human",
                        NOW - timedelta(minutes=1), NOW + timedelta(hours=1), "ADR-0007")
    approval = replace(approval, signature_b64=base64.b64encode(
        OWNER_PRIVATE_KEY.sign(holdout_approval_message(approval))).decode("ascii"))
    ledger = HoldoutAccessLedger(tmp_path / "holdout-access.sqlite", owner_public_key=OWNER_PUBLIC_KEY)
    reader = SealedDatasetReader(root, ledger, owner_id=OWNER)
    session = reader.open_session(
        relative_path=filename, dataset_id=dataset_id, dataset_sha256=digest,
        run_id=run_id, preregistration_sha256=PREREG, approval=approval, now=NOW,
    )
    return session, ledger


def test_legacy_label_holdout_requires_receipt_before_read(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(labels, "FIXTURES", tmp_path / "fixtures")
    with pytest.raises(SealedDatasetError, match="owner-authorized"):
        labels.score("holdout")

    session, ledger = _session(tmp_path, "announcement_labels_holdout.json", "labels-holdout")
    result = labels.score("holdout", holdout_session=session)
    assert result["n"] == 102
    assert result["precision"] >= 0.94, result["misses"]
    assert ledger.audit()["consumed_datasets"] == 1
    ledger.close()


def test_model_benchmarks_require_sealed_session_for_holdout_and_split_event_sets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(models, "FIXTURES", tmp_path / "fixtures")
    with pytest.raises(SealedDatasetError, match="owner-authorized"):
        models._rows("sentiment_holdout")
    with pytest.raises(SealedDatasetError, match="owner-authorized"):
        models._rows("event_type_holdout")

    session, ledger = _session(tmp_path, "sentiment_labels_holdout.json", "models-holdout")
    assert len(models._rows("event_type")) == 168
    assert len(models._rows("sentiment_holdout", holdout_session=session)) > 0
    assert ledger.audit()["consumed_datasets"] == 1
    with pytest.raises(SealedDatasetError, match="different dataset"):
        session.read_json(relative_path="announcement_labels_holdout.json")
    ledger.close()
