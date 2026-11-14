from __future__ import annotations

import pytest

from financial_brain.forward_edge import cockpit


def test_opportunity_counts_preserve_reasons_and_all_planned_dates():
    html = cockpit.opportunity_coverage(
        scheduled_dates=["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"],
        outcomes=[
            {"event_id": "r1", "session_date": "2026-11-09", "outcome": "REJECTED", "reason_code": "COSTS_EXCEED_ALPHA"},
            {"event_id": "r2", "session_date": "2026-11-10", "outcome": "REJECTED", "reason_code": "SPREAD_TOO_WIDE"},
            {"event_id": "a1", "session_date": "2026-11-10", "outcome": "ABSTAINED", "reason_code": "UNCERTAINTY_HIGH"},
            {"event_id": "m1", "session_date": "2026-11-11", "outcome": "MISSED", "reason_code": "NO_FILL"},
            {"event_id": "n1", "session_date": "2026-11-12", "outcome": "NO_SIGNAL", "reason_code": "NO_SETUP"},
        ])
    assert "Planned session denominator: 5 dates" in html
    assert "Unrecorded dates: 1" in html
    assert "<th>Rejected</th><td>2</td>" in html
    assert "<th>Abstained</th><td>1</td>" in html
    assert "<th>Missed</th><td>1</td>" in html
    assert "<th>No Signal</th><td>1</td>" in html
    assert "NO_RECORDED_OUTCOME" in html and "COSTS_EXCEED_ALPHA" in html


@pytest.mark.parametrize("events, message", [
    ([{"event_id": "x", "session_date": "2026-11-14", "outcome": "MISSED", "reason_code": "NO_FILL"}],
     "outside the planned"),
    ([{"event_id": "x", "session_date": "2026-11-13", "outcome": "MISSED", "reason_code": "NO_FILL"},
      {"event_id": "x", "session_date": "2026-11-13", "outcome": "REJECTED", "reason_code": "COST"}],
     "unique"),
    ([{"event_id": "x", "session_date": "2026-11-13", "outcome": "REJECTED", "reason_code": " "}],
     "reason code"),
])
def test_invalid_or_ungoverned_opportunity_events_are_rejected(events, message):
    with pytest.raises(cockpit.CockpitError, match=message):
        cockpit.opportunity_coverage(scheduled_dates=["2026-11-13"], outcomes=events)
