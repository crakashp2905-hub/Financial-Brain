from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib

import pytest

from financial_brain.portfolio.cross_desk_overlap import (
    ExposureBook,
    ExposureComponent,
    ExposureOverlapError,
    InstrumentLookthrough,
    UnresolvedPositionExposure,
    compare_exposure_books,
    exposure_book_from_canonical_snapshot,
)
from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


AS_OF = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)


def test_overlap_is_weighted_by_delta_equivalent_and_sign():
    # An options adapter can provide NIFTY constituent delta-equivalent notional under
    # the same canonical issuer keys used by ETF, MF and equity book adapters.
    strategy = ExposureBook("H005", AS_OF, {
        "ISSUER:INE009A01021": Decimal("100000"),
        "ISSUER:INE040A01034": Decimal("50000"),
    }, source_refs=("h005-ledger:sha256:abc",))
    options = ExposureBook("NIFTY_OPTIONS", AS_OF, {
        "issuer:ine009a01021": Decimal("80000"),
        "ISSUER:INE040A01034": Decimal("-50000"),
        "ISSUER:INE062A01020": Decimal("20000"),
    }, source_refs=("option-delta-lookthrough:sha256:def",))

    result = compare_exposure_books(strategy, options)

    assert result.status == "MEASURABLE"
    assert result.known_common_gross_inr == Decimal("130000")
    assert result.signed_common_inr == Decimal("30000")
    assert result.conservative_overlap_ratio == Decimal("130000") / Decimal("150000")
    assert result.directional_overlap_ratio == Decimal("30000") / Decimal("150000")
    assert result.coverage_a == result.coverage_b == Decimal("1")
    assert result.common_exposure_keys == (
        "ISSUER:INE009A01021", "ISSUER:INE040A01034",
    )


def test_unresolved_etf_or_mf_lookthrough_reduces_conservative_coverage():
    direct = ExposureBook("P3", AS_OF, {
        "ISSUER:INE009A01021": "100",
        "ISSUER:INE040A01034": "100",
    })
    fund = ExposureBook("ETF_AND_MF", AS_OF, {
        "ISSUER:INE009A01021": "80",
    }, unresolved_gross_inr="120", source_refs=("holdings-report:sha256:xyz",))

    result = compare_exposure_books(direct, fund)

    assert result.status == "INCOMPLETE_LOOKTHROUGH"
    assert result.known_overlap_ratio == Decimal("1")
    assert result.conservative_overlap_ratio == Decimal("0.4")
    assert result.coverage_b == Decimal("0.4")
    assert result.unresolved_gross_b_inr == Decimal("120")


def test_incompatible_time_or_invalid_basis_is_rejected():
    a = ExposureBook("a", AS_OF, {"INDEX:NIFTY50": "10"})
    later = ExposureBook("b", AS_OF.replace(minute=1), {"INDEX:NIFTY50": "10"})
    with pytest.raises(ExposureOverlapError, match="identical as-of"):
        compare_exposure_books(a, later)
    with pytest.raises(ExposureOverlapError, match="namespaced"):
        ExposureBook("bad", AS_OF, {"NIFTY50": "10"})
    with pytest.raises(ExposureOverlapError, match="cannot be negative"):
        ExposureBook("bad", AS_OF, {}, unresolved_gross_inr="-1")


def test_gross_overlap_does_not_disappear_when_within_book_positions_net():
    hedged = ExposureBook("hedged", AS_OF, {"ISSUER:INE009A01021": "0"},
                          gross_exposures_inr={"ISSUER:INE009A01021": "200"})
    long_only = ExposureBook("long", AS_OF, {"ISSUER:INE009A01021": "150"})

    result = compare_exposure_books(hedged, long_only)

    assert result.known_common_gross_inr == Decimal("150")
    assert result.min_total_gross_inr == Decimal("150")
    assert result.conservative_overlap_ratio == Decimal("1")
    assert result.directional_overlap_ratio == Decimal("0")


def test_canonical_snapshot_adapter_joins_sourced_cross_asset_lookthrough(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "overlap-ledger.sqlite")
    rows = [
        ("eq-fill", EventKind.FILL, "P3", AssetClass.EQUITY, "NSE:INFY", "BUY", "10", "100", "1"),
        ("etf-fill", EventKind.FILL, "H003", AssetClass.ETF, "NSE:INDEXBEES", "BUY", "5", "20", "1"),
        ("mf-fill", EventKind.FILL, "MF", AssetClass.MUTUAL_FUND, "INF000000001", "BUY", "2.5", "40", "1"),
        ("option-fill", EventKind.FILL, "H005", AssetClass.OPTION, "NFO:NIFTY-CALL", "SELL", "1", "50", "25"),
    ]
    events = []
    for event_id, kind, sleeve, asset, instrument, side, quantity, price, multiplier in rows:
        events.append(PortfolioEvent(
            account_id="account-1", sleeve_id=sleeve, source_system="fixture",
            source_event_id=event_id, source_sha256=hashlib.sha256(event_id.encode()).hexdigest(),
            event_kind=kind, occurred_at=AS_OF.isoformat(), asset_class=asset,
            instrument_id=instrument, side=side, quantity=quantity, price=price,
            multiplier=multiplier,
        ))
        events.append(PortfolioEvent(
            account_id="account-1", sleeve_id=sleeve, source_system="fixture",
            source_event_id=f"{event_id}-mark",
            source_sha256=hashlib.sha256(f"{event_id}-mark".encode()).hexdigest(),
            event_kind=EventKind.MARK, occurred_at=AS_OF.isoformat(), asset_class=asset,
            instrument_id=instrument, price=price,
        ))
    events.append(PortfolioEvent(
        account_id="account-1", sleeve_id="H003", source_system="legacy-import",
        source_event_id="legacy-1", source_sha256=hashlib.sha256(b"legacy-1").hexdigest(),
        event_kind=EventKind.UNRESOLVED_POSITION, occurred_at=AS_OF.isoformat(),
        asset_class=AssetClass.ETF, instrument_id="NSE:OLD-ETF", quantity="1",
    ))
    ledger.import_events(events)
    snapshot = ledger.snapshot("account-1", as_of=AS_OF.isoformat())
    lookthrough = {
        ("EQUITY", "NSE:INFY"): InstrumentLookthrough((
            ExposureComponent("ISSUER:INE009A01021", "100", AS_OF, "equity-mark:sha256:a"),
        )),
        ("ETF", "NSE:INDEXBEES"): InstrumentLookthrough((
            ExposureComponent("ISSUER:INE009A01021", "15", AS_OF, "etf-holdings:sha256:b"),
        ), unresolved_gross_inr_per_unit="5", unresolved_source_ref="etf-residual:sha256:c"),
        ("MUTUAL_FUND", "INF000000001"): InstrumentLookthrough((
            ExposureComponent("ISSUER:INE009A01021", "30", AS_OF, "mf-holdings:sha256:f"),
        ), unresolved_gross_inr_per_unit="10", unresolved_source_ref="mf-residual:sha256:g"),
        ("OPTION", "NFO:NIFTY-CALL"): InstrumentLookthrough((
            ExposureComponent("ISSUER:INE009A01021", "2000", AS_OF, "option-delta:sha256:d"),
        )),
    }

    book = exposure_book_from_canonical_snapshot(
        "mixed-paper", snapshot, as_of=AS_OF, lookthrough=lookthrough,
        unresolved_legacy={"legacy-1": UnresolvedPositionExposure("100", "legacy-report:sha256:e")},
    )

    assert book.exposures_inr == {"ISSUER:INE009A01021": Decimal("-850")}
    assert book.gross_exposures_inr == {"ISSUER:INE009A01021": Decimal("3150")}
    assert book.unresolved_gross_inr == Decimal("150")
    assert book.coverage == Decimal("3150") / Decimal("3300")
    assert set(book.source_refs) == {
        "equity-mark:sha256:a", "etf-holdings:sha256:b", "etf-residual:sha256:c",
        "option-delta:sha256:d", "legacy-report:sha256:e", "mf-holdings:sha256:f",
        "mf-residual:sha256:g",
    }


def test_canonical_snapshot_adapter_rejects_unpriced_or_future_lookthrough():
    snapshot = {
        "as_of": AS_OF.isoformat(),
        "positions": [{"asset_class": "EQUITY", "instrument_id": "NSE:INFY", "sleeve_id": "P3",
                       "quantity": "1", "mark": None, "mark_at": None,
                       "currency": "INR", "multiplier": "1"}],
        "unresolved_positions": [],
    }
    component = InstrumentLookthrough((ExposureComponent(
        "ISSUER:INE009A01021", "100", AS_OF, "future-row:sha256:a"),))
    with pytest.raises(ExposureOverlapError, match="mark for NSE:INFY"):
        exposure_book_from_canonical_snapshot(
            "bad", snapshot, as_of=AS_OF,
            lookthrough={("EQUITY", "NSE:INFY"): component},
        )

    snapshot["positions"][0]["mark"] = "100"
    snapshot["positions"][0]["mark_at"] = AS_OF.isoformat()
    later = InstrumentLookthrough((ExposureComponent(
        "ISSUER:INE009A01021", "100", AS_OF + timedelta(seconds=1),
        "future-row:sha256:a"),))
    with pytest.raises(ExposureOverlapError, match="unavailable at the as-of"):
        exposure_book_from_canonical_snapshot(
            "bad", snapshot, as_of=AS_OF,
            lookthrough={("EQUITY", "NSE:INFY"): later},
        )
