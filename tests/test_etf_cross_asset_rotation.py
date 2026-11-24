from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.features.etf_cross_asset_rotation import (
    SPEC_SHA256,
    build_cross_asset_rotation_snapshot,
)
from financial_brain.providers.etf_reference import ETFExposureCategory, ETFReferenceRecord


def _record(symbol: str, isin: str, exposure: ETFExposureCategory) -> ETFReferenceRecord:
    published = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return ETFReferenceRecord(
        source_id="NSE", exchange="NSE", symbol=symbol, isin=isin,
        listing_date=date(2020, 1, 1), effective_from=date(2020, 1, 1), effective_to=None,
        series="EQ", published_at=published, observed_at=published + timedelta(minutes=1),
        source_ref="synthetic test row", source_sha256="a" * 64,
        underlying_index=f"{exposure.value} TRI", exposure_category=exposure,
    )


def _data():
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(140)]
    as_of = datetime.combine(sessions[-1], time(23, 30), timezone.utc)
    categories = [
        ("EQ1", ETFExposureCategory.EQUITY_BROAD_MARKET, 0.08),
        ("EQ2", ETFExposureCategory.EQUITY_SECTOR, 0.12),
        ("DEBT", ETFExposureCategory.BOND_DEBT, 0.03),
        ("GOLD", ETFExposureCategory.GOLD, 0.20),
        ("SILVER", ETFExposureCategory.SILVER, -0.02),
    ]
    refs = [_record(symbol, f"INE000A0100{i+1}", exposure)
            for i, (symbol, exposure, _) in enumerate(categories)]
    closes = {}
    hashes = {}
    available = {}
    for i, row in enumerate(refs):
        growth = categories[i][2]
        closes[row.isin] = {day: 100 * (1 + growth) ** (n / 252)
                            for n, day in enumerate(sessions)}
        hashes[row.isin] = {day: f"{n + 1:064x}" for n, day in enumerate(sessions)}
        available[row.isin] = {day: datetime.combine(day, time(22), timezone.utc)
                               for day in sessions}
    return sessions, as_of, refs, closes, hashes, available


def _snapshot(data):
    sessions, as_of, refs, closes, hashes, available = data
    return build_cross_asset_rotation_snapshot(
        as_of=as_of, expected_sessions=sessions, closes_by_isin=closes,
        source_hashes_by_isin=hashes, available_at_by_isin=available,
        references=refs,
    )


def test_rotation_selects_top_positive_sleeve_and_emits_all_controls():
    data = _data()
    result = _snapshot(data)
    assert result.rotation_winner_isin == "INE000A01004"
    assert result.rotation.weights_by_isin[0][0] == result.rotation_winner_isin
    assert [row[0] for row in result.sleeve_winners] == ["DEBT", "EQUITY", "GOLD", "SILVER"]
    assert result.sleeve_winners[1][1] == "INE000A01002"  # best ETF within equity sleeve
    for control in (result.rotation, result.equal_volatility_control,
                    result.buy_and_hold_control, result.same_risk_control, result.cash_control):
        assert sum(weight for _, weight in control.weights_by_isin) + control.cash_weight == pytest.approx(1)
    assert result.cash_control.cash_weight == 1
    assert result.same_risk_control.cash_weight >= 0
    assert result.candidate_only and not result.orders_authorized
    assert result.strategy_spec_sha256 == SPEC_SHA256
    assert result == _snapshot(data)


def test_missing_or_future_available_data_is_excluded_without_bridging():
    data = _data()
    sessions, as_of, refs, closes, hashes, available = data
    missing_isin = refs[3].isin
    del closes[missing_isin][sessions[-1 - 30]]
    late_isin = refs[2].isin
    available[late_isin][sessions[-1]] = as_of + timedelta(seconds=1)
    result = build_cross_asset_rotation_snapshot(
        as_of=as_of, expected_sessions=sessions, closes_by_isin=closes,
        source_hashes_by_isin=hashes, available_at_by_isin=available,
        references=refs,
    )
    assert missing_isin not in {isin for _, isin, _ in result.sleeve_winners}
    assert late_isin not in {isin for _, isin, _ in result.sleeve_winners}


def test_no_positive_momentum_means_cash_rotation_but_controls_remain():
    data = _data()
    sessions, as_of, refs, closes, hashes, available = data
    for isin in closes:
        closes[isin] = {day: 100 + (len(sessions) - i) * 0.01 for i, day in enumerate(sessions)}
    result = build_cross_asset_rotation_snapshot(
        as_of=as_of, expected_sessions=sessions, closes_by_isin=closes,
        source_hashes_by_isin=hashes, available_at_by_isin=available,
        references=refs,
    )
    assert result.rotation_winner_isin is None
    assert result.rotation.cash_weight == 1
    assert result.buy_and_hold_control.cash_weight == 0


def test_invalid_calendar_hash_availability_and_future_session_fail_closed():
    data = _data()
    sessions, as_of, refs, closes, hashes, available = data
    with pytest.raises(ValueError, match="unique and ordered"):
        build_cross_asset_rotation_snapshot(
            as_of=as_of, expected_sessions=sessions + [sessions[-1]],
            closes_by_isin=closes, source_hashes_by_isin=hashes,
            available_at_by_isin=available, references=refs,
        )
    hashes[refs[0].isin][sessions[-1]] = "X" * 64
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _snapshot(data)


def test_naive_availability_and_future_session_are_rejected():
    data = _data()
    sessions, as_of, refs, closes, hashes, available = data
    available[refs[0].isin][sessions[-1]] = datetime(2025, 1, 1)
    with pytest.raises(ValueError, match="timezone aware"):
        _snapshot(data)
    future_sessions = sessions + [sessions[-1] + timedelta(days=1)]
    with pytest.raises(ValueError, match="cannot follow as_of"):
        build_cross_asset_rotation_snapshot(
            as_of=as_of, expected_sessions=future_sessions, closes_by_isin=closes,
            source_hashes_by_isin=hashes, available_at_by_isin=available,
            references=refs,
        )
