"""Cash-and-carry: expiry calendar (Thursday, then Tuesday from Sept 2025), and the carry earned by a converging basis."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import basis as BA


def sessions(a, b):
    return [d.date() for d in pd.bdate_range(a, b)]


def test_expiry_is_the_last_thursday_then_the_last_tuesday():
    s = sessions("2025-07-01", "2025-11-30")
    assert BA.expiry_for(date(2025, 7, 10), s) == date(2025, 7, 31)          # Thursday
    assert BA.expiry_for(date(2025, 8, 1), s) == date(2025, 8, 28)
    assert BA.expiry_for(date(2025, 9, 5), s) == date(2025, 9, 30)           # Tuesday from September 2025
    s2 = [d for d in s if d != date(2025, 10, 28)]                             # a holiday on expiry day
    assert BA.expiry_for(date(2025, 10, 6), s2) == date(2025, 10, 27)


def test_a_converging_basis_is_earned_and_judged_against_the_risk_free_rate():
    s = sessions("2025-06-02", "2025-07-31")
    idx = [d for d in s if d >= date(2025, 7, 1)]
    spot = pd.Series(np.linspace(100, 104, len(idx)), index=idx)
    basis = np.linspace(0.006, 0.0, len(idx))                                 # 0.6% at the start, 0 at expiry
    fut = spot * (1 + basis)
    c = BA.contracts(fut, spot, s)
    assert len(c) == 1 and c["gross"].iloc[0] == pytest.approx(0.006, abs=1e-6)
    sm = BA.summarise(c)
    assert sm["excess_over_rf_per_contract"] == pytest.approx(0.006 - BA.COSTS - BA.RISK_FREE * c["days"].iloc[0] / 365)
