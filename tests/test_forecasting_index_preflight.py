from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from financial_brain.forecasting.index_market import IndexBar15m
from financial_brain.forecasting.index_preflight import index_forecast_preflight

IST = ZoneInfo("Asia/Kolkata")
DECISION = datetime(2026, 11, 25, 10, 0, 5, tzinfo=IST)
BAR = IndexBar15m(datetime(2026, 11, 25, 9, 45, tzinfo=IST),
                  datetime(2026, 11, 25, 10, 0, tzinfo=IST),
                  datetime(2026, 11, 25, 10, 0, 2, tzinfo=IST),
                  100, 102, 99, 101, 15)


def preflight(**changes):
    inputs = {"probabilities": {"UP": .6, "DOWN": .2, "FLAT": .2},
              "bar": BAR, "decision_at": DECISION,
              "quote_observed_at": DECISION - timedelta(seconds=2),
              "quote_available_at": DECISION - timedelta(seconds=1),
              "max_quote_age": timedelta(seconds=5),
              "event_calendar_available_at": DECISION - timedelta(hours=1),
              "event_calendar_source_sha256": "a" * 64}
    inputs.update(changes)
    return index_forecast_preflight(**inputs)


def test_forecast_preflight_accepts_only_causal_complete_fresh_inputs():
    result = preflight()
    assert result["status"] == "READY"
    assert result["reason_codes"] == ()
    assert result["quote_age_ms"] == 1000
    assert result["paper_only"] is True


def test_preflight_fails_closed_for_invalid_probabilities_lag_partial_bars_and_events():
    partial = IndexBar15m(BAR.start, BAR.end, BAR.available_at, 100, 102, 99, 101, 14)
    result = preflight(probabilities={"UP": .8, "DOWN": .4, "FLAT": .2},
                       bar=partial, quote_available_at=DECISION - timedelta(seconds=30),
                       quote_observed_at=DECISION - timedelta(seconds=31),
                       event_calendar_available_at=None, event_calendar_source_sha256=None)
    assert result["status"] == "NO_TRADE"
    assert {"IMPOSSIBLE_PROBABILITIES", "PARTIAL_FORMING_OR_FUTURE_BAR",
            "QUOTE_LAG_EXCEEDS_POLICY", "PUBLISHED_EVENTS_UNAVAILABLE"}.issubset(
                set(result["reason_codes"]))


def test_preflight_rejects_future_or_naive_source_clocks():
    result = preflight(quote_available_at=DECISION + timedelta(seconds=1))
    assert "QUOTE_SOURCE_CLOCK_NONCAUSAL" in result["reason_codes"]
    result = preflight(event_calendar_available_at=DECISION + timedelta(seconds=1))
    assert "PUBLISHED_EVENTS_UNAVAILABLE_AT_DECISION" in result["reason_codes"]
    result = preflight(quote_observed_at=DECISION.replace(tzinfo=None))
    assert "QUOTE_SOURCE_CLOCK_MISSING" in result["reason_codes"]
