from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.historical_divergence import (
    DivergenceError, compare_live_ticks_to_historical_bar,
)
from financial_brain.forward_edge.market_data import NormalizedEvent


DAY = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _tick(offset_seconds, price):
    at = DAY + timedelta(seconds=offset_seconds)
    return NormalizedEvent("TICK", "NSE:INFY", at, at + timedelta(milliseconds=1),
                           at + timedelta(milliseconds=2), (("price", str(price)),))


def _bar(*, fetched=DAY + timedelta(days=1), values=None, source="kite_historical"):
    payload = {"source": source, "interval": "minute", "open": "100", "high": "101",
               "low": "99", "close": "100"}
    payload.update(values or {})
    return NormalizedEvent("BAR", "NSE:INFY", DAY, fetched - timedelta(seconds=1), fetched,
                           tuple(sorted(payload.items())))


def test_historical_divergence_is_logged_unverified_and_never_adjusted(tmp_path):
    journal = DurableJournal(tmp_path / "divergence.jsonl")
    ticks = [_tick(1, 100), _tick(2, 102), _tick(3, 98), _tick(4, 101), _tick(61, 999)]

    report = compare_live_ticks_to_historical_bar(
        ticks=ticks, bar=_bar(values={"high": "100", "low": "100"}),
        live_source="kite_quote_capture", tolerance_bps=Decimal("1"), audit_journal=journal,
    )
    replay = compare_live_ticks_to_historical_bar(
        ticks=ticks, bar=_bar(values={"high": "100", "low": "100"}),
        live_source="kite_quote_capture", tolerance_bps=Decimal("1"), audit_journal=journal,
    )

    assert report["comparison_status"] == "DIVERGENT"
    assert report["verification_status"] == "UNVERIFIED"
    assert report["adjustment_applied"] is False
    assert report["tick_count"] == 4
    assert report["metrics"]["high"]["live_tick_value"] == "102"
    assert replay == report
    assert len(journal.records()) == 1
    assert journal.records()[0]["t"] == "LIVE_HISTORICAL_DIVERGENCE"


def test_historical_bar_match_is_still_unverified_until_adjudicated(tmp_path):
    report = compare_live_ticks_to_historical_bar(
        ticks=[_tick(1, 100), _tick(2, 101), _tick(3, 99), _tick(4, 100)],
        bar=_bar(), live_source="kite_capture", tolerance_bps=Decimal("0"),
        audit_journal=DurableJournal(tmp_path / "match.jsonl"),
    )
    assert report["comparison_status"] == "MATCH_WITHIN_TOLERANCE"
    assert report["verification_status"] == "UNVERIFIED"
    assert report["adjustment_applied"] is False


@pytest.mark.parametrize("bar", [
    _bar(fetched=DAY + timedelta(hours=1)),
    _bar(source="unknown_source"),
])
def test_divergence_comparison_fails_closed_on_same_day_or_unknown_historical_source(tmp_path, bar):
    with pytest.raises(DivergenceError):
        compare_live_ticks_to_historical_bar(
            ticks=[_tick(1, 100)], bar=bar, live_source="kite_capture",
            tolerance_bps=Decimal("0"), audit_journal=DurableJournal(tmp_path / "reject.jsonl"),
        )
