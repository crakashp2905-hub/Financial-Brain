from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.portfolio.cross_desk_overlap import (
    ExposureBook,
    ExposureOverlapError,
    compare_exposure_books,
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
