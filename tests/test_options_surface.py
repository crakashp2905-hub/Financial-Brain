from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
import duckdb

from financial_brain.features.options_surface import OptionSurfaceError, build_from_archive, build_surface
from financial_brain.kernel.options import bsm_price

UTC = timezone.utc
AS_OF = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
RATE = 0.05
SPOT = 1000.0
EXPIRY = date(2026, 12, 31)


def _years(expiry: date, as_of: datetime) -> float:
    from zoneinfo import ZoneInfo
    close = datetime.combine(expiry, time(15, 30), tzinfo=ZoneInfo("Asia/Kolkata"))
    return (close.astimezone(UTC) - as_of).total_seconds() / (365.25 * 24 * 60 * 60)


def _quote(strike: float, kind: str, *, expiry: date = EXPIRY, iv: float = 0.25,
           bid_pad: float = 0.02, oi: int | None = 1000, received: datetime = AS_OF,
           source: datetime | None = None, spot_at: datetime | None = None,
           spot: float = SPOT) -> dict:
    call = kind in {"CE", "CALL"}
    theoretical = bsm_price(spot, strike, _years(expiry, AS_OF), RATE, iv, call)
    return {
        "underlying": "NIFTY",
        "expiry": expiry,
        "strike": strike,
        "option_type": kind,
        "bid": theoretical - bid_pad,
        "ask": theoretical + bid_pad,
        "underlying_ltp": spot,
        "open_interest": oi,
        "received_at": received,
        "source_at": source or received,
        "underlying_source_at": spot_at or received,
    }


def test_surface_uses_observed_midpoints_for_iv_greeks_skew_term_and_oi():
    rows = [_quote(k, kind) for k in (900, 1000, 1100) for kind in ("CE", "PE")]
    rows += [_quote(1000, kind, expiry=date(2027, 1, 28), iv=0.30)
             for kind in ("CE", "PE")]
    out = build_surface(rows, as_of=AS_OF, rate=RATE)
    assert out["status"] == "AVAILABLE"
    assert len(out["contracts"]) == len(rows)
    assert all(p["implied_volatility"] == pytest.approx(0.25, abs=2e-4)
               for p in out["contracts"][:6])
    assert all(p["gamma"] > 0 and p["vega"] > 0 for p in out["contracts"])
    assert len(out["expiries"]) == 2
    front, back = out["expiries"]
    assert front["atm_iv"] == pytest.approx(0.25, abs=2e-4)
    assert back["atm_iv"] == pytest.approx(0.30, abs=2e-4)
    assert front["call_open_interest"] == 3000
    assert front["put_call_oi_ratio"] == pytest.approx(1.0)
    assert [point["atm_iv"] for point in out["atm_iv_term_structure"]] == pytest.approx([0.25, 0.30])
    assert "interpolation" in out["method"]


def test_bad_or_stale_or_unsynchronized_quotes_are_excluded():
    stale = _quote(1000, "CE", received=AS_OF - timedelta(minutes=5))
    stale["source_at"] = stale["received_at"]
    stale["underlying_source_at"] = stale["received_at"]
    crossed = _quote(1000, "PE")
    crossed["bid"], crossed["ask"] = crossed["ask"], crossed["bid"]
    unsynced = _quote(950, "CE", spot_at=AS_OF - timedelta(minutes=2))
    future = _quote(1050, "PE", received=AS_OF + timedelta(seconds=1))
    future["source_at"] = AS_OF + timedelta(seconds=1)
    future["underlying_source_at"] = AS_OF + timedelta(seconds=1)
    out = build_surface([stale, crossed, unsynced, future], as_of=AS_OF, rate=RATE)
    assert out["status"] == "UNAVAILABLE"
    assert out["contracts"] == []
    assert out["rejected_counts"] == {
        "FUTURE_OR_INVERTED_CLOCK": 1,
        "NO_VALID_TWO_SIDED_BOOK": 1,
        "STALE_OPTION_QUOTE": 1,
        "UNSYNCHRONIZED_SPOT": 1,
    }


def test_missing_open_interest_is_never_fabricated():
    out = build_surface([_quote(1000, "CE", oi=None)], as_of=AS_OF, rate=RATE)
    assert out["contracts"][0]["open_interest"] is None
    assert out["expiries"][0]["call_open_interest"] is None
    assert out["expiries"][0]["put_call_oi_ratio"] is None


def test_missing_source_or_naive_clocks_cannot_create_surface_points():
    row = _quote(1000, "CE")
    del row["source_at"]
    out = build_surface([row], as_of=AS_OF, rate=RATE)
    assert out["status"] == "UNAVAILABLE"
    assert out["rejected_counts"]["MISSING_SOURCE_CLOCK"] == 1
    with pytest.raises(OptionSurfaceError, match="timezone-aware"):
        build_surface([], as_of=datetime(2026, 11, 23, 8), rate=RATE)


def test_latest_quote_per_contract_is_used_and_archive_adapter_is_as_of():
    from financial_brain.ingest import options_quotes

    received_earlier = AS_OF - timedelta(seconds=20)
    first = _quote(1000, "CE", received=received_earlier)
    latest = _quote(1000, "CE", received=AS_OF)
    latest["bid"] += 0.5
    latest["ask"] += 0.5
    out = build_surface([first, latest], as_of=AS_OF, rate=RATE)
    assert len(out["contracts"]) == 1
    assert out["contracts"][0]["midpoint"] == pytest.approx((latest["bid"] + latest["ask"]) / 2)

    now_ist = AS_OF.astimezone(options_quotes.IST)
    option_quote = {"NFO:ABC26NOV100CE": {
        "timestamp": now_ist, "oi": 50, "volume": 4,
        "depth": {"buy": [{"price": 9}], "sell": [{"price": 10}]},
    }}
    spot = {"NSE:ABC": {"last_price": 102.0, "timestamp": now_ist}}
    contract = {"instrument_token": 12, "tradingsymbol": "ABC26NOV100CE", "name": "ABC",
                "expiry": "2026-12-31", "strike": 100, "instrument_type": "CE", "lot_size": 50}
    rows = options_quotes.records([contract], option_quote, spot, now_ist)
    con = duckdb.connect(":memory:")
    con.execute(options_quotes.SCHEMA)
    con.execute("""INSERT INTO option_quotes (
        received_at, source_at, instrument_token, tradingsymbol, underlying, underlying_ltp,
        underlying_source_at, expiry, strike, option_type, lot_size, ltp, bid, ask, bid_qty,
        ask_qty, buy_depth, sell_depth, open_interest, volume, book_quality
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows[0])
    result = build_from_archive(con, as_of=AS_OF, rate=RATE)
    con.close()
    assert result["status"] == "AVAILABLE"
    assert result["contracts"][0]["open_interest"] == 50
    assert result["contracts"][0]["underlying"] == "ABC"
