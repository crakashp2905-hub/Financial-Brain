import pytest

from financial_brain.signals.quality import (CalendarEvidence, ExecutionQualityEvidence,
                                             QualityStatus, SignalQualityEvidence,
                                             assess_quality)

HASH = "c" * 64


def test_signal_execution_and_calendar_quality_are_reported_separately():
    result = assess_quality(
        SignalQualityEvidence(0.02, 0.03, 0.01, HASH),
        ExecutionQualityEvidence("BUY", 100, 101, HASH),
        CalendarEvidence("TRADING", "NSE:2026-11-23", HASH))
    assert result["signal_quality"]["forecast_error"] == pytest.approx(0.0)
    assert result["execution_quality"]["shortfall_bps"] == 100
    assert result["calendar_availability"]["session_state"] == "TRADING"
    assert result["composite_score"] is None


def test_missing_signal_or_calendar_inputs_are_explicit_and_no_fill_is_not_bad_execution():
    result = assess_quality(SignalQualityEvidence(None, None, None, None),
                            ExecutionQualityEvidence("SELL", 100, None, None),
                            CalendarEvidence(None, None, None))
    assert result["signal_quality"]["status"] == QualityStatus.MISSING_DATA
    assert result["execution_quality"]["status"] == QualityStatus.NO_FILL
    assert result["calendar_availability"]["status"] == QualityStatus.UNKNOWN


def test_sell_shortfall_sign_is_adverse_when_fill_is_below_reference():
    result = assess_quality(SignalQualityEvidence(None, None, None, None),
                            ExecutionQualityEvidence("SELL", 100, 99, HASH),
                            CalendarEvidence("CLOSED_HOLIDAY", "NSE:holiday", HASH))
    assert result["execution_quality"]["shortfall_bps"] == 100
