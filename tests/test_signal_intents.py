from datetime import datetime, timezone

import pytest

from financial_brain.signals.intents import ExecutionIntent, IntentError, IntentType

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def intent(kind, **kwargs):
    return ExecutionIntent("i-1", "s-1", kind, NOW, **kwargs)


@pytest.mark.parametrize("kind", list(IntentType))
def test_no_trade_is_a_typed_non_order_and_other_intents_are_distinct(kind):
    if kind == IntentType.NO_TRADE:
        assert intent(kind).intent_type == kind
    elif kind == IntentType.EXCHANGE_LIMIT:
        assert intent(kind, instrument_id="NSE:INFY", units_or_quantity=1,
                      limit_price=1500).intent_type == kind
    elif kind == IntentType.EXCHANGE_MARKETABLE_SIMULATION:
        assert intent(kind, instrument_id="NSE:INFY", units_or_quantity=1).paper_only
    elif kind == IntentType.MF_SWITCH:
        assert intent(kind, instrument_id="MF:1", target_scheme_code="2",
                      units_or_quantity=100).intent_type == kind
    else:
        assert intent(kind, instrument_id="MF:1", units_or_quantity=100).intent_type == kind


def test_intents_reject_live_mode_and_malformed_order_payloads():
    with pytest.raises(IntentError, match="paper-only"):
        intent(IntentType.EXCHANGE_MARKETABLE_SIMULATION, instrument_id="NSE:INFY",
               units_or_quantity=1, paper_only=False)
    with pytest.raises(IntentError, match="limit_price"):
        intent(IntentType.EXCHANGE_LIMIT, instrument_id="NSE:INFY", units_or_quantity=1)
    with pytest.raises(IntentError, match="NO_TRADE"):
        intent(IntentType.NO_TRADE, instrument_id="NSE:INFY")
