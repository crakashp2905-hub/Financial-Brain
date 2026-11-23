from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.availability import (
    AvailabilityError,
    latest_input_available_at,
    require_inputs_available,
)


NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


class _Input:
    def __init__(self, event_time, available_at):
        self.event_time = event_time
        self.available_at = available_at


def test_decision_availability_uses_latest_of_all_input_availability_clocks():
    early = _Input(event_time=NOW, available_at=NOW + timedelta(seconds=1))
    late = _Input(event_time=NOW - timedelta(days=1), available_at=NOW + timedelta(seconds=3))
    assert latest_input_available_at([early, late]) == NOW + timedelta(seconds=3)
    assert require_inputs_available([early, late], NOW + timedelta(seconds=3)) == NOW + timedelta(seconds=3)


def test_decision_availability_rejects_early_decisions_empty_inputs_and_naive_clocks():
    late = _Input(event_time=NOW, available_at=NOW + timedelta(seconds=1))
    with pytest.raises(AvailabilityError, match="precedes the latest input availability"):
        require_inputs_available([late], NOW)
    with pytest.raises(AvailabilityError, match="at least one"):
        latest_input_available_at([])
    with pytest.raises(AvailabilityError, match="timezone-aware"):
        latest_input_available_at([datetime(2026, 11, 20, 10, 0)])
