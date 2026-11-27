"""Observed instrument/day/size displayed-depth impact summaries."""
from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.impact_calibration import ImpactCalibrationError, estimate_displayed_impact
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive

T0 = datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc)


def event(index: int, *, source: str = "kite:observed", depth: int = 50) -> MarketEvent:
    at = T0.replace(minute=index)
    return MarketEvent(
        event_id=f"impact-{index}", kind=EventKind.QUOTE, venue="NSE", instrument_id="NSE:ABC",
        event_time=at, received_at=at, available_at=at,
        payload={"bid": 99.9, "ask": 100.1, "volume": 1000,
                 "bid_levels": [[99.9, depth], [99.8, depth]],
                 "ask_levels": [[100.1, depth], [100.2, depth]]},
        source=source, evidence_id="observed-session",
    )


def test_estimate_is_grouped_by_instrument_session_size_and_hashes_inputs(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        for index in range(20):
            archive.append(event(index))
        small, large = estimate_displayed_impact(
            archive, instrument_id="NSE:ABC", session_date=T0.date(), quantities=(10, 80),
            participation_bucket="LOW", minimum_observations=20)
        assert small.quantity == 10 and large.quantity == 80
        assert small.session_date == T0.date()
        assert small.instrument_id == "NSE:ABC"
        assert small.observations == 20
        assert small.median_round_trip_bps < large.median_round_trip_bps
        assert len(small.event_hashes) == 20
        assert small.calibrated is True
    finally:
        archive.close()


def test_fixture_observations_cannot_claim_calibration_and_sparse_data_refuses(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        for index in range(5):
            archive.append(event(index, source="kite-fixture"))
        with pytest.raises(ImpactCalibrationError, match="5 usable observations"):
            estimate_displayed_impact(archive, instrument_id="NSE:ABC", session_date=T0.date(),
                                      quantities=(10,), participation_bucket="LOW")
    finally:
        archive.close()


def test_enough_fixture_data_remains_uncalibrated(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        for index in range(20):
            archive.append(event(index, source="kite-fixture"))
        result, = estimate_displayed_impact(archive, instrument_id="NSE:ABC", session_date=T0.date(),
                                            quantities=(10,), participation_bucket="LOW")
        assert result.calibrated is False
    finally:
        archive.close()
