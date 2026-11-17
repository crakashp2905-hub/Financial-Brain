from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.mf_plans import Folio, PlanError, stamp_duty
from financial_brain.portfolio.multi_asset_ledger import Journal, LedgerError, Mark

T = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def book():
    j = Journal(); j.contribute("c", T, "MUTUAL_FUND", D(1000))
    j.mf_request("r", T, "MUTUAL_FUND", D(1000))
    j.mf_allot("a", T, "MUTUAL_FUND", "IN:MF:1", D(1000), D(100))
    return j


def test_idcw_payout_and_reinvest_posted():
    j = book()
    j.distribution("d1", T, "MUTUAL_FUND", "IN:MF:1", D(20))
    j.distribution("d2", T, "MUTUAL_FUND", "IN:MF:1", D(10), reinvest_units=D("1.000"))
    j.check()
    assert j.balance("INCOME:DISTRIBUTION", "MUTUAL_FUND") == D(-30)
    assert j.units[("MUTUAL_FUND", "IN:MF:1")] == D(101)
    nav = j.nav({"IN:MF:1": Mark(D(10), T)}, T, max_age=timedelta(days=1))
    assert nav["nav"] == D(20) + D(1010)
    with pytest.raises(LedgerError):
        j.distribution("d3", T, "MUTUAL_FUND", "IN:MF:1", D(0))


def test_stamp_duty_charge_posted():
    j = Journal(); j.contribute("c", T, "MUTUAL_FUND", D(1000))
    j.charge("s", T, "MUTUAL_FUND", "stamp_duty", stamp_duty(D(1000), D("0.00005")))
    assert j.cash("AVAILABLE", "MUTUAL_FUND") == D("999.95")
    with pytest.raises(PlanError):
        stamp_duty(D(1), D(-1))


def test_folio_restrictions():
    f = Folio("F1", "paper", schemes={"S1"})
    f.check("S1", redeem=True)
    with pytest.raises(PlanError):
        f.check("S2", redeem=True)
    with pytest.raises(PlanError):
        Folio("F2", "paper", frozen=True).check("S1", redeem=False)
    with pytest.raises(PlanError):
        Folio("F3", "paper", kyc_ok=False).check("S1", redeem=False)
