from __future__ import annotations

import pytest

from financial_brain.kernel.baselines import abstain, buy_and_hold, cash, ma_crossover, strategy_return, time_series_momentum
from financial_brain.kernel.preflight import ConfigError, load_config, preflight


def test_preflight_blocks_session_when_a_required_check_fails() -> None:
    report = preflight({"feed": lambda: True, "clock": lambda: False}, ["feed", "clock"])
    assert report.passed is False
    assert any("clock" in f for f in report.failures)


def test_missing_or_crashing_check_counts_as_failure() -> None:
    def boom() -> bool:
        raise RuntimeError("disk gone")

    report = preflight({"disk": boom}, ["disk", "db"])
    assert report.passed is False
    assert "db: check not registered" in report.failures
    assert "disk: raised RuntimeError" in report.failures


def test_all_checks_pass_allows_session() -> None:
    assert preflight({"a": lambda: True}, ["a"]).passed is True


def test_defaults_are_paper_only_and_external_orders_cannot_be_enabled() -> None:
    cfg = load_config()
    assert cfg["mode"] == "PAPER" and cfg["external_orders"] is False
    with pytest.raises(ConfigError, match="cannot be enabled"):
        load_config({"external_orders": True})
    with pytest.raises(ConfigError, match="unknown config key"):
        load_config({"broker_key": "x"})
    with pytest.raises(ConfigError, match="terminal"):
        load_config({"mode": "LIVE_DISABLED"})


def test_no_same_bar_entry_in_momentum_or_crossover() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    mom = time_series_momentum(closes, 2)
    assert mom[0] == 0 and mom[1] == 0
    assert mom[3] == 1  # decided at bar 2 (close above bar 0), applied at bar 3
    xo = ma_crossover(closes, 2, 3)
    assert xo[0] == 0


def test_abstention_has_zero_trades_and_zero_return() -> None:
    closes = [1.0, 2.0, 1.5]
    assert abstain(closes) == [0, 0, 0]
    assert strategy_return(closes, abstain(closes)) == pytest.approx(0.0)


def test_buy_and_hold_and_cash_references() -> None:
    closes = [100.0, 110.0, 99.0]
    assert strategy_return(closes, buy_and_hold(closes)) == pytest.approx(-0.01)
    assert strategy_return(closes, cash(closes)) == pytest.approx(0.0)


def test_crossover_rejects_bad_windows() -> None:
    with pytest.raises(ValueError):
        ma_crossover([1.0, 2.0], 5, 3)
