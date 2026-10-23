from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from financial_brain.evaluation.script_mechanisms import (
    daily_signals, liquidity_counterfactual, liquidity_no_trade, training_outcomes,
)


def _bars():
    dates = pd.bdate_range("2020-01-01", periods=90)
    close = [100.0] * 60 + [100.0] * 10 + [101.0] + [100.0] * 19
    return pd.DataFrame({"session": dates, "open": close, "high": close,
                         "low": [v - 0.2 for v in close], "close": close,
                         "volume": [1000] * 70 + [2000] + [1000] * 19})


def test_daily_signals_use_prior_regime_and_completed_history_only():
    bars = _bars()
    regime = pd.Series(["SIDEWAYS"] * 70 + ["BULL"] * 20, index=bars["session"])
    signals = daily_signals(bars, regime)
    # Today's BULL is unknown until after the breakout; it cannot approve it.
    assert signals.loc[70, "donchian_baseline"]
    assert not signals.loc[70, "regime_breakout"]
    assert not signals.loc[69, "donchian_baseline"]
    assert not signals.loc[:54, "donchian_baseline"].any()


def test_compression_requires_a_prior_narrow_range_and_volume():
    bars = _bars()
    bars.loc[:59, "high"] = 101.0
    bars.loc[:59, "low"] = 99.0
    bars.loc[60:69, "low"] = 99.95
    bars.loc[70, ["open", "high", "close"]] = 102.0
    regime = pd.Series(["SIDEWAYS"] * len(bars), index=bars["session"])
    signals = daily_signals(bars, regime)
    assert signals.loc[70, "compression_breakout"]
    bars.loc[70, "volume"] = 500
    assert not daily_signals(bars, regime).loc[70, "compression_breakout"]


def test_training_diagnostic_uses_next_open_and_counts_missing_exits():
    bars = _bars()
    regime = pd.Series(["BULL"] * len(bars), index=bars["session"])
    signals = daily_signals(bars, regime)
    result = training_outcomes(bars, signals)
    assert result["regime_breakout"]["selected"] == 1
    assert result["regime_breakout"]["priced"] == 1
    assert result["regime_breakout"]["mean_net"] == pytest.approx(-0.002)
    assert result["regime_filter_counterfactual"]["skipped"] == 0
    assert result["status"] == "TRAINING_DESCRIPTION_ONLY_NOT_A_TRIAL"


def test_training_rejects_holdout_dates():
    bars = _bars()
    bars.loc[89, "session"] = pd.Timestamp(date(2022, 1, 3))
    with pytest.raises(ValueError, match="restricted to training"):
        daily_signals(bars, pd.Series(["BULL"] * len(bars), index=bars["session"]))


def test_liquidity_gate_fails_closed_and_walks_visible_depth():
    now = datetime(2020, 6, 1, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    quote = {"timestamp": now, "depth": {"buy": [{"price": 99.95, "quantity": 100}],
                                           "sell": [{"price": 100.05, "quantity": 50},
                                                    {"price": 100.10, "quantity": 50}]}}
    good = liquidity_no_trade(quote, now=now, side="BUY", quantity=100)
    assert good["allow"] and good["visible_fill_price"] == pytest.approx(100.075)
    assert liquidity_no_trade(quote, now=now, side="BUY", quantity=101)["reason"] == "INSUFFICIENT_VISIBLE_DEPTH"
    assert liquidity_no_trade(quote, now=now + timedelta(minutes=3), side="BUY", quantity=10)["reason"] == "STALE_OR_MISSING_QUOTE"
    assert liquidity_no_trade(quote, now=now, side="BUY", quantity=100, max_spread_bps=5)["reason"] == "WIDE_SPREAD"


def test_liquidity_filter_counts_missed_winner_not_only_avoided_loser():
    now = datetime(2020, 6, 1, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    missing_book = {"timestamp": now}
    trades = [{"trade_id": "a", "day": "2020-06-01", "decision_at": now,
               "side": "BUY", "quantity": 10, "quote": missing_book, "baseline_net_inr": 100.0},
              {"trade_id": "b", "day": "2020-06-01", "decision_at": now,
               "side": "BUY", "quantity": 10, "quote": missing_book, "baseline_net_inr": -20.0}]
    result = liquidity_counterfactual(trades)
    assert result["missed_winners"] == 1
    assert result["avoided_losers"] == 1
    assert result["difference_inr"] == -80.0


def test_liquidity_gate_rejects_crossed_and_nonfinite_books():
    now = datetime(2020, 6, 1, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    depth = {"buy": [{"price": 101, "quantity": 10}],
             "sell": [{"price": 100, "quantity": 10}]}
    assert liquidity_no_trade({"timestamp": now, "depth": depth}, now=now,
                              side="BUY", quantity=1)["reason"] == "INVALID_OR_CROSSED_BOOK"
    depth["sell"][0]["price"] = float("nan")
    assert liquidity_no_trade({"timestamp": now, "depth": depth}, now=now,
                              side="BUY", quantity=1)["reason"] == "INVALID_OR_CROSSED_BOOK"
