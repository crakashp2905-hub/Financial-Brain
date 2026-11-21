from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.features.etf_valuation import compare_quote_to_inav
from financial_brain.forward_edge.market_data import normalize_kite_quote
from financial_brain.providers.etf_reference import (
    ETFReferenceError,
    ETFReferenceRecord,
    ETFValuationObservation,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 13, 4, 45, 3, tzinfo=UTC)


def _identity() -> ETFReferenceRecord:
    return ETFReferenceRecord(
        source_id="NSE", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        listing_date=date(2020, 1, 2), effective_from=date(2020, 1, 2), effective_to=None,
        series="EQ", published_at=datetime(2020, 1, 2, 3, tzinfo=UTC),
        observed_at=datetime(2020, 1, 2, 4, tzinfo=UTC), source_ref="reference",
        source_sha256="a" * 64,
    )


def _quote(*, symbol: str = "INDEXETF", timestamp: str = "2026-11-13 10:15:00",
           received: datetime = NOW):
    return normalize_kite_quote(
        f"NSE:{symbol}",
        {"timestamp": timestamp, "last_price": 101.5, "volume": 100,
         "depth": {"buy": [{"price": 101.4, "quantity": 12}],
                   "sell": [{"price": 101.6, "quantity": 9}]}},
        received_at=received,
    )


def _inav(*, symbol: str = "INDEXETF", observed: datetime | None = None) -> ETFValuationObservation:
    observed = observed or datetime(2026, 11, 13, 4, 45, 1, tzinfo=UTC)
    return ETFValuationObservation(
        source_id="NSE", exchange="NSE", symbol=symbol, isin="INE000A01001",
        valuation_type="INAV", valuation_date=date(2026, 11, 13), value_per_unit="100.00",
        published_at=observed - timedelta(seconds=1), observed_at=observed,
        source_ref="iNAV observation", source_sha256="b" * 64,
    )


def _compare(quote, inav, *, decision: datetime = NOW, quote_age=5, inav_age=10, skew=2):
    return compare_quote_to_inav(
        quote, identity=_identity(), inav=inav, decision_time=decision,
        quote_max_age=timedelta(seconds=quote_age), inav_max_age=timedelta(seconds=inav_age),
        max_observation_skew=timedelta(seconds=skew),
    )


def test_fresh_synchronized_quote_reports_midquote_premium_in_bps() -> None:
    result = _compare(_quote(), _inav())
    assert result["premium_discount_bps"] == "150"
    assert result["quote_age_seconds"] == 3.0
    assert result["inav_age_seconds"] == 2.0
    assert result["observation_skew_seconds"] == 1.0
    assert len(result["quote_sha256"]) == len(result["inav_sha256"]) == 64


@pytest.mark.parametrize(
    ("quote", "inav", "decision", "ages", "match"),
    [
        (_quote(), _inav(), NOW + timedelta(seconds=20), (5, 60, 2), "quote is stale"),
        (_quote(), _inav(), NOW, (5, 1, 2), "iNAV is stale"),
        (_quote(timestamp="2026-11-13 10:15:04", received=NOW + timedelta(seconds=10)),
         _inav(), NOW + timedelta(seconds=11), (30, 30, 2), "asynchronous"),
        (_quote(), _inav(observed=NOW + timedelta(seconds=1)), NOW, (5, 10, 2), "unavailable"),
    ],
)
def test_stale_future_or_asynchronous_values_are_refused(quote, inav, decision, ages, match) -> None:
    with pytest.raises(ETFReferenceError, match=match):
        _compare(quote, inav, decision=decision, quote_age=ages[0], inav_age=ages[1], skew=ages[2])


def test_inav_for_another_symbol_is_refused() -> None:
    with pytest.raises(ETFReferenceError, match="same point-in-time ETF identity"):
        _compare(_quote(), _inav(symbol="OTHERETF"))
