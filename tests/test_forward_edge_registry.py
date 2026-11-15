"""F02 strategy specification and eligibility tests (FE-0023/0024)."""
from __future__ import annotations

import pytest

from financial_brain.kernel.registry import RegistryError, StrategyEligibility, StrategySpec
from financial_brain.kernel.instruments import Instrument, InstrumentId, Phase, RuleVersion, Status
from datetime import date
from decimal import Decimal


def test_forward_strategy_spec_validates_all_required_execution_metadata() -> None:
    spec = StrategySpec("orb", "1.0", {"range_bars": 15}, "owner", family="session_breakout",
                        code_sha256="a" * 64, asset_scope=("NSE_EQ",), decision_clock="5m_bar_close",
                        direction="LONG", stop_exit="range_low_or_eod", horizon="intraday",
                        data_requirements=("ohlcv_1m", "exchange_calendar"))
    spec.validate_forward()
    assert StrategySpec.from_json(spec.to_json()).hash() == spec.hash()
    with pytest.raises(RegistryError, match="code_sha256"):
        StrategySpec("orb", "1.0", {}, "owner", family="orb", code_sha256="bad", asset_scope=("NSE_EQ",),
                     decision_clock="bar", direction="LONG", stop_exit="stop", horizon="day",
                     data_requirements=("ohlcv",)).validate_forward()


def test_strategy_eligibility_cannot_omit_lineage_or_use_unrecognized_state() -> None:
    with pytest.raises(RegistryError, match="unknown forward strategy status"):
        StrategyEligibility("orb", "1", "WINNER", "registry", "a" * 64, "reason", "b" * 64)
    with pytest.raises(RegistryError, match="lineage"):
        StrategyEligibility("orb", "1", "FAILED", "", "a" * 64, "reason", "b" * 64)


def test_dated_instrument_eligibility_fails_closed_on_status_rights_and_ticket_rules() -> None:
    instrument = Instrument(InstrumentId("NSE", "EQUITY", "INFY"),
                            [RuleVersion(date(2026, 1, 1), Decimal("0.05"), lot=1,
                                         max_order_quantity=100)],
                            [(date(2026, 1, 1), Status.ACTIVE)], isin="INE009A01021",
                            rights_status="authorized")
    assert instrument.eligible(date(2026, 11, 15), Phase.CONTINUOUS, quantity=10,
                               price=Decimal("1500")) == (True, ())
    ok, reasons = instrument.eligible(date(2026, 11, 15), Phase.CLOSED, quantity=101,
                                      price=Decimal("1500"))
    assert not ok and "PHASE_CLOSED" in reasons and any("freeze limit" in r for r in reasons)
    restricted = Instrument(InstrumentId("NSE", "OPTION", "NIFTY26NOV25000CE"),
                            [RuleVersion(date(2026, 1, 1), Decimal("0.05"), lot=75,
                                         max_order_quantity=750)], expiry=date(2026, 11, 26),
                            strike=Decimal("25000"))
    ok, reasons = restricted.eligible(date(2026, 11, 15), Phase.CONTINUOUS, quantity=75,
                                      price=Decimal("100"))
    assert not ok and "INSTRUMENT_RIGHTS_UNVERIFIED" in reasons
