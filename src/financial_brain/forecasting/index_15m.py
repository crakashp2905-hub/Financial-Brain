"""Point-in-time 15-minute index research helpers.

The five-desk D2 implementation supplied the feature, leakage-check, purged-fold and
naive-baseline contracts. This module keeps those research utilities while delegating
market-bar construction, availability clocks and same-session labels to ``index_market``.
It does not fetch data or choose option contracts.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from financial_brain.forecasting.index_market import (
    BAR,
    IndexBar15m,
    IndexForwardTarget,
    IndexMarketError,
    IndexMinute,
    build_15m_bars,
    direction_label,
    forward_return,
    forward_targets,
    summarize_forward_distributions,
)

SeriesError = IndexMarketError
Bar = IndexBar15m


def bars_15m(minutes: list[IndexMinute], *, as_of: datetime) -> list[IndexBar15m]:
    """Compatibility name for the availability-aware complete-bar builder."""
    return build_15m_bars(minutes, as_of=as_of)


def completed_before(bars: list[IndexBar15m], decision_at: datetime) -> list[IndexBar15m]:
    """Return bars complete and available by the decision clock, within its session."""
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise SeriesError("decision time must be timezone-aware")
    cutoff = decision_at
    known = sorted((bar for bar in bars if bar.end <= cutoff and bar.available_at <= cutoff),
                   key=lambda bar: (bar.end, bar.start))
    if not known:
        return []
    latest_day = known[-1].end.date()
    return [bar for bar in known if bar.end.date() == latest_day]


def features_at(bars: list[IndexBar15m], decision_at: datetime,
                session_open: datetime) -> dict[str, float]:
    """Compute the five-desk feature set from available completed bars only.

    Return volatility only uses adjacent 15-minute bars; gaps and day boundaries do not
    become synthetic returns. The opening-range denominator uses a neutral 1.0 fallback
    for a zero-range first bar, matching the original D2 feature contract.
    """
    if session_open.tzinfo is None or session_open.utcoffset() is None:
        raise SeriesError("session open must be timezone-aware")
    known = completed_before(bars, decision_at)
    if len(known) < 2:
        raise SeriesError("not enough completed bars")
    last, previous = known[-1], known[-2]
    if last.end - previous.end != BAR or last.start - previous.start != BAR:
        raise SeriesError("latest completed bars are not contiguous")
    returns = [right.close / left.close - 1.0 for left, right in zip(known, known[1:])
               if right.end - left.end == BAR and right.start - left.start == BAR]
    if not returns:
        raise SeriesError("not enough contiguous returns")
    mean = sum(returns) / len(returns)
    volatility = (sum((value - mean) ** 2 for value in returns) / len(returns)) ** 0.5
    first = known[0]
    return {
        "ret_15m": last.close / previous.close - 1.0,
        "ret_session": last.close / first.open - 1.0,
        "range_last": (last.high - last.low) / last.close,
        "vol_session": volatility,
        "minutes_since_open": (decision_at - session_open).total_seconds() / 60.0,
        "opening_range_pos": (last.close - first.low) / ((first.high - first.low) or 1.0),
    }


def leak_check(bars: list[IndexBar15m], decision_at: datetime, session_open: datetime) -> bool:
    """Verify changing every unknown bar leaves currently available features unchanged."""
    baseline = features_at(bars, decision_at, session_open)
    shocked = []
    for bar in bars:
        known = bar.end <= decision_at and bar.available_at <= decision_at
        shocked.append(bar if known else replace(
            bar, open=bar.open * 3, high=bar.high * 3, low=bar.low * 0.1, close=bar.close * 5))
    return features_at(shocked, decision_at, session_open) == baseline


def noise_threshold(train_returns: list[float], quantile: float = 0.33) -> float:
    """Choose a FLAT-band threshold from training-period absolute returns only."""
    if not train_returns or not isfinite(quantile) or not 0 <= quantile <= 1:
        raise SeriesError("training returns and quantile in [0, 1] are required")
    if any(not isfinite(value) for value in train_returns):
        raise SeriesError("training returns must be finite")
    ordered = sorted(abs(value) for value in train_returns)
    threshold = ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]
    if threshold <= 0:
        raise SeriesError("training noise threshold must be positive")
    return threshold


def cost_aware_flat_band(train_returns: list[float], round_trip_cost_fraction: float,
                         quantile: float = 0.33) -> float:
    """Freeze a flat threshold at the larger of training noise and round-trip cost.

    Costs must be expressed as a fraction of underlying notional for the same intended
    round trip. Call once inside each training fold and reuse unchanged on validation/test.
    This is a label threshold, not proof that a strategy is profitable after costs.
    """
    if (not isfinite(round_trip_cost_fraction) or round_trip_cost_fraction < 0):
        raise SeriesError("round-trip cost fraction must be finite and nonnegative")
    return max(noise_threshold(train_returns, quantile), round_trip_cost_fraction)


def label(realized_return: float | None, threshold: float) -> str | None:
    """Compatibility label helper with a frozen positive threshold."""
    if realized_return is None:
        return None
    return direction_label(realized_return, noise_threshold=threshold)


def purged_splits(n: int, folds: int, label_steps: int, embargo: int = 1
                  ) -> list[tuple[list[int], list[int]]]:
    """Build expanding chronological folds purged by label horizon plus embargo."""
    if (type(n) is not int or type(folds) is not int or type(label_steps) is not int
            or type(embargo) is not int or n < folds or folds < 2
            or label_steps < 1 or embargo < 0):
        raise SeriesError("need valid sample count, folds, label horizon and embargo")
    size = n // folds
    result = []
    for fold in range(1, folds):
        test_start = fold * size
        test_end = min(n, (fold + 1) * size)
        test = list(range(test_start, test_end))
        gap = label_steps + embargo
        train = [index for index in range(test_start) if index + gap < test_start]
        if test and train:
            result.append((train, test))
    return result


def purged_time_splits(decision_times: list[datetime], label_end_times: list[datetime], *,
                       folds: int, embargo: timedelta = timedelta(0)
                       ) -> list[tuple[list[int], list[int]]]:
    """Build expanding folds using actual outcome-end clocks, keeping cross-sections intact.

    ``label_end_times[i]`` is the last source clock used to compute row ``i``'s target.
    Training labels must end strictly before the test decision boundary minus embargo.
    Repeated decision times form one indivisible test group, including for intraday bars.
    """
    if (type(folds) is not int or folds < 2 or len(decision_times) != len(label_end_times)
            or not isinstance(embargo, timedelta) or embargo < timedelta(0)):
        raise SeriesError("need aligned decision/label clocks, at least two folds and nonnegative embargo")
    if not decision_times:
        raise SeriesError("decision and label clocks cannot be empty")

    def to_utc(value: datetime, name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SeriesError(f"{name} timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    decisions = [to_utc(value, "decision") for value in decision_times]
    ends = [to_utc(value, "label end") for value in label_end_times]
    if decisions != sorted(decisions):
        raise SeriesError("decision timestamps must be chronological")
    if any(end < decision for decision, end in zip(decisions, ends)):
        raise SeriesError("label end cannot precede its decision timestamp")
    shared_ends: dict[datetime, set[datetime]] = {}
    for decision, end in zip(decisions, ends):
        shared_ends.setdefault(decision, set()).add(end)
    if any(len(values) != 1 for values in shared_ends.values()):
        raise SeriesError("shared decision cross-section must use one common label end")

    unique_decisions = list(dict.fromkeys(decisions))
    if len(unique_decisions) < folds:
        raise SeriesError("fold count cannot exceed unique decision timestamps")
    group_size = len(unique_decisions) // folds
    result: list[tuple[list[int], list[int]]] = []
    for fold in range(1, folds):
        test_start_index = fold * group_size
        test_end_index = min(len(unique_decisions), (fold + 1) * group_size)
        test_decisions = set(unique_decisions[test_start_index:test_end_index])
        if not test_decisions:
            continue
        cutoff = unique_decisions[test_start_index] - embargo
        test = [index for index, decision in enumerate(decisions) if decision in test_decisions]
        train = [index for index, (decision, end) in enumerate(zip(decisions, ends))
                 if decision < unique_decisions[test_start_index] and end < cutoff]
        if train and test:
            result.append((train, test))
    return result


def frequency_baseline(train_labels: list[str]) -> Callable[[], dict[str, float]]:
    """Return a deterministic UP/DOWN/FLAT class-frequency baseline."""
    counts = Counter(value for value in train_labels if value in {"UP", "DOWN", "FLAT"})
    total = sum(counts.values())
    if not total:
        raise SeriesError("training labels contain no supported classes")
    probabilities = {name: counts.get(name, 0) / total for name in ("UP", "DOWN", "FLAT")}
    return lambda: dict(probabilities)


def _deterministic_forecast(method: str, expected_return: float, flat_band: float,
                            predicted_label: str) -> dict[str, float | str]:
    probabilities = {name: float(name == predicted_label) for name in ("UP", "DOWN", "FLAT")}
    return {"method": method, "expected_return": expected_return,
            "flat_band": flat_band, "predicted_label": predicted_label,
            "p_up": probabilities["UP"], "p_down": probabilities["DOWN"],
            "p_flat": probabilities["FLAT"]}


def random_walk_baseline(flat_band: float) -> dict[str, float | str]:
    """Zero expected price change, classified with a training-frozen FLAT band."""
    if not isfinite(flat_band) or flat_band <= 0:
        raise SeriesError("random-walk baseline needs a positive finite flat band")
    return _deterministic_forecast("RANDOM_WALK", 0.0, flat_band,
                                   label(0.0, flat_band) or "FLAT")


def volatility_aware_no_direction_baseline(
        train_returns: list[float], round_trip_cost_fraction: float) -> dict[str, float | str]:
    """Always abstain directionally; set its expected-move band from training volatility and cost."""
    band = cost_aware_flat_band(train_returns, round_trip_cost_fraction)
    variance = sum(value * value for value in train_returns) / len(train_returns)
    volatility = variance ** 0.5
    band = max(band, volatility)
    forecast = _deterministic_forecast("VOLATILITY_AWARE_NO_DIRECTION", 0.0, band, "FLAT")
    forecast["expected_move_abs"] = volatility
    return forecast


def previous_bar_baseline(previous_return: float, flat_band: float) -> dict[str, float | str]:
    """Repeat the previous bar's return only when it clears the frozen FLAT band."""
    if not isfinite(previous_return) or not isfinite(flat_band) or flat_band <= 0:
        raise SeriesError("previous-bar baseline needs finite return and positive flat band")
    predicted = previous_return if abs(previous_return) > flat_band else 0.0
    predicted_label = label(predicted, flat_band) or "FLAT"
    return _deterministic_forecast("PREVIOUS_BAR", predicted, flat_band, predicted_label)


def same_time_of_day_baseline(
        train_labels_by_bucket: dict[str, list[str]], train_labels: list[str]
        ) -> Callable[[str], dict[str, float]]:
    """Use training-only class frequencies for each time bucket, with global fallback."""
    if not train_labels:
        raise SeriesError("same-time-of-day baseline needs training labels")
    fallback = frequency_baseline(train_labels)()
    by_bucket = {bucket: frequency_baseline(labels)()
                 for bucket, labels in train_labels_by_bucket.items() if labels}

    def predict(bucket: str) -> dict[str, float]:
        if not bucket:
            raise SeriesError("time-of-day bucket is required")
        return dict(by_bucket.get(bucket, fallback))

    return predict


def session_regimes(decision_at: datetime, *, macro_event: bool | None,
                    expiry_day: bool | None, option_spread_fraction: float | None,
                    normal_spread_limit: float | None) -> tuple[str, ...]:
    """Tag session, event, expiry and option-spread regimes from point-in-time inputs.

    Spread limits must be calibrated from prior/training observations. ``None`` inputs
    remain explicit unknowns rather than silently being treated as normal conditions.
    """
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise SeriesError("regime decision timestamp must be timezone-aware")
    local = decision_at.astimezone(ZoneInfo("Asia/Kolkata"))
    current = local.time()
    if current < time(9, 15) or current >= time(15, 30):
        raise SeriesError("regime decision timestamp must be inside the regular NSE session")
    if macro_event not in (True, False, None) or expiry_day not in (True, False, None):
        raise SeriesError("event and expiry inputs must be true, false or unknown")
    if (option_spread_fraction is None) != (normal_spread_limit is None):
        raise SeriesError("option spread and its prior-data limit must be supplied together")
    if option_spread_fraction is not None:
        if (not isfinite(option_spread_fraction) or option_spread_fraction < 0
                or normal_spread_limit is None or not isfinite(normal_spread_limit)
                or normal_spread_limit <= 0):
            raise SeriesError("spread fraction must be nonnegative and its limit positive")

    regimes = []
    if current < time(9, 30):
        regimes.append("FIRST_15_MINUTES")
    elif time(11, 30) <= current < time(13, 30):
        regimes.append("MIDDAY")
    elif current >= time(15, 0):
        regimes.append("PRE_CLOSE")
    else:
        regimes.append("REGULAR_SESSION")
    regimes.append("MACRO_EVENT" if macro_event else
                    "MACRO_EVENT_UNKNOWN" if macro_event is None else "NO_KNOWN_MACRO_EVENT")
    regimes.append("EXPIRY_DAY" if expiry_day else
                    "EXPIRY_STATUS_UNKNOWN" if expiry_day is None else "NON_EXPIRY_DAY")
    if option_spread_fraction is None:
        regimes.append("OPTION_SPREAD_UNKNOWN")
    elif normal_spread_limit is None:  # guarded by the paired-input validation above
        raise SeriesError("option spread limit is missing")
    elif option_spread_fraction > normal_spread_limit:
        regimes.append("UNUSUAL_OPTION_SPREAD")
    else:
        regimes.append("NORMAL_OPTION_SPREAD")
    return tuple(regimes)


def check_probabilities(probabilities: dict[str, float], tolerance: float = 1e-9) -> None:
    if (set(probabilities) != {"UP", "DOWN", "FLAT"}
            or any(not isfinite(value) or value < 0 or value > 1 for value in probabilities.values())
            or not isfinite(tolerance) or tolerance < 0
            or abs(sum(probabilities.values()) - 1.0) > tolerance):
        raise SeriesError("probabilities must cover UP/DOWN/FLAT, stay in [0,1], and sum to one")


def brier(probabilities: dict[str, float], actual: str) -> float:
    check_probabilities(probabilities)
    if actual not in {"UP", "DOWN", "FLAT"}:
        raise SeriesError("actual label must be UP, DOWN or FLAT")
    return sum((probabilities[name] - float(name == actual)) ** 2 for name in probabilities)


__all__ = [
    "Bar", "IndexForwardTarget", "SeriesError", "bars_15m", "brier", "check_probabilities", "completed_before",
    "features_at", "forward_return", "forward_targets", "frequency_baseline", "label", "leak_check",
    "summarize_forward_distributions",
    "cost_aware_flat_band", "noise_threshold", "previous_bar_baseline", "random_walk_baseline",
    "same_time_of_day_baseline", "volatility_aware_no_direction_baseline",
    "session_regimes",
    "purged_splits", "purged_time_splits",
]
