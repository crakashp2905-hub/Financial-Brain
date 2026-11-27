import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.nifty_delta import (
    NiftyDeltaError, NiftyDeltaPosition, assess_nifty_delta, require_within_nifty_delta_cap,
)
from financial_brain.risk.signed_policy import SignedRiskPolicy


NOW = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)


def signed_policy(cap=500_000):
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    policy = SignedRiskPolicy("risk", "v1", "owner", NOW - timedelta(days=1),
        NOW + timedelta(days=5), 1_000_000, 5_000, 0.02, 0.05, 0.1, 10,
        cap, 25, "")
    return replace(policy, signature_b64=base64.b64encode(
        private.sign(policy.canonical_bytes())).decode()), public


def position(pid, strategy, instrument, index, quantity, *, beta=1.0, delta=1.0,
             date=NOW - timedelta(days=1), digest="a" * 64):
    return NiftyDeltaPosition(pid, strategy, instrument, index, quantity, 1, 100_000,
                              delta, beta, date, digest)


def test_aggregates_directional_delta_across_indices_and_strategies():
    policy, public = signed_policy()
    positions = (position("cash", "swing", "STOCK-A", "NIFTY50", 2),
                 position("option", "intraday", "BANKNIFTY-CE", "BANKNIFTY", 1,
                          delta=0.5, beta=1.2))
    result = assess_nifty_delta(positions, as_of=NOW, policy=policy,
                                trusted_owner_public_key_b64=public)
    assert result.net_delta_inr == pytest.approx(260_000)
    assert result.gross_delta_inr == pytest.approx(260_000)
    assert result.within_cap
    require_within_nifty_delta_cap(result)


def test_hard_signed_cap_refuses_cross_strategy_directional_breach():
    policy, public = signed_policy(cap=250_000)
    result = assess_nifty_delta((position("a", "s1", "A", "NIFTY", 2),
                                 position("b", "s2", "B", "BANKNIFTY", 1)),
        as_of=NOW, policy=policy, trusted_owner_public_key_b64=public)
    assert result.gross_delta_inr == 300_000 and not result.within_cap
    with pytest.raises(NiftyDeltaError, match="exceeds absolute cap"):
        require_within_nifty_delta_cap(result)


def test_unknown_or_stale_beta_and_invalid_signature_fail_closed():
    policy, public = signed_policy()
    stale = position("a", "s", "A", "NIFTY", 1, date=NOW - timedelta(days=60))
    with pytest.raises(NiftyDeltaError, match="stale"):
        assess_nifty_delta((stale,), as_of=NOW, policy=policy,
                           trusted_owner_public_key_b64=public)
    altered = replace(policy, max_nifty_direction_delta_inr=1_000_000)
    with pytest.raises(ValueError, match="signature"):
        assess_nifty_delta((position("a", "s", "A", "NIFTY", 1),), as_of=NOW,
                           policy=altered, trusted_owner_public_key_b64=public)
