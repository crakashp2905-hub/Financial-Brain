from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.mf_orders import (
    CashState,
    Holding,
    LoadRule,
    MfOrderError,
    PublishedNav,
    Subscription,
    eligible_nav_date,
)

IST = timezone(timedelta(hours=5, minutes=30))
CUT = time(15, 0)


def weekday(d: date) -> bool:
    return d.weekday() < 5


def at(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=IST)


def test_before_cutoff_with_funds_gets_same_day():
    assert eligible_nav_date(at(17, 11), at(17, 12), cutoff=CUT, is_scheme_day=weekday,
                             tz=IST) == date(2026, 11, 17)


def test_after_cutoff_gets_next_day():
    assert eligible_nav_date(at(17, 15, 1), at(17, 15, 1), cutoff=CUT, is_scheme_day=weekday,
                             tz=IST) == date(2026, 11, 18)


def test_delayed_funds_move_nav_date():
    assert eligible_nav_date(at(17, 10), at(18, 16), cutoff=CUT, is_scheme_day=weekday,
                             tz=IST) == date(2026, 11, 19)


def test_unrealised_funds_give_unknown_date():
    assert eligible_nav_date(at(17, 10), None, cutoff=CUT, is_scheme_day=weekday, tz=IST) is None


def test_friday_after_cutoff_rolls_to_monday():
    assert eligible_nav_date(at(20, 16), at(20, 16), cutoff=CUT, is_scheme_day=weekday,
                             tz=IST) == date(2026, 11, 23)


def test_allotment_waits_for_publication_and_right_day():
    s = Subscription("O1", "IN:MF:1", D(1000), at(17, 10))
    s.accept(date(2026, 11, 17))
    nav = PublishedNav(date(2026, 11, 17), D("12.3456"), at(17, 22))
    with pytest.raises(MfOrderError):
        s.allot(nav, at(17, 18))
    with pytest.raises(MfOrderError):
        s.allot(PublishedNav(date(2026, 11, 16), D("10"), at(16, 22)), at(18, 9))
    lot = s.allot(nav, at(17, 23))
    assert lot.units == D("81.000")


def test_order_without_nav_date_cannot_allot():
    s = Subscription("O1", "IN:MF:1", D(1000), at(17, 10))
    s.accept(None)
    with pytest.raises(MfOrderError):
        s.allot(PublishedNav(date(2026, 11, 17), D(10), at(17, 22)), at(18, 9))


def test_fifo_redemption_charges_load_only_on_young_lot():
    h = Holding()
    h.lots = []
    from financial_brain.execution.mf_orders import Lot
    h.lots.append(Lot(date(2025, 1, 1), D(100), D(1000)))
    h.lots.append(Lot(date(2026, 11, 1), D(100), D(1200)))
    nav = PublishedNav(date(2026, 11, 17), D(15), at(17, 22))
    r = h.redeem(D(150), nav, at(18, 9), LoadRule(365, D("0.01")))
    assert r.gross == D(2250)
    assert r.load == D(50) * 15 * D("0.01")
    assert h.units == D(50)


def test_cannot_oversell_or_use_unpublished_nav():
    h = Holding()
    from financial_brain.execution.mf_orders import Lot
    h.lots.append(Lot(date(2026, 1, 1), D(10), D(100)))
    nav = PublishedNav(date(2026, 11, 17), D(15), at(17, 22))
    with pytest.raises(MfOrderError):
        h.redeem(D(11), nav, at(18, 9))
    with pytest.raises(MfOrderError):
        h.redeem(D(1), nav, at(17, 20))


def test_proceeds_are_not_spendable_until_settled():
    h = Holding()
    from financial_brain.execution.mf_orders import Lot
    h.lots.append(Lot(date(2026, 1, 1), D(10), D(100)))
    r = h.redeem(D(10), PublishedNav(date(2026, 11, 17), D(15), at(17, 22)), at(18, 9))
    assert r.spendable == 0
    r.advance(); r.advance()
    assert r.spendable == 0
    r.advance()
    assert r.state is CashState.CASH_AVAILABLE and r.spendable == D(150)
    with pytest.raises(MfOrderError):
        r.advance()
