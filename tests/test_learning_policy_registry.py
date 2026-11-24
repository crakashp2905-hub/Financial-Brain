import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.learning.policy_registry import (
    PolicyArtifact,
    PolicyRegistry,
    PolicyRegistryError,
)

NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)


def _hash(number: int) -> str:
    return f"{number:064x}"


def _artifact(policy_id: str = "policy-1") -> PolicyArtifact:
    return PolicyArtifact(policy_id, _hash(1), _hash(2), _hash(3), _hash(4),
                          {"price": "v3", "breadth": "v2"}, _hash(5), _hash(6), _hash(7))


def _register(registry: PolicyRegistry) -> None:
    registry.register(_artifact(), event_id="e0", actor_id="agent:research", reason="candidate generated",
                      actor_type="GENERATOR",
                      evidence_sha256=_hash(20), occurred_at=NOW)


def _transition(registry: PolicyRegistry, status: str, *, event: str, actor: str = "eval:1",
                actor_type: str = "EVALUATOR", approval: str | None = None) -> None:
    registry.transition("policy-1", status=status, event_id=event, actor_id=actor,
                        reason=f"record {status}", actor_type=actor_type, evidence_sha256=_hash(30),
                        approval_sha256=approval, occurred_at=NOW + timedelta(minutes=int(event[1:])))


def test_artifact_is_immutable_and_candidate_registration_is_idempotent(tmp_path) -> None:
    registry = PolicyRegistry(tmp_path / "policies.sqlite")
    try:
        _register(registry)
        assert registry.status("policy-1") == "CANDIDATE"
        assert registry.register(_artifact(), event_id="duplicate", actor_id="researcher",
                                 reason="idempotent duplicate", actor_type="RESEARCHER", evidence_sha256=_hash(20),
                                 occurred_at=NOW) is False
        assert registry.artifact("policy-1")["weights_sha256"] == _hash(2)
        changed = PolicyArtifact("policy-1", _hash(1), _hash(22), _hash(3), _hash(4),
                                 {"price": "v3", "breadth": "v2"}, _hash(5), _hash(6), _hash(7))
        with pytest.raises(PolicyRegistryError, match="different immutable artifact"):
            registry.register(changed, event_id="changed", actor_id="researcher", actor_type="RESEARCHER",
                              reason="changed artifact", evidence_sha256=_hash(20), occurred_at=NOW)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry._con.execute("UPDATE policy_artifacts SET artifact_json='{}' WHERE policy_id='policy-1'")
    finally:
        registry.close()


def test_state_machine_blocks_generator_promotion_and_requires_human_review(tmp_path) -> None:
    registry = PolicyRegistry(tmp_path / "policies.sqlite")
    try:
        _register(registry)
        with pytest.raises(PolicyRegistryError, match="generators"):
            _transition(registry, "OOS_PASSED", event="e1", actor="agent:research", actor_type="GENERATOR")
        _transition(registry, "OOS_PASSED", event="e1")
        _transition(registry, "SHADOW", event="e2")
        _transition(registry, "FORWARD_UNPROVEN", event="e3")
        with pytest.raises(PolicyRegistryError, match="human reviewer"):
            _transition(registry, "REVIEW_ELIGIBLE", event="e4")
        with pytest.raises(PolicyRegistryError, match="human reviewer"):
            _transition(registry, "REVIEW_ELIGIBLE", event="e4", actor_type="EVALUATOR", approval=_hash(40))
        _transition(registry, "REVIEW_ELIGIBLE", event="e4", actor="owner", actor_type="HUMAN_REVIEWER",
                    approval=_hash(40))
        assert registry.status("policy-1") == "REVIEW_ELIGIBLE"
        assert len(registry.events("policy-1")) == 5
        assert registry.events("policy-1")[-1]["reason"] == "record REVIEW_ELIGIBLE"
        assert registry.verify()
        with pytest.raises(PolicyRegistryError, match="not allowed"):
            _transition(registry, "OOS_PASSED", event="e5")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._con.execute("DELETE FROM policy_events WHERE event_id='e1'")
    finally:
        registry.close()


def test_safety_actor_can_only_block_or_retire_and_terminal_states_stay_terminal(tmp_path) -> None:
    registry = PolicyRegistry(tmp_path / "policies.sqlite")
    try:
        _register(registry)
        with pytest.raises(PolicyRegistryError, match="only block or retire"):
            _transition(registry, "OOS_PASSED", event="e1", actor="safety", actor_type="SYSTEM_SAFETY")
        _transition(registry, "BLOCKED", event="e1", actor="safety", actor_type="SYSTEM_SAFETY")
        with pytest.raises(PolicyRegistryError, match="not allowed"):
            _transition(registry, "OOS_PASSED", event="e2")
        assert registry.verify()
    finally:
        registry.close()


def test_invalid_hash_or_missing_feature_version_fails_closed() -> None:
    with pytest.raises(PolicyRegistryError, match="SHA-256"):
        PolicyArtifact("bad", "not-a-hash", _hash(2), _hash(3), _hash(4), {"price": "v1"},
                       _hash(5), _hash(6), _hash(7))
    with pytest.raises(PolicyRegistryError, match="feature names and versions"):
        PolicyArtifact("bad", _hash(1), _hash(2), _hash(3), _hash(4), {}, _hash(5), _hash(6), _hash(7))
