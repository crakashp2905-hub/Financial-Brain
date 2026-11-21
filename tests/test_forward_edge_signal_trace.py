from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.signal_trace import (
    SignalTrace,
    SignalTraceError,
    SignalTraceJournal,
    TraceEvidence,
    render_signal_trace,
)


DECISION_AT = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _trace(**overrides):
    values = {
        "signal_id": "sig-1", "strategy_id": "momentum", "strategy_version": "v2",
        "symbol": "NSE:ABC", "decision": "TRADE", "decision_at": DECISION_AT,
        "evidence": (TraceEvidence("kite:quote:1", "a" * 64,
                                    DECISION_AT - timedelta(seconds=1)),),
        "bid": 99.9, "ask": 100.1, "volume": 50_000,
        "lower_price_limit": 90.0, "upper_price_limit": 110.0,
        "expires_at": DECISION_AT + timedelta(days=1), "side": "BUY",
        "quantity": 5, "order_type": "LIMIT", "limit_price": 100.0,
        "reason_codes": ("BREAKOUT_CONFIRMED",),
    }
    values.update(overrides)
    return SignalTrace(**values)


def test_trace_hash_binds_asof_evidence_quotes_limits_and_order_intent():
    trace = _trace()
    body = trace.body()
    assert body["spread_bps"] == pytest.approx(20.0, abs=0.01)
    assert body["volume"] == 50_000
    assert body["lower_price_limit"] == 90
    assert body["expires_at"] is not None
    assert body["quantity"] == 5
    changed = _trace(quantity=6)
    assert changed.digest() != trace.digest()


def test_future_evidence_and_incomplete_no_trade_are_rejected():
    with pytest.raises(SignalTraceError, match="future evidence"):
        _trace(evidence=(TraceEvidence("future", "b" * 64,
                                      DECISION_AT + timedelta(seconds=1)),))
    with pytest.raises(SignalTraceError, match="requires reason codes"):
        _trace(decision="NO_TRADE", bid=None, ask=None, side=None, quantity=None,
               order_type=None, limit_price=None, reason_codes=())


def test_signal_trace_journal_is_immutable_idempotent_and_rendered_safely(tmp_path):
    journal = SignalTraceJournal(tmp_path / "signal-traces.sqlite3")
    trace = _trace(symbol="<img src=x>")
    assert journal.append(trace)
    assert not journal.append(trace)
    assert journal.get(trace.signal_id)["symbol"] == "<img src=x>"
    with pytest.raises(SignalTraceError, match="changed evidence"):
        journal.append(_trace(quantity=6))
    page = render_signal_trace(trace, tmp_path / "trace.html").read_text(encoding="utf-8")
    assert "<img src=x>" not in page
    assert "&lt;img src=x&gt;" in page
    assert trace.digest() in page
    journal.close()
