from __future__ import annotations

from datetime import date, timedelta

import pytest

from financial_brain.features.etf_trend_rules import (
    SPEC_SHA256,
    build_etf_trend_candidates,
)


def _inputs(count: int = 240):
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(count)]
    closes = {f"ISIN{i}": {day: 100.0 + index * (i + 1) * 0.1
                            for index, day in enumerate(sessions)} for i in range(2)}
    hashes = {isin: {day: f"{index + 1:064x}" for index, day in enumerate(sessions)}
              for isin in closes}
    return sessions, closes, hashes


def test_candidates_emit_frozen_trend_volatility_and_sourced_hashes():
    sessions, closes, hashes = _inputs()
    result = build_etf_trend_candidates(
        sessions=sessions, closes_by_isin=closes, source_hashes_by_isin=hashes,
        symbols_by_isin={"ISIN0": "ETF0", "ISIN1": "ETF1"},
    )
    assert [row.symbol for row in result] == ["ETF0", "ETF1"]
    assert all(row.strategy_spec_sha256 == SPEC_SHA256 for row in result)
    assert all(row.candidate_only and not row.orders_authorized for row in result)
    assert all(row.rule_signals[0][1] and row.rule_signals[1][1] for row in result)
    assert all(0 < row.volatility_target_gross_exposure <= 1 for row in result)
    assert result == build_etf_trend_candidates(
        sessions=sessions, closes_by_isin=closes, source_hashes_by_isin=hashes,
        symbols_by_isin={"ISIN0": "ETF0", "ISIN1": "ETF1"},
    )


def test_missing_or_short_sourced_history_is_not_filled_or_bridged():
    sessions, closes, hashes = _inputs(240)
    del closes["ISIN0"][sessions[-15]]
    assert [row.isin for row in build_etf_trend_candidates(
        sessions=sessions, closes_by_isin=closes, source_hashes_by_isin=hashes,
        symbols_by_isin={},
    )] == ["ISIN1"]
    assert build_etf_trend_candidates(
        sessions=sessions[:200], closes_by_isin=closes,
        source_hashes_by_isin=hashes, symbols_by_isin={},
    ) == []


def test_invalid_calendar_and_price_hash_inputs_fail_closed():
    sessions, closes, hashes = _inputs()
    with pytest.raises(ValueError, match="unique and strictly ordered"):
        build_etf_trend_candidates(
            sessions=sessions + [sessions[-1]], closes_by_isin=closes,
            source_hashes_by_isin=hashes, symbols_by_isin={},
        )
    hashes["ISIN0"][sessions[-1]] = "x" * 64
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        build_etf_trend_candidates(
            sessions=sessions, closes_by_isin=closes,
            source_hashes_by_isin=hashes, symbols_by_isin={},
        )


def test_zero_volatility_series_emits_no_candidate():
    sessions, closes, hashes = _inputs()
    closes["ISIN0"] = {day: 100.0 for day in sessions}
    assert "ISIN0" not in {row.isin for row in build_etf_trend_candidates(
        sessions=sessions, closes_by_isin=closes, source_hashes_by_isin=hashes,
        symbols_by_isin={},
    )}
