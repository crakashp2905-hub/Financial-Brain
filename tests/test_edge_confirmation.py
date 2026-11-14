from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.custody import Receipt, TrustRoot, receipt_message
from financial_brain.forward_edge.dependence import DependencePolicy
from financial_brain.forward_edge.edge_confirmation import (
    SessionEvidence,
    _outcome_digest,
    evaluate_edge_confirmation,
    frozen_protocol_sha256,
)
from financial_brain.forward_edge.evidence_stats import FALSIFICATION_CHECKS
from financial_brain.forward_edge.sample_size import design_sample_size


UTC = timezone.utc
PROVIDER = "independent-time-authority"
KEY_ID = "root-v1"


def _plan(first="2026-01-02T00:00:00Z"):
    return design_sample_size(
        design_id="edge-confirmation-test", detectable_daily_benefit=0.01,
        daily_volatility=0.0001, target_power=0.8, familywise_alpha=0.05,
        planned_looks=1, dependence_design_effect=1.0,
        frozen_at_utc="2026-01-01T00:00:00Z", first_observation_at_utc=first,
    )


def _policy(first="2026-01-02T00:00:00Z"):
    return DependencePolicy(
        policy_id="confirmation-inference-v1", hac_lags=1, bootstrap_mean_block=2.0,
        bootstrap_resamples=100, bootstrap_seed=11, alpha=0.05,
        frozen_at_utc="2026-01-01T00:00:00Z", first_observation_at_utc=first,
    )


def _base_kwargs():
    sessions = ["2026-01-02", "2026-01-03", "2026-01-04"]
    return dict(
        scheduled_sessions=sessions, charter_sha256="a" * 64,
        sample_size_plan=_plan(), dependence_policy=_policy(),
        risk_policy_sha256="b" * 64, execution_policy_sha256="c" * 64,
        integrity_policy_sha256="d" * 64, registered_at_utc="2026-01-01T00:00:00Z",
        registration_receipt=None, first_observation_at_utc="2026-01-02T00:00:00Z",
        session_evidence=[], trust_roots={},
        net_by_cost_multiplier={1.0: 0.01, 1.5: 0.01, 2.0: 0.01},
        net_by_cost_and_fill={(c, f): 0.01 for c in (1.0, 1.5, 2.0)
                              for f in (1.0, 0.75, 0.5)},
        fills_feasible=True, fill_evidence_sha256="e" * 64,
        risk_pass=True, risk_evidence_sha256="f" * 64,
        integrity_pass=True, integrity_evidence_sha256="1" * 64,
        falsification_checks={name: True for name in FALSIFICATION_CHECKS},
        completed_at_utc="2026-01-04T10:00:00Z", release_deadline_utc="2026-01-05T03:00:00Z",
        release_receipt=None,
    )


def test_favorable_synthetic_metrics_cannot_confirm_without_signed_prospective_evidence():
    for value in (0.0, 0.01):
        kwargs = _base_kwargs()
        kwargs["net_by_cost_multiplier"] = {1.0: value, 1.5: value, 2.0: value}
        kwargs["net_by_cost_and_fill"] = {
            (cost, fill): value
            for cost in (1.0, 1.5, 2.0)
            for fill in (1.0, 0.75, 0.5)
        }
        result = evaluate_edge_confirmation(**kwargs)
        assert result["verdict"] == "NOT_CONFIRMED"
        assert "PREREGISTRATION_RECEIPT_NOT_VERIFIED" in result["reasons"]
        assert "INDEPENDENT_SESSION_RECEIPTS_INCOMPLETE" in result["reasons"]
        assert "SAMPLE_SIZE_NOT_MET" in result["reasons"]
        assert "RELEASE_BUNDLE_RECEIPT_NOT_VERIFIED" in result["reasons"]


def test_protocol_digest_binds_charter_schedule_power_inference_risk_execution_and_integrity():
    kwargs = _base_kwargs()
    digest = frozen_protocol_sha256(
        charter_sha256=kwargs["charter_sha256"], scheduled_sessions=kwargs["scheduled_sessions"],
        sample_size_plan=kwargs["sample_size_plan"], dependence_policy=kwargs["dependence_policy"],
        risk_policy_sha256=kwargs["risk_policy_sha256"],
        execution_policy_sha256=kwargs["execution_policy_sha256"],
        integrity_policy_sha256=kwargs["integrity_policy_sha256"],
    )
    assert len(digest) == 64
    assert digest != frozen_protocol_sha256(
        charter_sha256="2" * 64, scheduled_sessions=kwargs["scheduled_sessions"],
        sample_size_plan=kwargs["sample_size_plan"], dependence_policy=kwargs["dependence_policy"],
        risk_policy_sha256=kwargs["risk_policy_sha256"],
        execution_policy_sha256=kwargs["execution_policy_sha256"],
        integrity_policy_sha256=kwargs["integrity_policy_sha256"],
    )


def test_failed_or_missing_release_gates_never_return_edge_confirmed():
    kwargs = _base_kwargs()
    kwargs["fills_feasible"] = False
    kwargs["risk_pass"] = False
    kwargs["integrity_pass"] = None
    kwargs["falsification_checks"] = {}
    result = evaluate_edge_confirmation(**kwargs)
    assert result["verdict"] == "NOT_CONFIRMED"
    assert "FILLS_GATE_NOT_PASSED" in result["reasons"]
    assert "RISK_GATE_NOT_PASSED" in result["reasons"]
    assert "INTEGRITY_GATE_NOT_PASSED" in result["reasons"]
    assert "FALSIFICATION_INSUFFICIENT_EVIDENCE" in result["reasons"]


def _sign(private_key, digest, anchored):
    message = receipt_message(PROVIDER, KEY_ID, digest, anchored)
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")
    return Receipt(PROVIDER, KEY_ID, digest, anchored, signature)


def test_signed_full_evidence_can_clear_the_gate_when_all_requirements_pass():
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = crypto.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    root = TrustRoot(PROVIDER, KEY_ID, base64.b64encode(public).decode("ascii"))
    trust_roots = {(PROVIDER, KEY_ID): root}
    sessions = [(datetime(2026, 1, 2, tzinfo=UTC) + timedelta(days=i)).date().isoformat()
                for i in range(60)]
    first = "2026-01-02T00:00:00Z"
    plan = _plan(first)
    policy = _policy(first)
    kwargs = _base_kwargs()
    kwargs.update(scheduled_sessions=sessions, sample_size_plan=plan, dependence_policy=policy,
                  first_observation_at_utc=first, trust_roots=trust_roots,
                  registered_at_utc="2026-01-01T00:00:00Z")
    protocol = frozen_protocol_sha256(
        charter_sha256=kwargs["charter_sha256"], scheduled_sessions=sessions,
        sample_size_plan=plan, dependence_policy=policy,
        risk_policy_sha256=kwargs["risk_policy_sha256"],
        execution_policy_sha256=kwargs["execution_policy_sha256"],
        integrity_policy_sha256=kwargs["integrity_policy_sha256"],
    )
    kwargs["registration_receipt"] = _sign(
        private, protocol, datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    )
    evidence = []
    for i, day in enumerate(sessions):
        start = datetime.fromisoformat(day).replace(tzinfo=UTC)
        decision_sha = f"{i + 10:064x}"
        decision_at = start.replace(hour=2)
        entry_at = start.replace(hour=3)
        alpha = 0.01 + (i % 5 - 2) * 0.00001
        utility = 0.005 + (i % 3 - 1) * 0.00001
        outcome_at = start.replace(hour=10)
        next_entry = start + timedelta(days=1)
        outcome_row = SessionEvidence(
            day, decision_sha, decision_at.isoformat(), entry_at.isoformat(), None,
            alpha, utility, outcome_at.isoformat(), next_entry.replace(hour=3).isoformat(), None,
        )
        evidence.append(SessionEvidence(
            day, decision_sha, decision_at.isoformat(), entry_at.isoformat(),
            _sign(private, decision_sha, decision_at + timedelta(seconds=1)),
            alpha, utility, outcome_at.isoformat(), next_entry.replace(hour=3).isoformat(),
            _sign(private, _outcome_digest(outcome_row),
                  outcome_at + timedelta(seconds=1)),
        ))
    kwargs["session_evidence"] = evidence
    last = datetime.fromisoformat(sessions[-1]).replace(tzinfo=UTC)
    kwargs["completed_at_utc"] = last.replace(hour=10, minute=2).isoformat()
    kwargs["release_deadline_utc"] = (last + timedelta(days=1)).replace(hour=3).isoformat()
    preview = evaluate_edge_confirmation(**kwargs)
    kwargs["release_receipt"] = _sign(
        private, preview["release_bundle_sha256"], last.replace(hour=10, minute=3)
    )
    result = evaluate_edge_confirmation(**kwargs)
    assert result["verdict"] == "EDGE_CONFIRMED"
    assert result["reasons"] == []

    # The final attestation covers the exact signed outcomes. A post-hoc favorable
    # edit cannot reuse either a session receipt or the release receipt.
    edited = list(kwargs["session_evidence"])
    edited[0] = replace(edited[0], daily_net_alpha=edited[0].daily_net_alpha + 0.1)
    tampered_kwargs = {**kwargs, "session_evidence": edited}
    tampered = evaluate_edge_confirmation(**tampered_kwargs)
    assert tampered["verdict"] == "NOT_CONFIRMED"
    assert tampered["release_bundle_sha256"] != result["release_bundle_sha256"]
    assert "RELEASE_BUNDLE_RECEIPT_NOT_VERIFIED" in tampered["reasons"]
