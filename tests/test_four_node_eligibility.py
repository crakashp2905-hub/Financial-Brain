from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.strategies.four_node.eligibility import InstrumentSnapshot, evaluate_instrument


AS_OF = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


def snapshot(**changes) -> InstrumentSnapshot:
    values = dict(
        instrument_id="NSE:INFY", asset_kind="EQUITY", provider_token=408065,
        source_id="kite-instrument-master-2026-11-24", source_sha256="a" * 64,
        valid_from=AS_OF - timedelta(days=1), valid_until=AS_OF + timedelta(days=1),
        as_of=AS_OF, session_bars_complete=True, tradable=True,
        median_turnover_inr=50_000_000.0, min_turnover_inr=10_000_000.0,
    )
    values.update(changes)
    return InstrumentSnapshot(**values)


def test_liquid_point_in_time_equity_and_etf_are_separately_trade_eligible():
    equity = evaluate_instrument(snapshot())
    etf = evaluate_instrument(snapshot(instrument_id="NSE:NIFTYBEES", asset_kind="ETF"))
    assert equity.status == etf.status == "ELIGIBLE"
    assert equity.signal_eligible and equity.trade_eligible
    assert etf.evidence_id == "kite-instrument-master-2026-11-24"


def test_nifty_indices_can_be_signal_references_but_never_trade_instruments():
    decision = evaluate_instrument(snapshot(instrument_id="NSE:NIFTY 50", asset_kind="INDEX",
                                           tradable=False))
    assert decision.status == "REFERENCE_ONLY"
    assert decision.signal_eligible and not decision.trade_eligible


@pytest.mark.parametrize(("changes", "reason"), [
    ({"asset_kind": "OPTIONS"}, "UNSUPPORTED_ASSET_KIND"),
    ({"provider_token": 0}, "MISSING_PROVIDER_INSTRUMENT_ID"),
    ({"session_bars_complete": False}, "SESSION_DATA_INCOMPLETE"),
    ({"median_turnover_inr": 1.0}, "BELOW_MINIMUM_LIQUIDITY"),
    ({"median_turnover_inr": float("nan")}, "INVALID_LIQUIDITY_EVIDENCE"),
    ({"tradable": False}, "NOT_TRADABLE_AT_DECISION"),
    ({"valid_from": AS_OF + timedelta(seconds=1)}, "INSTRUMENT_NOT_EFFECTIVE_AT_DECISION"),
    ({"source_sha256": "bad"}, "INVALID_SOURCE_HASH"),
])
def test_unsupported_missing_or_untradeable_snapshot_cannot_receive_trade(changes, reason):
    decision = evaluate_instrument(snapshot(**changes))
    assert decision.status == "REJECTED"
    assert not decision.signal_eligible and not decision.trade_eligible
    assert reason in decision.reason_codes


def test_timezone_is_required_for_decision_provenance():
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        evaluate_instrument(snapshot(as_of=datetime(2026, 11, 24, 10, 0)))
