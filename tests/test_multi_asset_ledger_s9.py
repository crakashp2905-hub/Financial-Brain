from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.portfolio.multi_asset_ledger import Journal, LedgerError, Line, Mark

T = datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)
AGE = timedelta(hours=1)


def book():
    j = Journal()
    j.contribute("c1", T, "SWING", D(10000))
    j.contribute("c2", T, "MUTUAL_FUND", D(5000))
    return j


def test_unbalanced_entry_refused():
    with pytest.raises(LedgerError):
        Journal().post("x", T, "SWING", "bad", [Line("A", debit=D(1)), Line("B", credit=D(2))])


def test_posting_is_idempotent():
    j = book()
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    assert j.units[("SWING", "IN:A")] == D(10)
    assert j.cash("AVAILABLE", "SWING") == D(9000)


def test_sale_proceeds_unsettled_until_settlement():
    j = book()
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    j.sell("s1", T, "SWING", "IN:A", D(10), D(110), fees=D(5))
    assert j.cash("UNSETTLED", "SWING") == D(1095)
    with pytest.raises(LedgerError):
        j.buy("b2", T, "SWING", "IN:B", D(100), D(100))
    j.settle("st1", T, "SWING", D(1095))
    assert j.cash("AVAILABLE", "SWING") == D(10095)
    assert j.balance("INCOME:REALIZED", "SWING") == D(-100)  # credit-side income
    j.check()


def test_cannot_oversell():
    j = book()
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    with pytest.raises(LedgerError):
        j.sell("s1", T, "SWING", "IN:A", D(11), D(100))


def test_mf_pending_cash_is_not_spendable_and_allot_moves_it():
    j = book()
    j.mf_request("m1", T, "MUTUAL_FUND", D(5000))
    assert j.cash("AVAILABLE", "MUTUAL_FUND") == 0
    assert j.cash("MF_PENDING", "MUTUAL_FUND") == D(5000)
    j.mf_allot("m2", T, "MUTUAL_FUND", "IN:MF:1", D(5000), D("400.000"))
    assert j.cash("MF_PENDING", "MUTUAL_FUND") == 0
    j.check()


def test_desk_navs_sum_to_consolidated():
    j = book()
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    j.mf_request("m1", T, "MUTUAL_FUND", D(1000))
    j.mf_allot("m2", T, "MUTUAL_FUND", "IN:MF:1", D(1000), D(100))
    marks = {"IN:A": Mark(D(105), T), "IN:MF:1": Mark(D(11), T)}
    total = j.nav(marks, T, AGE)["nav"]
    parts = j.nav(marks, T, AGE, "SWING")["nav"] + j.nav(marks, T, AGE, "MUTUAL_FUND")["nav"]
    assert total == parts == D(15000) + D(50) + D(100)


def test_stale_mark_makes_nav_unpriced_not_zero():
    j = book()
    j.buy("b1", T, "SWING", "IN:A", D(10), D(100))
    r = j.nav({"IN:A": Mark(D(105), T - timedelta(days=2))}, T, AGE)
    assert r["nav"] is None and r["unpriced"] == ["IN:A"]


def test_exposure_overlap_summed_by_underlying():
    j = book()
    j.underlying.update({"IN:NIFTYBEES": "NIFTY", "IN:MF:IDX": "NIFTY"})
    j.buy("b1", T, "SWING", "IN:NIFTYBEES", D(10), D(250))
    j.mf_request("m1", T, "MUTUAL_FUND", D(1000))
    j.mf_allot("m2", T, "MUTUAL_FUND", "IN:MF:IDX", D(1000), D(100))
    exp = j.exposure_by_underlying({"IN:NIFTYBEES": Mark(D(250), T), "IN:MF:IDX": Mark(D(10), T)})
    assert exp == {"NIFTY": D(3500)}
