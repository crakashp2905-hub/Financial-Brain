from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.instruments import (
    AssetClass,
    Desk,
    Instrument,
    InstrumentError,
    InstrumentId,
    InstrumentMaster,
    Phase,
    RuleVersion,
    Status,
    desk_supports_asset_class,
    fx_pip_size,
    fx_pip_value,
)


def _equity() -> Instrument:
    return Instrument(
        InstrumentId("NSE", "EQUITY", "ABC"),
        rules=[
            RuleVersion(date(2026, 1, 1), tick=Decimal("0.05")),
            RuleVersion(date(2026, 6, 1), tick=Decimal("0.10"), lot=1),
        ],
        status_history=[(date(2026, 1, 1), Status.ACTIVE), (date(2026, 9, 1), Status.SUSPENDED)],
    )


def test_rule_lookup_is_effective_dated() -> None:
    item = _equity()
    assert item.rule_on(date(2026, 3, 1)).tick == Decimal("0.05")
    assert item.rule_on(date(2026, 7, 1)).tick == Decimal("0.10")


def test_day_before_first_rule_refuses() -> None:
    with pytest.raises(InstrumentError, match="refusing to guess"):
        _equity().rule_on(date(2025, 1, 1))


def test_suspended_status_blocks_trading_fail_closed() -> None:
    item = _equity()
    assert item.tradable(date(2026, 3, 2), Phase.CONTINUOUS) == (True, "OK")
    ok, reason = item.tradable(date(2026, 9, 2), Phase.CONTINUOUS)
    assert ok is False and reason == "STATUS_SUSPENDED"


def test_non_continuous_phase_blocks_trading() -> None:
    ok, reason = _equity().tradable(date(2026, 3, 2), Phase.PREOPEN)
    assert ok is False and reason == "PHASE_PREOPEN"


def test_expired_contract_never_tradable() -> None:
    fut = Instrument(
        InstrumentId("NSE", "FUTURE", "ABC26NOV"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"), multiplier=Decimal(500))],
        expiry=date(2026, 11, 26),
    )
    assert fut.tradable(date(2026, 11, 27), Phase.CONTINUOUS) == (False, "EXPIRED_BY_DATE")


def test_unknown_instrument_is_not_tradable() -> None:
    master = InstrumentMaster()
    with pytest.raises(InstrumentError, match="no trade is possible"):
        master.get(InstrumentId("NSE", "EQUITY", "NOPE"))


def test_duplicate_instrument_rejected() -> None:
    master = InstrumentMaster()
    master.add(_equity())
    with pytest.raises(InstrumentError, match="duplicate"):
        master.add(_equity())


def test_invalid_asset_class_and_rules_rejected() -> None:
    with pytest.raises(InstrumentError):
        InstrumentId("NSE", "CRYPTOX", "ABC")
    with pytest.raises(InstrumentError):
        RuleVersion(date(2026, 1, 1), tick=Decimal("0"))


def test_fx_pip_conventions_differ_for_jpy() -> None:
    assert fx_pip_size("EURUSD") == Decimal("0.0001")
    assert fx_pip_size("USDJPY") == Decimal("0.01")
    assert fx_pip_value("EURUSD", 100_000, Decimal("1.10")) == Decimal("10.0000")


def test_bad_fx_pair_rejected() -> None:
    with pytest.raises(InstrumentError):
        fx_pip_size("eurusd")


def test_asset_classes_and_desks_are_independent() -> None:
    assert set(AssetClass) == {
        AssetClass.EQUITY, AssetClass.ETF, AssetClass.OPTION, AssetClass.MUTUAL_FUND,
    }
    assert set(Desk) == {
        Desk.SWING, Desk.INTRADAY, Desk.OPTIONS, Desk.ETF, Desk.MUTUAL_FUND,
    }
    assert desk_supports_asset_class(Desk.SWING, AssetClass.ETF)
    assert desk_supports_asset_class(Desk.INTRADAY, AssetClass.ETF)
    assert desk_supports_asset_class(Desk.OPTIONS, AssetClass.OPTION)
    assert not desk_supports_asset_class(Desk.OPTIONS, AssetClass.EQUITY)


def test_canonical_instrument_identity_normalizes_and_resolves_effective_versions() -> None:
    master = InstrumentMaster()
    old = Instrument(
        InstrumentId("nse", "equity", "abc", isin="INE000A01001",
                     provider_token="11", effective_from=date(2020, 1, 1),
                     effective_to=date(2024, 12, 31), underlying_lineage=("NIFTY50",)),
        rules=[RuleVersion(date(2020, 1, 1), tick=Decimal("0.05"))],
    )
    new = Instrument(
        InstrumentId("NSE", "EQUITY", "ABC", isin="INE000A01019",
                     provider_token="22", effective_from=date(2025, 1, 1)),
        rules=[RuleVersion(date(2025, 1, 1), tick=Decimal("0.05"))],
    )
    master.add(old)
    master.add(new)
    assert old.instrument.exchange == old.instrument.venue == "NSE"
    assert old.instrument.key() != new.instrument.key()
    assert master.resolve(market="IN", exchange="NSE", symbol="ABC",
                          on=date(2024, 1, 1)) is old
    assert master.resolve(market="IN", exchange="NSE", symbol="ABC",
                          on=date(2025, 1, 1)) is new
    assert old.isin == "INE000A01001"
    assert old.tradable(date(2025, 1, 1), Phase.CONTINUOUS) == (False, "NO_LONGER_EFFECTIVE")


def test_instrument_master_rejects_overlapping_listing_versions() -> None:
    master = InstrumentMaster()
    master.add(Instrument(
        InstrumentId("NSE", "EQUITY", "ABC", isin="INE000A01001",
                     effective_from=date(2020, 1, 1)),
        rules=[RuleVersion(date(2020, 1, 1), tick=Decimal("0.05"))],
    ))
    with pytest.raises(InstrumentError, match="overlapping effective identities"):
        master.add(Instrument(
            InstrumentId("NSE", "EQUITY", "ABC", isin="INE000A01019",
                         effective_from=date(2021, 1, 1)),
            rules=[RuleVersion(date(2021, 1, 1), tick=Decimal("0.05"))],
        ))
