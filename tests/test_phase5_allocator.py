import base64
from dataclasses import replace
from datetime import date, datetime, timezone

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.risk import allocator as A
from financial_brain.risk.audited_forward_kelly import (
    AuditedForwardReturns,
    AuditedKellyPolicy,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 27, 10, tzinfo=UTC)


def series(mean, sd, n, seed=0):
    return tuple(np.random.default_rng(seed).normal(mean, sd, n))


def _policy():
    return AuditedKellyPolicy("allocator-v1", datetime(2026, 10, 20, tzinfo=UTC),
                              date(2026, 10, 21), maximum_position_fraction=0.10)


def _signed_forward(name, returns, private_key):
    evidence = AuditedForwardReturns(
        strategy_id=name,
        strategy_version_sha256="a" * 64,
        cohort_id=f"cohort-{name}",
        cohort_manifest_sha256="b" * 64,
        execution_costs_sha256="c" * 64,
        audit_report_sha256="d" * 64,
        auditor_id="pinned-external-auditor",
        training_through=datetime(2026, 10, 10, tzinfo=UTC),
        first_eligible_session=date(2026, 10, 21),
        forward_start=datetime(2026, 10, 21, tzinfo=UTC),
        forward_end=datetime(2026, 11, 20, tzinfo=UTC),
        audited_at=datetime(2026, 11, 25, tzinfo=UTC),
        net_period_returns=tuple(returns),
        net_of_all_costs=True,
        includes_zero_return_periods=True,
        signature_b64="",
    )
    return replace(evidence, signature_b64=base64.b64encode(
        private_key.sign(evidence.signed_bytes())).decode("ascii"))


def _allocate(sleeves):
    private_key = Ed25519PrivateKey.generate()
    public = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
    receipts = {s.name: _signed_forward(s.name, s.returns, private_key) for s in sleeves}
    return A.allocate(
        sleeves,
        forward_evidence=receipts,
        policy=_policy(),
        decision_at=NOW,
        trusted_auditor_public_key_b64=public,
    )


def test_short_history_is_rejected_from_sizing_even_when_money_gate_passes():
    with pytest.raises(A.AllocatorError, match="insufficient"):
        _allocate([A.Sleeve("new", series(0.02, 0.03, 10), True)])


def test_audited_forward_net_edge_gets_capped_size_only_after_money_gate():
    returns = tuple(0.009 if i % 2 else 0.011 for i in range(120))
    result = _allocate([A.Sleeve("good", returns, True)])
    assert result["weights"]["good"] > 0
    assert result["weights"]["good"] <= 0.10
    assert result["audited_kelly_decisions"]["good"].periods == 120


def test_a_statistically_strong_sleeve_still_gets_zero_without_money_gate():
    returns = tuple(0.009 if i % 2 else 0.011 for i in range(120))
    result = _allocate([A.Sleeve("good", returns, False)])
    assert result["weights"]["good"] == 0
    assert "good" in result["paper_only"]
    assert result["detail"]["good"]["held_to_paper_by_money_gate"]


def test_negative_recent_forward_edge_is_stopped():
    returns = tuple(series(0.02, 0.003, 80, 1)) + tuple(series(-0.01, 0.003, 40, 2))
    result = _allocate([A.Sleeve("fading", returns, True)])
    assert result["weights"]["fading"] == 0
    assert result["detail"]["fading"]["state"] in (A.STOP, A.REDUCE)


def test_no_forward_edge_is_stopped():
    result = _allocate([A.Sleeve("noise", series(0.0, 0.03, 120, 5), True)])
    assert result["weights"]["noise"] == 0


def test_multiple_audited_weights_never_exceed_cap_or_total_capital():
    sleeves = [A.Sleeve(f"s{i}", tuple(0.009 if j % 2 else 0.011 for j in range(120)), True)
               for i in range(6)]
    result = _allocate(sleeves)
    assert sum(result["weights"].values()) <= 1
    assert max(result["weights"].values()) <= 0.10


def test_raw_in_memory_returns_cannot_bypass_receipt_or_alter_signed_series():
    sleeve = A.Sleeve("a", (0.1,) * 60, True)
    private_key = Ed25519PrivateKey.generate()
    receipt = _signed_forward("a", (0.1,) * 60, private_key)
    public = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
    with pytest.raises(A.AllocatorError, match="exactly one"):
        A.allocate([sleeve], forward_evidence={}, policy=_policy(), decision_at=NOW,
                   trusted_auditor_public_key_b64=public)
    with pytest.raises(A.AllocatorError, match="exactly match"):
        A.allocate([replace(sleeve, returns=(0.2,) * 60)],
                   forward_evidence={"a": receipt}, policy=_policy(), decision_at=NOW,
                   trusted_auditor_public_key_b64=public)
    with pytest.raises(A.AllocatorError, match="rejected"):
        A.allocate([sleeve], forward_evidence={"a": replace(receipt, signature_b64="bad")},
                   policy=_policy(), decision_at=NOW,
                   trusted_auditor_public_key_b64=public)


def test_errors():
    with pytest.raises(A.AllocatorError, match="no sleeves"):
        A.allocate([], forward_evidence={}, policy=_policy(), decision_at=NOW,
                   trusted_auditor_public_key_b64="unused")
    duplicate = [A.Sleeve("a", (0.1,) * 30), A.Sleeve("a", (0.1,) * 30)]
    with pytest.raises(A.AllocatorError, match="unique"):
        A.allocate(duplicate, forward_evidence={}, policy=_policy(), decision_at=NOW,
                   trusted_auditor_public_key_b64="unused")


def test_state_diagnostics_remain_available_without_granting_sizing_authority():
    edge = series(0.01, 0.03, 200)
    assert A.decayed_edge(edge)["n_eff"] < len(edge)
    assert A.state_of(A.Sleeve("diagnostic-only", edge))["state"] in (A.ACTIVE, A.REDUCE, A.STOP)
