from __future__ import annotations

import pytest

from financial_brain.forward_edge.options_attribution import (
    OptionTradeBridgeInput,
    OptionsAttributionError,
    decompose_option_trade_pnl,
)


def _trade(**overrides):
    values = dict(
        position="LONG", quantity=2, contract_multiplier=25,
        entry_underlying=100.0, exit_underlying=102.0,
        entry_delta=0.5, entry_gamma=0.02, entry_theta_per_day=-0.3,
        entry_vega_per_vol_point=0.08, entry_iv_percent=20.0,
        exit_iv_percent=18.0, elapsed_days=1.0,
        entry_bid=9.8, entry_ask=10.2, exit_bid=10.7, exit_ask=11.1,
        fees_inr=3.0,
    )
    return OptionTradeBridgeInput(**(values | overrides))


def test_long_option_bridge_reconciles_greeks_execution_and_fees():
    out = decompose_option_trade_pnl(_trade())
    assert out["underlying_move_inr"] == pytest.approx(50.0)
    assert out["gamma_approximation_inr"] == pytest.approx(2.0)
    assert out["theta_decay_inr"] == pytest.approx(-15.0)
    assert out["implied_volatility_change_inr"] == pytest.approx(-8.0)
    assert out["bid_ask_execution_inr"] == pytest.approx(-20.0)
    assert out["gross_executable_pnl_inr"] == pytest.approx(25.0)
    assert out["net_executable_pnl_inr"] == pytest.approx(22.0)
    assert out["reconciled"] is True
    assert out["reconciled_net_pnl_inr"] == pytest.approx(out["net_executable_pnl_inr"])
    assert len(out["report_sha256"]) == 64


def test_short_option_bridge_uses_bid_entry_ask_exit_and_signed_greeks():
    out = decompose_option_trade_pnl(_trade(position="SHORT"))
    assert out["gross_executable_pnl_inr"] == pytest.approx(-65.0)
    assert out["bid_ask_execution_inr"] == pytest.approx(-20.0)
    assert out["net_executable_pnl_inr"] == pytest.approx(-68.0)
    assert out["underlying_move_inr"] == pytest.approx(-50.0)
    assert out["theta_decay_inr"] == pytest.approx(15.0)
    assert out["reconciled"] is True


def test_invalid_quote_and_non_finite_inputs_fail_closed():
    with pytest.raises(OptionsAttributionError, match="crossed quote"):
        decompose_option_trade_pnl(_trade(exit_bid=12.0, exit_ask=11.0))
    with pytest.raises(OptionsAttributionError, match="finite"):
        decompose_option_trade_pnl(_trade(entry_delta=float("nan")))
    with pytest.raises(OptionsAttributionError, match="positive integer"):
        decompose_option_trade_pnl(_trade(quantity=0))
