from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.signals.deduplicate import AlertKey, DedupeError, suppress

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def alert(version="v1", price=100, seconds=0):
    return AlertKey("momentum", version, "NSE:INFY", "BUY",
                    NOW + timedelta(seconds=seconds), price)


def test_repeat_alert_suppressed_but_new_version_or_material_price_change_survives():
    prior = [alert()]
    assert suppress(alert(price=100.5, seconds=10), prior, window=timedelta(minutes=5),
                    material_price_change_pct=1)[0]
    assert not suppress(alert("v2", seconds=10), prior, window=timedelta(minutes=5),
                        material_price_change_pct=1)[0]
    assert not suppress(alert(price=103, seconds=10), prior, window=timedelta(minutes=5),
                        material_price_change_pct=1)[0]


def test_expired_and_out_of_order_alert_handling():
    prior = [alert()]
    assert not suppress(alert(seconds=600), prior, window=timedelta(minutes=5),
                        material_price_change_pct=1)[0]
    with pytest.raises(DedupeError, match="precedes"):
        suppress(alert(seconds=-1), prior, window=timedelta(minutes=5),
                 material_price_change_pct=1)
