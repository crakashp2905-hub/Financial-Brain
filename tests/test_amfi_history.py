from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.providers.amfi_history import AmfiHistoryError, ingest, parse, resume_from, windows

HDR = "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;" \
      "Repurchase Price;Sale Price;Date\n"


def test_windows_bounded_and_contiguous():
    w = windows(date(2026, 1, 1), date(2026, 12, 31))
    assert all((e - s).days < 90 for s, e in w)
    assert w[0][0] == date(2026, 1, 1) and w[-1][1] == date(2026, 12, 31)
    assert all((w[i + 1][0] - w[i][1]).days == 1 for i in range(len(w) - 1))
    with pytest.raises(AmfiHistoryError):
        windows(date(2026, 1, 1), date(2026, 2, 1), max_days=91)


def test_resume():
    assert resume_from(None, date(2020, 1, 1)) == date(2020, 1, 1)
    assert resume_from(date(2026, 3, 4), date(2020, 1, 1)) == date(2026, 3, 5)


def test_parse_filters_window_and_junk():
    t = HDR + "100;Fund A;X;Y;12.34;;;02-Jan-2026\n100;Fund A;X;Y;N.A.;;;03-Jan-2026\n" \
              "100;Fund A;X;Y;13;;;05-Mar-2026\nOpen Ended Schemes\n"
    r = parse(t, date(2026, 1, 1), date(2026, 1, 31))
    assert len(r) == 1 and r[0].nav == Decimal("12.34")


def test_ingest_retries_and_spacing():
    calls, sleeps = [], []

    def fetch(lo, hi):
        calls.append(lo)
        if len(calls) == 1:
            raise TimeoutError
        return HDR + f"1;F;;;10;;;{lo.strftime('%d-%b-%Y')}\n"

    rows, failed = ingest(date(2026, 1, 1), date(2026, 5, 1), fetch, sleep=sleeps.append)
    assert not failed and len(rows) == 2 and sleeps == [2.0, 1.0]

    def bad(lo, hi):
        raise OSError

    _, failed = ingest(date(2026, 1, 1), date(2026, 1, 5), bad, sleep=lambda s: None, retries=1)
    assert failed == [(date(2026, 1, 1), date(2026, 1, 5))]
