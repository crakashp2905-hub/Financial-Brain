from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.lake.retention import (
    RedactedPayloadError,
    RetentionDirective,
    RetentionError,
    redact_payload,
)
from financial_brain.lake.store import RawLake

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _directive(**overrides) -> RetentionDirective:
    values = {
        "directive_id": "retention-001",
        "source": "TEST_SOURCE",
        "dataset": "quotes",
        "delete_after": NOW,
        "authority_ref": "review-ticket-17",
        "reviewed_by": "data-owner",
        "reason": "source retention period expired",
    }
    values.update(overrides)
    return RetentionDirective(**values)


def _object(lake: RawLake):
    return lake.put(source="TEST_SOURCE", dataset="quotes", business_date=date(2026, 11, 19),
                    filename="quote.json", payload=b'{"bid":100,"ask":101}', url="fixture://quote")


def test_redaction_is_scoped_and_cannot_run_before_retention_deadline(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    with pytest.raises(RetentionError, match="deadline has not elapsed"):
        redact_payload(lake.root, obj.key, _directive(delete_after=NOW + timedelta(days=1)), now=NOW)
    with pytest.raises(RetentionError, match="scope does not match"):
        redact_payload(lake.root, obj.key, _directive(source="OTHER_SOURCE"), now=NOW)
    assert lake.read(obj) == b'{"bid":100,"ask":101}'


def test_redaction_removes_only_payload_and_keeps_verifiable_provenance(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    receipt = redact_payload(lake.root, obj.key, _directive(), now=NOW)

    assert not lake._path(obj.key).exists()
    assert lake._meta_path(obj.key).is_file()
    assert lake._meta_path(obj.key).with_name("quote.json.redacted.json").is_file()
    assert receipt.original_sha256 == obj.sha256
    assert len(receipt.receipt_sha256) == 64
    with pytest.raises(RedactedPayloadError, match="was redacted"):
        lake.read(obj)


def test_redaction_retry_is_idempotent_and_finishes_interrupted_unlink(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    receipt_path = lake._meta_path(obj.key).with_name("quote.json.redacted.json")
    expected = redact_payload(lake.root, obj.key, _directive(), now=NOW)

    # Simulate a crash after the durable tombstone was written but before the payload unlink.
    lake._path(obj.key).write_bytes(b'{"bid":100,"ask":101}')
    replay = redact_payload(lake.root, obj.key, _directive(), now=NOW + timedelta(seconds=1))
    assert replay == expected
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["original_sha256"] == obj.sha256
    assert not lake._path(obj.key).exists()
    with pytest.raises(RetentionError, match="different redaction directive"):
        redact_payload(lake.root, obj.key, _directive(reason="changed directive"), now=NOW + timedelta(seconds=1))


def test_tampered_redaction_receipt_is_not_accepted_as_recovery_evidence(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    redact_payload(lake.root, obj.key, _directive(), now=NOW)
    receipt_path = lake._meta_path(obj.key).with_name("quote.json.redacted.json")
    row = json.loads(receipt_path.read_text(encoding="utf-8"))
    row["original_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(RetentionError, match="receipt checksum mismatch"):
        redact_payload(lake.root, obj.key, _directive(), now=NOW + timedelta(seconds=1))


def test_recovery_does_not_delete_bytes_that_disagree_with_tombstone(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    redact_payload(lake.root, obj.key, _directive(), now=NOW)
    lake._path(obj.key).write_bytes(b"different payload")
    with pytest.raises(RetentionError, match="does not match tombstone"):
        redact_payload(lake.root, obj.key, _directive(), now=NOW + timedelta(seconds=1))
    assert lake._path(obj.key).read_bytes() == b"different payload"


def test_corrupt_payload_and_path_traversal_are_not_deleted(tmp_path) -> None:
    lake = RawLake(tmp_path / "lake")
    obj = _object(lake)
    lake._path(obj.key).write_bytes(b"changed")
    with pytest.raises(RetentionError, match="does not match immutable metadata"):
        redact_payload(lake.root, obj.key, _directive(), now=NOW)
    with pytest.raises(RetentionError, match="safe relative lake key"):
        redact_payload(lake.root, "../outside", _directive(), now=NOW)
    assert lake._path(obj.key).is_file()
