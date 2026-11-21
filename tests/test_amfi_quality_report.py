from datetime import date

import pytest

from financial_brain.providers.amfi import parse_quality_report


NAV_FILE = b"""Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
Open Ended Schemes(Equity Scheme - Small Cap Fund)
Example Mutual Fund
111;INF000000001;-;Example Small Fund;Direct Plan;Growth Option;10.00;01-Jan-2026
111;INF000000001;-;Example Small Fund;Direct Plan;Growth Option;11.00;08-Jan-2026
222;-;-;Unavailable Fund;Direct Plan;Growth Option;N.A.;08-Jan-2026
"""


def test_quality_report_preserves_repeated_rows_and_flags_stale_or_unavailable():
    report = parse_quality_report(
        NAV_FILE, stale_after_days=3, known_scheme_codes=("111", "222", "333")
    )

    assert report["latest_nav_date"] == "2026-01-08"
    assert report["repeated_scheme_codes"] == ["111"]
    assert report["stale_scheme_codes"] == ["111"]
    rows = report["rows"]
    assert len(rows) == 2
    assert [row["nav_date"] for row in rows] == [date(2026, 1, 1), date(2026, 1, 8)]
    unavailable = report["unavailable"]
    assert {row["scheme_code"] for row in unavailable} == {"222", "333"}
    assert {row["reason"] for row in unavailable} == {
        "NAV_UNAVAILABLE", "MISSING_FROM_SNAPSHOT",
    }


def test_report_needs_explicit_staleness_threshold():
    with pytest.raises(ValueError, match="stale_after_days"):
        parse_quality_report(NAV_FILE, stale_after_days=-1)
