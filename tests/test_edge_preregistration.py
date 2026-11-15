from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.custody import Receipt
from financial_brain.forward_edge.edge_preregistration import (
    EdgePreregistrationError,
    EdgePreregistrationRegistry,
)


def _register(registry, **changes):
    args = dict(
        trial_id="trial-a", protocol_sha256="a" * 64,
        scheduled_sessions=["2026-01-02", "2026-01-03"],
        registered_at_utc="2026-01-01T00:00:00Z",
        first_observation_at_utc="2026-01-02T00:00:00Z",
        registration_receipt=Receipt(
            "provider", "key-1", "a" * 64,
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc), "signature",
        ),
    )
    args.update(changes)
    return registry.register(**args)


def test_preregistration_is_durable_immutable_and_idempotent(tmp_path):
    path = tmp_path / "preregistrations.jsonl"
    registry = EdgePreregistrationRegistry(path)
    first = _register(registry)
    seq = registry.journal.seq
    assert _register(registry) == first
    assert registry.journal.seq == seq
    recovered = EdgePreregistrationRegistry(path)
    assert recovered.resolve("trial-a") == first
    with pytest.raises(EdgePreregistrationError, match="immutable"):
        _register(recovered, protocol_sha256="b" * 64)


def test_preregistration_rejects_unsigned_mismatch_or_late_freeze(tmp_path):
    registry = EdgePreregistrationRegistry(tmp_path / "preregistrations.jsonl")
    with pytest.raises(EdgePreregistrationError, match="attest"):
        _register(registry, protocol_sha256="b" * 64)
    with pytest.raises(EdgePreregistrationError, match="before observations"):
        _register(registry, registered_at_utc="2026-01-02T00:00:00Z")
