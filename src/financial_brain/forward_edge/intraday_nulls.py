"""Same-session time-of-day and side null for intraday strategy evaluation (FE-0085)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date


class IntradayNullError(ValueError):
    """Invalid intraday signal or null comparison input."""


@dataclass(frozen=True)
class IntradayReturn:
    observation_id: str
    session: date
    instrument_id: str
    minute_bin: int
    side: str
    after_cost_net_return: float
    role: str

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.instrument_id.strip():
            raise IntradayNullError("observation and instrument IDs are required")
        if type(self.minute_bin) is not int or not 0 <= self.minute_bin < 375:
            raise IntradayNullError("minute_bin must be in [0, 374] from regular-session open")
        if self.side not in {"BUY", "SELL"} or self.role not in {"SIGNAL", "NULL"}:
            raise IntradayNullError("side or observation role is invalid")
        if not math.isfinite(float(self.after_cost_net_return)):
            raise IntradayNullError("after-cost net return must be finite")


@dataclass(frozen=True)
class IntradayNullPair:
    signal_id: str
    matched_null_count: int
    signal_net_return: float
    mean_null_net_return: float
    incremental_net_return: float


@dataclass(frozen=True)
class IntradayNullReport:
    pairs: tuple[IntradayNullPair, ...]
    unmatched_signal_ids: tuple[str, ...]
    mean_incremental_net_return: float | None
    report_hash: str


def compare_intraday_time_side_null(observations: tuple[IntradayReturn, ...], *,
                                    minimum_null_count: int = 3) -> IntradayNullReport:
    """Compare each signal to non-signal instruments in the same date/time/side bin.

    Returns are expected to use the same after-cost convention. This is a descriptive
    null comparison; dependence and multiple testing are not adjusted here.
    """
    if type(minimum_null_count) is not int or minimum_null_count < 1:
        raise IntradayNullError("minimum_null_count must be a positive integer")
    ids = [row.observation_id for row in observations]
    if len(ids) != len(set(ids)):
        raise IntradayNullError("observation IDs must be unique")
    null_bins: dict[tuple[date, int, str], list[IntradayReturn]] = {}
    for row in observations:
        if row.role == "NULL":
            null_bins.setdefault((row.session, row.minute_bin, row.side), []).append(row)
    pairs: list[IntradayNullPair] = []
    unmatched: list[str] = []
    for signal in sorted((row for row in observations if row.role == "SIGNAL"),
                         key=lambda row: (row.session, row.minute_bin, row.observation_id)):
        peers = [row for row in null_bins.get((signal.session, signal.minute_bin, signal.side), [])
                 if row.instrument_id != signal.instrument_id]
        if len(peers) < minimum_null_count:
            unmatched.append(signal.observation_id)
            continue
        null_mean = sum(row.after_cost_net_return for row in peers) / len(peers)
        pairs.append(IntradayNullPair(signal.observation_id, len(peers), signal.after_cost_net_return,
                                      null_mean, signal.after_cost_net_return - null_mean))
    mean = sum(pair.incremental_net_return for pair in pairs) / len(pairs) if pairs else None
    body = {"pairs": [{"signal_id": pair.signal_id, "matched_null_count": pair.matched_null_count,
                       "signal_net_return": pair.signal_net_return,
                       "mean_null_net_return": pair.mean_null_net_return,
                       "incremental_net_return": pair.incremental_net_return} for pair in pairs],
            "unmatched_signal_ids": sorted(unmatched), "mean_incremental_net_return": mean}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return IntradayNullReport(tuple(pairs), tuple(sorted(unmatched)), mean, digest)
