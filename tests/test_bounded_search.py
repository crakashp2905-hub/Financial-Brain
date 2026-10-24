from datetime import date

import pandas as pd
import pytest

from financial_brain.evaluation import bounded_search as S


def test_catalogue_is_deterministic_and_counts_every_variant():
    a = S.variants(seed=7, random_budget=4)
    b = S.variants(seed=7, random_budget=4)
    assert a == b
    assert len(a) == 4 + 11 * 3
    assert len({v.name for v in a}) == len(a)


def test_universe_selection_excludes_names_joining_after_training_start():
    basis = pd.DataFrame({"start": ["2016-01-01", "2016-01-01", "2016-06-01"],
                          "symbol": ["EARLY1", "EARLY2", "LATE"]})
    assert S.choose_symbols(basis, n=2, seed=1) == ["EARLY1", "EARLY2"]


def test_training_winner_is_not_reselected_using_internal_validation():
    catalogue = [S.Variant("a", "regime_breakout", 10),
                 S.Variant("b", "regime_breakout", 10)]
    rows = []
    for year in range(2016, 2022):
        for month in range(1, 13):
            for trade in range(3):
                day = pd.Timestamp(date(year, month, trade + 1))
                wobble = (month % 3) * 0.001
                a = (0.02 if year <= 2018 else -0.02) + wobble
                b = 0.01 + wobble
                rows.append(("a", "regime_breakout", "ABC", day, a, 0.0, a))
                rows.append(("b", "regime_breakout", "ABC", day, b, 0.0, b))
    result = S.summarize(pd.DataFrame(rows, columns=["variant", "family", "symbol", "signal_day",
                                                  "net", "benchmark_net", "excess_net"]),
                         catalogue)
    assert result["searched_variants"] == 2
    assert result["selected_on_training"]["variant"] == "a"
    assert result["same_variant_internal_validation"]["mean_net_per_trade"] < 0


def test_schedule_matched_benchmark_removes_a_common_market_move(monkeypatch):
    dates = pd.bdate_range("2020-01-01", periods=240)
    def bars(exit_close):
        close = [100.0] * 240
        close[216] = exit_close
        return pd.DataFrame({"session": dates, "open": [100.0] * 240,
                             "high": [max(100.0, v) for v in close],
                             "low": [100.0] * 240, "close": close,
                             "volume": [1000] * 240})
    def one_event(bars_, market, variant, fixed):
        event = pd.Series(False, index=range(len(bars_)))
        event.iloc[210] = True
        return event
    monkeypatch.setattr(S, "events", one_event)
    variant = S.Variant("test", "fixed_rule", 5)
    result = S.evaluate({"A": bars(110.0), "B": bars(100.0)}, pd.Series(dtype=float),
                        [variant], cost=0)
    by_symbol = result.set_index("symbol")
    assert by_symbol.loc["A", "benchmark_net"] == pytest.approx(0.05)
    assert by_symbol.loc["A", "excess_net"] == pytest.approx(0.05)
    assert by_symbol.loc["B", "excess_net"] == pytest.approx(-0.05)
