import pytest

from financial_brain.research.protocol import ProtocolSpec


def _spec(**overrides):
    values = dict(strategy="momentum", strategy_version="v1", schedule=("2026-11-02",),
                  benchmark="NIFTY50", cost_model="india-cash-v1", risk_limits="paper-v1",
                  capital_inr="1000000", entry_rule="next-session-open", mark_rule="close")
    values.update(overrides)
    return ProtocolSpec(**values)


def test_protocol_hash_is_order_independent_for_schedule():
    assert _spec(schedule=("2026-11-02", "2026-11-03")).sha256 == _spec(
        schedule=("2026-11-03", "2026-11-02")).sha256


def test_protocol_rejects_empty_identity_and_bad_session():
    with pytest.raises(ValueError, match="identity"):
        _spec(strategy="").canonical()
    with pytest.raises(ValueError, match="invalid session"):
        _spec(schedule=("not-a-day",)).canonical()
