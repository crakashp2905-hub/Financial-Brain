from datetime import datetime, timedelta, timezone
import json

import pytest

from financial_brain.evaluation import challenger_registry as CR
from financial_brain.forward_edge.approvals import Approval


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
KEY = b"k" * 32
KEY_ID = "owner-key-v1"
MODEL = "a" * 64


def _candidate(path):
    return CR.register_challenger(
        path, model_id=MODEL, model_sha256="b" * 64,
        training_manifest_sha256="c" * 64, code_sha256="d" * 64,
        created_at=NOW, signing_key=KEY, key_id=KEY_ID,
    )


def _evidence():
    return {
        "status": "VERIFIED", "preauthorized": True, "prospective_only": True,
        "verified_sessions": 80,
        "independent_receipt_coverage": True, "net_alpha_lcb_bps": 1.2,
        "matched_control_lcb_bps": 0.8, "cost_stress_passed": True,
        "multiplicity_adjusted": True, "candidate_count": 4, "evidence_sha256": "e" * 64,
    }


def _approval(evidence, *, actor_id="owner-1"):
    scope = CR.promotion_scope_sha256(model_id=MODEL, evidence_sha256=evidence["evidence_sha256"])
    return Approval("STRATEGY_ENROLLMENT", MODEL, scope, actor_id, "human",
                    NOW - timedelta(minutes=1), NOW + timedelta(hours=1))


def test_registered_training_artifact_is_hash_bound_and_never_auto_promoted(tmp_path):
    path = tmp_path / "models.jsonl"
    registered = _candidate(path)
    assert registered["status"] == "CHALLENGER_ONLY"
    assert registered["auto_promotion_allowed"] is False
    verified = CR.verify_registry(path, verification_keys={KEY_ID: KEY})
    assert verified["valid"] and verified["signature_verified"]
    assert verified["records"][0]["record_sha256"] == registered["record_sha256"]


def test_registry_detects_content_tampering_and_duplicate_models(tmp_path):
    path = tmp_path / "models.jsonl"
    _candidate(path)
    line = json.loads(path.read_text())
    line["status"] = "PROMOTED"
    path.write_text(json.dumps(line) + "\n")
    with pytest.raises(CR.ChallengerRegistryError, match="chain/hash mismatch|signature mismatch"):
        CR.verify_registry(path, verification_keys={KEY_ID: KEY})
    path.unlink()
    _candidate(path)
    with pytest.raises(CR.ChallengerRegistryError, match="already registered"):
        _candidate(path)


def test_training_only_or_underpowered_evidence_cannot_promote(tmp_path):
    path = tmp_path / "models.jsonl"
    _candidate(path)
    training_result = _evidence()
    training_result.update(status="RESEARCH", independent_receipt_coverage=False,
                           net_alpha_lcb_bps=9999.0)
    with pytest.raises(CR.ChallengerRegistryError, match="requires authorized"):
        CR.promote_challenger(path, model_id=MODEL, evidence=training_result,
                              approval=_approval(training_result), now=NOW,
                              signing_key=KEY, key_id=KEY_ID)
    thin = _evidence()
    thin["verified_sessions"] = 12
    with pytest.raises(CR.ChallengerRegistryError, match="requires authorized"):
        CR.promote_challenger(path, model_id=MODEL, evidence=thin,
                              approval=_approval(thin), now=NOW,
                              signing_key=KEY, key_id=KEY_ID)


def test_promotion_requires_independent_future_evidence_and_human_scope(tmp_path):
    path = tmp_path / "models.jsonl"
    _candidate(path)
    evidence = _evidence()
    with pytest.raises(CR.ChallengerRegistryError, match="scoped human approval"):
        CR.promote_challenger(path, model_id=MODEL, evidence=evidence,
                              approval=_approval(evidence, actor_id="agent:autotrain"), now=NOW,
                              signing_key=KEY, key_id=KEY_ID)
    promoted = CR.promote_challenger(path, model_id=MODEL, evidence=evidence,
                                     approval=_approval(evidence), now=NOW,
                                     signing_key=KEY, key_id=KEY_ID)
    assert promoted["status"] == "PROMOTED"
    assert CR.verify_registry(path, verification_keys={KEY_ID: KEY})["records"][-1]["event"] == "CHALLENGER_PROMOTED"
    with pytest.raises(CR.ChallengerRegistryError, match="already promoted"):
        CR.promote_challenger(path, model_id=MODEL, evidence=evidence,
                              approval=_approval(evidence), now=NOW,
                              signing_key=KEY, key_id=KEY_ID)
