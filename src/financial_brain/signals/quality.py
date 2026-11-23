"""Separate signal forecast, execution shortfall and exchange-calendar evidence."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

_HASH = re.compile(r"^[0-9a-f]{64}$")


class QualityStatus(StrEnum):
    MEASURED = "MEASURED"
    MISSING_DATA = "MISSING_DATA"
    NO_FILL = "NO_FILL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SignalQualityEvidence:
    predicted_excess_return: float | None
    realized_return: float | None
    benchmark_return: float | None
    source_sha256: str | None


@dataclass(frozen=True, slots=True)
class ExecutionQualityEvidence:
    side: str
    reference_price: float | None
    fill_price: float | None
    fill_sha256: str | None


@dataclass(frozen=True, slots=True)
class CalendarEvidence:
    session_state: str | None
    session_id: str | None
    calendar_sha256: str | None


def _valid_hash(value: str | None) -> bool:
    return value is not None and _HASH.fullmatch(value.lower()) is not None


def assess_quality(signal: SignalQualityEvidence, execution: ExecutionQualityEvidence,
                   calendar: CalendarEvidence) -> dict:
    """Return three independent evidence dimensions; deliberately no composite score."""
    if execution.side not in {"BUY", "SELL"}:
        raise ValueError("execution side must be BUY or SELL")
    sig_names = ("predicted_excess_return", "realized_return", "benchmark_return")
    sig_values = (signal.predicted_excess_return, signal.realized_return, signal.benchmark_return)
    if all(value is not None and math.isfinite(value) for value in sig_values) \
            and _valid_hash(signal.source_sha256):
        signal_result = {"status": QualityStatus.MEASURED.value,
                         "forecast_error": signal.realized_return - signal.benchmark_return
                         - signal.predicted_excess_return,
                         "source_sha256": signal.source_sha256.lower(), "missing": []}
    else:
        missing = [name for name, value in zip(sig_names, sig_values)
                   if value is None or not math.isfinite(value)]
        if not _valid_hash(signal.source_sha256):
            missing.append("source_sha256")
        signal_result = {"status": QualityStatus.MISSING_DATA.value,
                         "forecast_error": None, "source_sha256": None, "missing": missing}

    if execution.fill_price is None:
        execution_result = {"status": QualityStatus.NO_FILL.value,
                            "shortfall_bps": None, "missing": []}
    elif (execution.reference_price is None or execution.fill_price <= 0
          or execution.reference_price <= 0 or not math.isfinite(execution.fill_price)
          or not math.isfinite(execution.reference_price) or not _valid_hash(execution.fill_sha256)):
        missing = []
        if (execution.reference_price is None or not math.isfinite(execution.reference_price)
                or execution.reference_price <= 0):
            missing.append("reference_price")
        if not math.isfinite(execution.fill_price) or execution.fill_price <= 0:
            missing.append("fill_price")
        if not _valid_hash(execution.fill_sha256):
            missing.append("fill_sha256")
        execution_result = {"status": QualityStatus.MISSING_DATA.value,
                            "shortfall_bps": None, "missing": missing}
    else:
        sign = 1.0 if execution.side == "BUY" else -1.0
        shortfall = sign * (execution.fill_price - execution.reference_price) \
            / execution.reference_price * 10_000
        execution_result = {"status": QualityStatus.MEASURED.value,
                            "shortfall_bps": shortfall,
                            "fill_sha256": execution.fill_sha256.lower(), "missing": []}

    state = (calendar.session_state or "").upper()
    if state not in {"TRADING", "CLOSED_HOLIDAY", "CLOSED_WEEKEND", "SPECIAL_SESSION"} \
            or not calendar.session_id or not _valid_hash(calendar.calendar_sha256):
        missing = []
        if state not in {"TRADING", "CLOSED_HOLIDAY", "CLOSED_WEEKEND", "SPECIAL_SESSION"}:
            missing.append("session_state")
        if not calendar.session_id:
            missing.append("session_id")
        if not _valid_hash(calendar.calendar_sha256):
            missing.append("calendar_sha256")
        calendar_result = {"status": QualityStatus.UNKNOWN.value, "session_state": None,
                           "missing": missing}
    else:
        calendar_result = {"status": QualityStatus.MEASURED.value,
                           "session_state": state, "session_id": calendar.session_id,
                           "calendar_sha256": calendar.calendar_sha256.lower(), "missing": []}
    return {"signal_quality": signal_result, "execution_quality": execution_result,
            "calendar_availability": calendar_result, "composite_score": None}
