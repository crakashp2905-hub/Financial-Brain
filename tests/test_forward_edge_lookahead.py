"""FE-0043 point-in-time hashes and future-perturbation invariance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.lookahead import (
    AvailableInput,
    LookaheadError,
    assert_prefix_invariant,
    validate_point_in_time,
)

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def test_point_in_time_inputs_are_hashable_only_when_available() -> None:
    items = [AvailableInput("bars", "a" * 64, NOW - timedelta(seconds=1)),
             AvailableInput("calendar", "b" * 64, NOW)]
    first = validate_point_in_time(items, NOW)
    assert first == validate_point_in_time(list(reversed(items)), NOW)
    with pytest.raises(LookaheadError, match="unavailable"):
        validate_point_in_time(items + [AvailableInput("filing", "c" * 64, NOW + timedelta(microseconds=1))], NOW)


def test_lookahead_contract_rejects_naive_and_duplicate_inputs() -> None:
    with pytest.raises(LookaheadError, match="timezone aware"):
        validate_point_in_time([AvailableInput("bars", "a" * 64, NOW)], datetime(2026, 11, 15))
    with pytest.raises(LookaheadError, match="duplicate"):
        validate_point_in_time([AvailableInput("bars", "a" * 64, NOW),
                                AvailableInput("bars", "b" * 64, NOW)], NOW)


def test_prefix_output_survives_future_perturbations_and_detects_leakage() -> None:
    values = [1, 2, 3, 4, 5]
    causal = lambda xs: [sum(xs[:i + 1]) for i in range(len(xs))]
    assert_prefix_invariant(values, causal, split=3, perturbation=[400, -500])
    leaky = lambda xs: [x + xs[-1] for x in xs]
    with pytest.raises(LookaheadError, match="future data perturbation"):
        assert_prefix_invariant(values, leaky, split=3, perturbation=[400, -500])
