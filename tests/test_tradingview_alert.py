import json
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.interfaces.tradingview_alert import AlertRejected, parse_alert


NOW = datetime(2026, 10, 30, 4, 30, tzinfo=timezone.utc)
BASE = {"alert_id": "tv-123", "strategy": "paper-momentum-v1", "exchange": "NSE",
        "symbol": "INFY", "direction": "UP", "event_time": NOW.isoformat(),
        "reference_price": 1500.5}


def parse(payload):
    return parse_alert(json.dumps(payload).encode(), received_time=NOW,
                       allowed_strategies=frozenset({"paper-momentum-v1"}))


def test_valid_observation_has_no_order_instruction():
    alert = parse(BASE)
    assert alert.symbol == "INFY" and alert.direction == "UP"
    assert not hasattr(alert, "quantity")
    assert not hasattr(alert, "order_type")


@pytest.mark.parametrize("change", [
    {"strategy": "unknown"}, {"direction": "BUY"}, {"exchange": "FAKE"},
    {"reference_price": 0}, {"reference_price": float("inf")},
    {"event_time": (NOW - timedelta(minutes=3)).isoformat()},
    {"event_time": (NOW + timedelta(minutes=1)).isoformat()},
    {"event_time": "2026-10-30T10:00:00"}, {"quantity": 100},
])
def test_rejects_unsafe_or_unknown_payload(change):
    with pytest.raises(AlertRejected):
        parse({**BASE, **change})


def test_rejects_oversized_payload():
    with pytest.raises(AlertRejected):
        parse_alert(b" " * 4097, received_time=NOW,
                    allowed_strategies=frozenset({"paper-momentum-v1"}))
