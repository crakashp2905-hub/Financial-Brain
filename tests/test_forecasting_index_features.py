from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_features import (
    DerivativeMicrostructure,
    IndexFeatureError,
    causal_index_features,
)
from financial_brain.forecasting.index_market import IndexBar15m

IST = ZoneInfo("Asia/Kolkata")
END = datetime(2026, 11, 25, 10, 0, tzinfo=IST)
DECISION = END + timedelta(minutes=1)


def bars(prices, *, delayed_last=False):
    result = []
    start = END - timedelta(minutes=15)
    for offset, close in enumerate(prices):
        end = start + timedelta(minutes=15 * offset)
        available = end + timedelta(seconds=2)
        if delayed_last and offset == len(prices) - 1:
            available += timedelta(minutes=2)
        result.append(IndexBar15m(end - timedelta(minutes=15), end, available,
                                  close, close, close, close))
    return result


def test_cross_index_features_require_same_available_bar_clock_and_are_causal():
    inputs = {"NIFTY": bars([100, 101]), "BANK": bars([200, 204]),
              "MIDCAP": bars([300, 303], delayed_last=True)}
    result = causal_index_features(inputs, target_index="NIFTY",
                                   sector_indices=["BANK", "MIDCAP"], decision_at=DECISION)
    assert result["target_return_15m"] == pytest.approx(.01)
    assert result["sector_relative_returns_15m"]["BANK"] == pytest.approx(.01)
    assert result["index_returns_15m"]["MIDCAP"] is None
    assert result["sector_relative_returns_15m"]["MIDCAP"] is None


def test_derivatives_require_rights_evidence_and_are_hidden_until_available():
    item = DerivativeMicrostructure("Kite options quote", "a" * 64,
                                    END - timedelta(seconds=2), END + timedelta(minutes=2),
                                    spread_fraction=.02, volume=10, open_interest=100)
    result = causal_index_features({"NIFTY": bars([100, 101])}, target_index="NIFTY",
                                   sector_indices=[], decision_at=DECISION, derivatives=item)
    assert result["derivatives"]["source_id"] is None
    available = DerivativeMicrostructure("Kite options quote", "a" * 64,
                                         END - timedelta(seconds=2), DECISION,
                                         spread_fraction=.02, volume=10, open_interest=100)
    result = causal_index_features({"NIFTY": bars([100, 101])}, target_index="NIFTY",
                                   sector_indices=[], decision_at=DECISION, derivatives=available)
    assert result["derivatives"]["spread_fraction"] == .02
    with pytest.raises(IndexFeatureError, match="rights evidence"):
        DerivativeMicrostructure("unverified", "bad", END, END)


def test_cross_index_features_reject_missing_target_and_naive_decision_clock():
    with pytest.raises(IndexFeatureError, match="target index series"):
        causal_index_features({}, target_index="NIFTY", sector_indices=[], decision_at=END)
    with pytest.raises(IndexFeatureError, match="timezone-aware"):
        causal_index_features({"NIFTY": bars([100, 101])}, target_index="NIFTY",
                              sector_indices=[], decision_at=DECISION.replace(tzinfo=None))
