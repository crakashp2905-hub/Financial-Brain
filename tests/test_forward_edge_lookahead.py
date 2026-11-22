"""FE-0043 point-in-time hashes and future-perturbation invariance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pandas as pd

from financial_brain.evaluation.momentum_book import choose
from financial_brain.forward_edge.lookahead import (
    AvailableInput,
    LookaheadError,
    assert_prefix_invariant,
    validate_point_in_time,
)
from financial_brain.kernel.data_quality import Revision, Revisions

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


def test_future_published_revision_cannot_change_prior_features_signals_or_book_ranking() -> None:
    decision = NOW
    correction_published = NOW + timedelta(days=5)
    store = Revisions()
    for key, value in (("A:prior", 100.0), ("A:current", 110.0),
                       ("B:prior", 100.0), ("B:current", 105.0)):
        store.add(Revision(key, value, NOW - timedelta(days=1), NOW - timedelta(days=1)))

    def decision_output(as_of: datetime) -> tuple[dict[str, float], dict[str, str], list[str]]:
        features: dict[str, float] = {}
        for symbol in ("A", "B"):
            prior = store.as_of(f"{symbol}:prior", as_of)
            current = store.as_of(f"{symbol}:current", as_of)
            assert prior is not None and current is not None
            features[symbol] = current.value / prior.value - 1.0
        signals = {symbol: "LONG" if score > 0 else "NO_TRADE" for symbol, score in features.items()}
        eligible = {symbol: score for symbol, score in features.items() if signals[symbol] == "LONG"}
        ranking = choose(pd.Series(eligible, dtype=float), [], enter=1, keep=2)
        return features, signals, ranking

    before_revision_exists = decision_output(decision)
    store.add(Revision("A:current", 90.0, correction_published, correction_published))

    # The newly appended correction must not affect an already-issued decision.
    assert decision_output(decision) == before_revision_exists
    # It becomes visible only after publication, proving this was a meaningful perturbation.
    after_publication = decision_output(correction_published)
    assert after_publication[0]["A"] == pytest.approx(-0.1)
    assert after_publication[1]["A"] == "NO_TRADE"
    assert after_publication[2] == ["B"]
