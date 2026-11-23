from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.instruments import (
    AssetClass,
    Desk,
    Instrument,
    InstrumentError,
    InstrumentIdentityVersion,
    InstrumentId,
    InstrumentMaster,
    InstrumentMetadataVersion,
    InstrumentObservation,
    Phase,
    RuleVersion,
    Status,
    fx_pip_size,
    fx_pip_value,
)
from financial_brain.kernel.clock import MarketFactTimes


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


def test_explicit_rule_effective_interval_is_end_exclusive() -> None:
    item = Instrument(
        InstrumentId("NSE", "ETF", "FUND"),
        rules=[
            RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"), effective_to=date(2026, 6, 1)),
            RuleVersion(date(2026, 6, 1), tick=Decimal("0.10")),
        ],
    )
    assert item.rule_on(date(2026, 5, 31)).tick == Decimal("0.05")
    assert item.rule_on(date(2026, 6, 1)).tick == Decimal("0.10")


def test_effective_dated_metadata_preserves_etf_and_fund_versions() -> None:
    item = Instrument(
        InstrumentId("NSE", "ETF", "TRACKER"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
        metadata_versions=[
            InstrumentMetadataVersion(
                date(2026, 1, 1),
                effective_to=date(2026, 7, 1),
                isin="INF123A01010",
                underlying_index="INDEX-A",
                scheme_id="SCHEME-1",
                plan="DIRECT_GROWTH",
            ),
            InstrumentMetadataVersion(
                date(2026, 7, 1),
                isin="INF123A01010",
                underlying_index="INDEX-B",
                scheme_id="SCHEME-1",
                plan="DIRECT_GROWTH",
            ),
        ],
    )
    assert item.metadata_on(date(2026, 6, 30)).underlying_index == "INDEX-A"
    assert item.metadata_on(date(2026, 7, 1)).underlying_index == "INDEX-B"
    assert item.metadata_on(date(2026, 7, 1)).plan == "DIRECT_GROWTH"


def test_metadata_lookup_refuses_pre_effective_and_overlap() -> None:
    item = Instrument(
        InstrumentId("NSE", "OPTION", "CONTRACT"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
        metadata_versions=[InstrumentMetadataVersion(date(2026, 2, 1), expiry=date(2026, 3, 26))],
    )
    with pytest.raises(InstrumentError, match="refusing to guess"):
        item.metadata_on(date(2026, 1, 31))
    with pytest.raises(InstrumentError, match="overlapping metadata"):
        Instrument(
            InstrumentId("NSE", "ETF", "BAD"),
            rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
            metadata_versions=[
                InstrumentMetadataVersion(date(2026, 1, 1), effective_to=date(2026, 5, 1)),
                InstrumentMetadataVersion(date(2026, 4, 1)),
            ],
        )


def test_dated_ticker_and_isin_changes_resolve_to_stable_canonical_lineage() -> None:
    canonical_id = InstrumentId(
        "NSE",
        "EQUITY",
        "OLDCO",
        market="IN",
        exchange="NSE",
        underlying_lineage="issuer-42",
    )
    item = Instrument(
        canonical_id,
        rules=[RuleVersion(date(2020, 1, 1), tick=Decimal("0.05"))],
        identity_versions=[
            InstrumentIdentityVersion(
                effective_from=date(2020, 1, 1),
                effective_to=date(2025, 1, 1),
                market="IN",
                exchange="NSE",
                symbol="OLDCO",
                isin="INE000A01011",
                provider_token="old-token",
                underlying_lineage="issuer-42",
            ),
            InstrumentIdentityVersion(
                effective_from=date(2025, 1, 1),
                market="IN",
                exchange="NSE",
                symbol="NEWCO",
                isin="INE000A01029",
                provider_token="new-token",
                underlying_lineage="issuer-42",
            ),
        ],
    )
    master = InstrumentMaster()
    master.add(item)
    assert master.resolve("IN", "NSE", "EQUITY", "OLDCO", date(2024, 12, 31)) is item
    assert master.resolve("IN", "NSE", "EQUITY", "NEWCO", date(2025, 1, 1)) is item
    assert item.instrument.key() == "LINEAGE:EQUITY:issuer-42"
    with pytest.raises(InstrumentError, match="unknown instrument identity"):
        master.resolve("IN", "NSE", "EQUITY", "OLDCO", date(2025, 1, 1))


def test_overlapping_identity_aliases_are_rejected_across_instruments() -> None:
    master = InstrumentMaster()
    master.add(
        Instrument(
            InstrumentId("NSE", "EQUITY", "ONE"),
            rules=[RuleVersion(date(2020, 1, 1), tick=Decimal("0.05"))],
            identity_versions=[
                InstrumentIdentityVersion(date(2020, 1, 1), "IN", "NSE", "DUPLICATE")
            ],
        )
    )
    with pytest.raises(InstrumentError, match="overlapping active identity alias"):
        master.add(
            Instrument(
                InstrumentId("NSE", "EQUITY", "TWO"),
                rules=[RuleVersion(date(2020, 1, 1), tick=Decimal("0.05"))],
                identity_versions=[
                    InstrumentIdentityVersion(date(2021, 1, 1), "IN", "NSE", "DUPLICATE")
                ],
            )
        )


def test_day_before_first_rule_refuses() -> None:
    with pytest.raises(InstrumentError, match="refusing to guess"):
        _equity().rule_on(date(2025, 1, 1))


def test_suspended_status_blocks_trading_fail_closed() -> None:
    item = _equity()
    assert item.tradable(date(2026, 3, 2), Phase.CONTINUOUS) == (True, "OK")
    ok, reason = item.tradable(date(2026, 9, 2), Phase.CONTINUOUS)
    assert ok is False and reason == "STATUS_SUSPENDED"


@pytest.mark.parametrize(
    "status",
    [
        Status.SUSPENDED,
        Status.HALTED,
        Status.DELISTED,
        Status.MATURED,
        Status.CLOSED_FOR_PURCHASE,
        Status.CLOSED_FOR_REDEMPTION,
        Status.RESTRICTED,
    ],
)
def test_non_listed_instrument_lifecycle_states_fail_closed(status: Status) -> None:
    item = Instrument(
        InstrumentId("NSE", "EQUITY", "ABC"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
        status_history=[(date(2026, 1, 1), status)],
    )
    ok, reason = item.tradable(date(2026, 1, 2), Phase.CONTINUOUS)
    assert not ok
    assert reason == f"STATUS_{status.value}"


def test_listed_instrument_is_tradable_during_continuous_phase() -> None:
    item = Instrument(
        InstrumentId("NSE", "EQUITY", "ABC"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
        status_history=[(date(2026, 1, 1), Status.LISTED)],
    )
    assert item.tradable(date(2026, 1, 2), Phase.CONTINUOUS) == (True, "OK")


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


def test_asset_class_and_desk_are_independent_taxonomies() -> None:
    assert InstrumentId("NSE", AssetClass.ETF, "NIFTYBEES").asset_class == "ETF"
    assert {item.value for item in AssetClass} >= {"EQUITY", "ETF", "OPTION", "MUTUAL_FUND"}
    assert {item.value for item in Desk} == {"SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND"}


def test_fx_pip_conventions_differ_for_jpy() -> None:
    assert fx_pip_size("EURUSD") == Decimal("0.0001")
    assert fx_pip_size("USDJPY") == Decimal("0.01")
    assert fx_pip_value("EURUSD", 100_000, Decimal("1.10")) == Decimal("10.0000")


def test_bad_fx_pair_rejected() -> None:
    with pytest.raises(InstrumentError):
        fx_pip_size("eurusd")


def test_semantic_observation_resolution_rejects_unknown_and_future_data() -> None:
    start = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)
    decision = start + timedelta(seconds=5)
    item = Instrument(
        InstrumentId("NSE", "EQUITY", "ABC", underlying_lineage="issuer-abc"),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
        identity_versions=[
            InstrumentIdentityVersion(date(2026, 1, 1), "IN", "NSE", "ABC", "INE000A01011")
        ],
    )
    master = InstrumentMaster()
    master.add(item)
    observation = InstrumentObservation(
        market="IN",
        exchange="NSE",
        asset_class="EQUITY",
        symbol="ABC",
        as_of=date(2026, 11, 9),
        times=MarketFactTimes(
            received_at=start,
            available_at=start + timedelta(seconds=1),
            ingested_at=start + timedelta(seconds=2),
            source_at=start,
            exchange_at=start,
            published_at=start,
        ),
        source_id="fixture-feed-1",
        record_sha256="d" * 64,
    )
    assert master.resolve_observation(observation, decision) is item

    unknown = InstrumentObservation(
        market="IN",
        exchange="NSE",
        asset_class="EQUITY",
        symbol="UNKNOWN",
        as_of=date(2026, 11, 9),
        times=observation.times,
        source_id="fixture-feed-1",
        record_sha256="e" * 64,
    )
    with pytest.raises(InstrumentError, match="unknown instrument identity"):
        master.resolve_observation(unknown, decision)

    future = InstrumentObservation(
        market="IN",
        exchange="NSE",
        asset_class="EQUITY",
        symbol="ABC",
        as_of=date(2026, 11, 9),
        times=MarketFactTimes(
            received_at=start,
            available_at=decision + timedelta(seconds=1),
            ingested_at=decision + timedelta(seconds=2),
            source_at=decision + timedelta(seconds=1),
        ),
        source_id="fixture-feed-1",
        record_sha256="f" * 64,
    )
    with pytest.raises(InstrumentError, match="not yet available"):
        master.resolve_observation(future, decision)
