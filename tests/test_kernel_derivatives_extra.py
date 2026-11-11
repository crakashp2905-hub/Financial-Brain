from __future__ import annotations

from decimal import Decimal

from financial_brain.kernel.derivatives_extra import (
    OptionLeg,
    contract_notional,
    delta_hedge,
    executable_net,
    futures_tsmom,
    lot_ok,
    strategy_payoff,
)


def test_bull_call_spread_payoff_is_capped() -> None:
    legs = [
        OptionLeg("CALL", Decimal("100"), 1, Decimal("5"), Decimal("5")),
        OptionLeg("CALL", Decimal("110"), -1, Decimal("2"), Decimal("2")),
    ]
    assert strategy_payoff(legs, Decimal("90")) == Decimal("0")
    assert strategy_payoff(legs, Decimal("105")) == Decimal("5")
    assert strategy_payoff(legs, Decimal("130")) == Decimal("10")


def test_delta_hedge_neutralises_delta_and_charges_half_spread() -> None:
    hedge = delta_hedge(Decimal("-250.7"), Decimal("99.9"), Decimal("100.1"))
    assert hedge["shares"] == Decimal("250")
    assert hedge["residual_delta"] == Decimal("-0.7")
    assert hedge["cost"] == Decimal("250") * Decimal("0.1")


def test_executable_net_pays_ask_on_buys_and_bid_on_sells() -> None:
    legs = [
        OptionLeg("CALL", Decimal("100"), 1, Decimal("4.0"), Decimal("4.4")),
        OptionLeg("CALL", Decimal("110"), -1, Decimal("1.0"), Decimal("1.2")),
    ]
    net = executable_net(legs)
    assert net["executable"] == Decimal("4.4") - Decimal("1.0")
    assert net["mid"] == Decimal("4.2") - Decimal("1.1")
    assert net["legging_spread"] == Decimal("0.4") + Decimal("0.2")


def test_contract_notional_and_lot_checks() -> None:
    assert contract_notional(Decimal("20000"), Decimal("50"), 2) == Decimal("2000000")
    assert lot_ok(75, 25) is True
    assert lot_ok(30, 25) is False
    assert lot_ok(0, 25) is False


def test_futures_tsmom_applies_from_next_bar() -> None:
    assert futures_tsmom([1.0, 2.0, 3.0, 2.5], 1) == [0, 0, 1, 1]
