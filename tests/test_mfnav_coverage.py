from datetime import date

import pytest

from financial_brain.providers.amfi import nav_coverage, parse
from financial_brain.providers.base import FetchError


def test_repeated_scheme_codes_keep_each_embedded_nav_date_and_staleness_is_explicit():
    raw = b"""Scheme Code;ISIN Growth;ISIN Reinvestment;Scheme Name;Plan;Option;NAV;Date
Open Ended Schemes(Equity)
AMC Mutual Fund
7;-;-;Example Direct;Direct Plan;Growth;10;20-Nov-2026
7;-;-;Example Direct;Direct Plan;Growth;9;02-Jul-2018
9;-;-;Old Scheme;Direct Plan;Growth;9;02-Jul-2018
"""
    rows = parse(raw)
    assert [row["nav_date"] for row in rows] == [date(2026, 11, 20), date(2018, 7, 2),
                                                date(2018, 7, 2)]
    coverage = nav_coverage(rows, ["7", "8", "9"], as_of=date(2026, 11, 23), stale_after_days=7)
    assert coverage == [
        {"scheme_code": "7", "latest_nav_date": date(2026, 11, 20), "age_days": 3,
         "status": "CURRENT"},
        {"scheme_code": "8", "latest_nav_date": None, "age_days": None,
         "status": "UNAVAILABLE"},
        {"scheme_code": "9", "latest_nav_date": date(2018, 7, 2), "age_days": 3066,
         "status": "STALE"},
    ]


def test_duplicate_scheme_and_nav_date_fails_closed_and_nonfinite_nav_is_dropped():
    raw = b"""Scheme Code;ISIN Growth;ISIN Reinvestment;Scheme Name;Plan;Option;NAV;Date
Open Ended Schemes(Equity)
AMC Mutual Fund
7;-;-;Example Direct;Direct Plan;Growth;10;20-Nov-2026
7;-;-;Example Direct;Direct Plan;Growth;11;20-Nov-2026
"""
    with pytest.raises(FetchError):
        parse(raw)
    nonfinite = raw.replace(b"10;20-Nov-2026", b"nan;20-Nov-2026").replace(
        b"11;20-Nov-2026", b"12;21-Nov-2026")
    assert len(parse(nonfinite)) == 1
