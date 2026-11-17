"""Five-desk D1: instrument identity, units, clocks, corporate actions, storage and import adapters."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import duckdb
import pytest

from financial_brain.instruments import adapters, store
from financial_brain.instruments.clocks import (
    IST,
    Calendar,
    CalendarKind,
    CutoffRule,
    NavPublication,
    SessionHours,
    Stamps,
    TradingClock,
)
from financial_brain.instruments.corporate_actions import (
    Distribution,
    Holding,
    Split,
    apply_distribution,
    apply_split,
    roll_forward,
)
from financial_brain.instruments.master import (
    AssetClass,
    Desk,
    InstrumentError,
    InstrumentId,
    InstrumentMaster,
    InstrumentState,
    Provenance,
    StateChange,
    Succession,
    Terms,
    desk_allows,
)
from financial_brain.instruments.units import (
    BasisPoints,
    MfUnits,
    NavPerUnit,
    OptionLots,
    PremiumPerUnit,
    SharePrice,
    WholeUnits,
    inr,
    notional,
)
from financial_brain.kernel.clock import ClockError
from financial_brain.kernel.units import UnitError
from financial_brain.storage.db import SCHEMA

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def prov(day: date, source: str = "fixture") -> Provenance:
    t = datetime.combine(day, time(12), tzinfo=timezone.utc)
    return Provenance(source, "h", t, max(t, NOW))


def ident(uid, symbol, start, end=None, *, cls=AssetClass.EQUITY, exchange="NSE", isin=None,
          underlying=None, published=None) -> InstrumentId:
    return InstrumentId(uid=uid, asset_class=cls, market="IN", exchange=exchange, symbol=symbol, isin=isin,
                        effective_from=start, effective_to=end, underlying=underlying,
                        provenance=prov(published or start))


# ---------------------------------------------------------------------------------------------- units
def test_each_asset_class_has_its_own_units():
    assert notional("EQUITY", WholeUnits(10), SharePrice("100.05")) == inr("1000.50")
    assert notional(AssetClass.OPTION, OptionLots(2, 75), PremiumPerUnit("12.5")) == inr("1875.0")
    assert notional("MUTUAL_FUND", MfUnits("10.125"), NavPerUnit("20.0000")) == inr("202.5000000")


@pytest.mark.parametrize("cls,qty,price", [
    ("MUTUAL_FUND", OptionLots(1, 75), NavPerUnit("20")),        # lots of a fund
    ("OPTION", OptionLots(1, 75), NavPerUnit("20")),             # an option priced by NAV
    ("EQUITY", MfUnits("1.5"), SharePrice("100")),               # fractional shares
    ("ETF", WholeUnits(1), PremiumPerUnit("5")),
    ("INDEX", WholeUnits(1), SharePrice("100")),                 # an index is not tradable
])
def test_unlike_units_cannot_be_multiplied(cls, qty, price):
    with pytest.raises(UnitError):
        notional(cls, qty, price)


def test_unit_validation():
    for bad in (lambda: WholeUnits(1.5), lambda: WholeUnits(-1), lambda: WholeUnits(True),
                lambda: OptionLots(1, 0), lambda: MfUnits("1.2345"), lambda: MfUnits("-1"),
                lambda: NavPerUnit("0"), lambda: NavPerUnit("10.12345"), lambda: PremiumPerUnit("-0.05"),
                lambda: SharePrice("0")):
        with pytest.raises(UnitError):
            bad()
    assert PremiumPerUnit("0").value == 0                        # a worthless bid is a real price
    assert SharePrice("100.05").on_tick("0.05") and not SharePrice("100.03").on_tick("0.05")
    assert BasisPoints(25).of(inr(10_000)) == inr("25.0000")


def test_fund_units_are_truncated_never_rounded_up():
    units = MfUnits.allot(inr(10_000), NavPerUnit("33.3333"))
    assert units.units == Decimal("300.000")                     # 300.0003... truncated
    assert MfUnits.allot(inr(1_000), NavPerUnit("3.0000")).units == Decimal("333.333")
    assert units.units * Decimal("33.3333") <= Decimal(10_000)


# --------------------------------------------------------------------------------------------- master
def test_same_ticker_resolves_to_the_right_instrument_over_time():
    m = InstrumentMaster()
    m.add_identity(ident("IN:INE000A01010", "ABC", date(2015, 1, 1), date(2019, 12, 31),
                         isin="INE000A01010"), now=NOW)
    m.add_identity(ident("IN:INE999Z01015", "ABC", date(2021, 6, 1), isin="INE999Z01015"), now=NOW)
    assert m.resolve("NSE", "ABC", date(2018, 5, 1)).isin == "INE000A01010"
    assert m.resolve("NSE", "ABC", date(2024, 5, 1)).isin == "INE999Z01015"
    with pytest.raises(InstrumentError, match="unknown instrument"):
        m.resolve("NSE", "ABC", date(2020, 6, 1))                # the gap between the two
    with pytest.raises(InstrumentError, match="unknown instrument"):
        m.resolve("NSE", "NOPE", date(2024, 5, 1))


def test_overlapping_identities_are_rejected():
    m = InstrumentMaster()
    m.add_identity(ident("A", "ABC", date(2020, 1, 1), isin="INE000A01010"), now=NOW)
    with pytest.raises(InstrumentError, match="already active"):
        m.add_identity(ident("B", "ABC", date(2022, 1, 1)), now=NOW)                 # same symbol
    with pytest.raises(InstrumentError, match="ISIN"):
        m.add_identity(ident("B", "XYZ", date(2022, 1, 1), isin="INE000A01010"), now=NOW)
    with pytest.raises(InstrumentError, match="overlapping"):
        m.add_identity(ident("A", "ABC2", date(2023, 1, 1)), now=NOW)                # same uid, same venue
    with pytest.raises(InstrumentError, match="asset class"):
        m.add_identity(ident("A", "ABC", date(2020, 1, 1), cls=AssetClass.ETF, exchange="BSE"), now=NOW)
    m.add_identity(ident("A", "500001", date(2020, 1, 1), exchange="BSE"), now=NOW)  # second venue is fine
    assert len(m) == 1


def test_future_published_records_are_refused():
    m = InstrumentMaster()
    tomorrow = NOW + timedelta(days=1)
    late = Provenance("fixture", "h", tomorrow, tomorrow)
    with pytest.raises(InstrumentError, match="future-published"):
        m.add_identity(InstrumentId(uid="A", asset_class=AssetClass.EQUITY, market="IN", exchange="NSE",
                                    symbol="ABC", effective_from=date(2020, 1, 1), provenance=late), now=NOW)
    with pytest.raises(InstrumentError):
        Provenance("fixture", "h", NOW, NOW - timedelta(seconds=1))          # ingested before published
    with pytest.raises(InstrumentError):
        Provenance("fixture", "h", datetime(2026, 1, 1), NOW)                # naive timestamp


def test_time_travel_is_impossible():
    """A lot-size change and a suspension published later are invisible to an earlier decision."""
    m = InstrumentMaster()
    m.add_identity(ident("IDX", "NIFTY", date(2010, 1, 1), cls=AssetClass.INDEX), now=NOW)
    m.add_identity(ident("OPT", "NIFTY26DEC25000CE", date(2026, 9, 1), cls=AssetClass.OPTION,
                         underlying="IDX"), now=NOW)
    m.add_terms(Terms("OPT", date(2026, 9, 1), prov(date(2026, 9, 1)), tick="0.05", lot=75,
                      expiry=date(2026, 12, 31), strike=25000, option_type="CE"), now=NOW)
    m.add_terms(Terms("OPT", date(2026, 11, 1), prov(date(2026, 10, 20)), tick="0.05", lot=65,
                      expiry=date(2026, 12, 31), strike=25000, option_type="CE"), now=NOW)
    m.add_state(StateChange("OPT", date(2026, 9, 1), InstrumentState.LISTED, prov(date(2026, 9, 1))), now=NOW)
    m.add_state(StateChange("OPT", date(2026, 11, 10), InstrumentState.SUSPENDED, prov(date(2026, 11, 10))),
                now=NOW)

    before_notice = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert m.terms("OPT", date(2026, 11, 5), known_at=before_notice).lot == 75
    assert m.terms("OPT", date(2026, 11, 5)).lot == 65
    assert m.terms("OPT", date(2026, 10, 25)).lot == 75                     # not yet effective
    assert m.can_enter("OPT", date(2026, 11, 12)) == (False, "STATE_SUSPENDED")
    assert m.can_enter("OPT", date(2026, 11, 12), known_at=datetime(2026, 11, 9, tzinfo=timezone.utc))[0]
    assert m.can_enter("OPT", date(2027, 1, 4)) == (False, "MATURED")
    assert m.underlying_chain("OPT", date(2026, 11, 5)) == ["IDX"]
    assert m.can_enter("IDX", date(2026, 11, 5)) == (False, "INDEX_IS_NOT_TRADABLE")
    with pytest.raises(InstrumentError, match="unknown instrument"):
        m.resolve("NSE", "NIFTY26DEC25000CE", date(2026, 11, 5),
                  known_at=datetime(2026, 8, 1, tzinfo=timezone.utc))


def test_states_fail_closed_and_split_entry_from_exit():
    m = InstrumentMaster()
    m.add_identity(ident("F", "100001", date(2020, 1, 1), cls=AssetClass.MUTUAL_FUND, exchange="AMFI"),
                   now=NOW)
    assert m.can_enter("F", date(2024, 1, 1)) == (False, "STATE_UNKNOWN")
    assert m.can_enter("ZZ", date(2024, 1, 1)) == (False, "UNKNOWN_IDENTITY")
    for d, s in ((date(2020, 1, 1), InstrumentState.LISTED),
                 (date(2024, 3, 1), InstrumentState.CLOSED_FOR_PURCHASE),
                 (date(2024, 6, 1), InstrumentState.CLOSED_FOR_REDEMPTION),
                 (date(2024, 9, 1), InstrumentState.RESTRICTED)):
        m.add_state(StateChange("F", d, s, prov(d)), now=NOW)
    assert m.can_enter("F", date(2024, 2, 1))[0] and m.can_exit("F", date(2024, 2, 1))[0]
    assert not m.can_enter("F", date(2024, 4, 1))[0] and m.can_exit("F", date(2024, 4, 1))[0]
    assert m.can_enter("F", date(2024, 7, 1))[0] and not m.can_exit("F", date(2024, 7, 1))[0]
    assert not m.can_enter("F", date(2024, 10, 1))[0] and not m.can_exit("F", date(2024, 10, 1))[0]


def test_desk_and_asset_class_are_independent():
    assert desk_allows(Desk.SWING, AssetClass.ETF) and desk_allows(Desk.INTRADAY, AssetClass.ETF)
    assert not desk_allows(Desk.INTRADAY, AssetClass.MUTUAL_FUND)
    assert not desk_allows(Desk.MUTUAL_FUND, AssetClass.ETF)
    with pytest.raises(InstrumentError, match="underlying"):
        ident("O", "X", date(2026, 1, 1), cls=AssetClass.OPTION)
    with pytest.raises(InstrumentError, match="ISIN"):
        ident("E", "X", date(2026, 1, 1), isin="NOTANISIN")


# ---------------------------------------------------------------------------------- corporate actions
def test_etf_split_preserves_holding_and_cost_basis():
    h = Holding("ETF1", Decimal(37), inr(74_000))
    out = apply_split(h, Split("ETF1", date(2025, 3, 3), Decimal(10)), whole_units=True)
    assert out.holding.units == 370 and out.holding.cost == inr(74_000) and out.fraction == 0
    odd = apply_split(Holding("E", Decimal(7), inr(700)), Split("E", date(2025, 1, 1), Decimal("1.5")),
                      whole_units=True)
    assert odd.holding.units == 10 and odd.fraction == Decimal("0.5") and odd.holding.cost == inr(700)


def test_scheme_merger_preserves_holding_and_cost_basis_and_history():
    m = InstrumentMaster()
    m.add_identity(ident("IN:MF:1", "1", date(2018, 1, 1), date(2024, 6, 28), cls=AssetClass.MUTUAL_FUND,
                         exchange="AMFI"), now=NOW)
    m.add_identity(ident("IN:MF:2", "2", date(2015, 1, 1), cls=AssetClass.MUTUAL_FUND, exchange="AMFI"),
                   now=NOW)
    m.add_succession(Succession("IN:MF:1", "IN:MF:2", date(2024, 6, 29), "SCHEME_MERGER",
                                prov(date(2024, 6, 1)), ratio=Decimal("0.7421")), now=NOW)
    h = Holding("IN:MF:1", Decimal("1000.000"), inr(25_000))
    after, steps = roll_forward(h, m, after=date(2024, 1, 1), through=date(2024, 12, 31), whole_units=False)
    assert after.uid == "IN:MF:2" and after.units == Decimal("742.1000") and after.cost == inr(25_000)
    assert [s.event for s in steps] == ["SCHEME_MERGER"]
    # the extinct scheme still exists on its own dates, and a signal on it is not rewritten
    assert m.resolve("AMFI", "1", date(2023, 1, 2)).uid == "IN:MF:1"
    assert m.current("IN:MF:1", date(2023, 1, 2)) == "IN:MF:1"
    assert m.current("IN:MF:1", date(2025, 1, 2)) == "IN:MF:2"
    assert m.lineage("IN:MF:2") == ["IN:MF:1", "IN:MF:2"]
    untouched, none = roll_forward(h, m, after=date(2023, 1, 1), through=date(2024, 6, 28), whole_units=False)
    assert untouched == h and none == []
    with pytest.raises(InstrumentError, match="successor"):
        m.add_succession(Succession("IN:MF:1", "IN:MF:2", date(2025, 1, 1), "RENAME", prov(date(2025, 1, 1))),
                         now=NOW)
    with pytest.raises(InstrumentError, match="cycle"):
        m.add_succession(Succession("IN:MF:2", "IN:MF:1", date(2025, 1, 1), "RENAME", prov(date(2025, 1, 1))),
                         now=NOW)


def test_distribution_is_a_flow_not_a_loss():
    h = Holding("F", Decimal("100.000"), inr(2_000))
    paid = apply_distribution(h, Distribution("F", date(2025, 3, 3), inr(1)))
    assert paid.cash == inr("100.000") and paid.holding == h
    re = apply_distribution(h, Distribution("F", date(2025, 3, 3), inr(1), NavPerUnit("19.0000")))
    assert re.cash == inr(0) and re.holding.units == Decimal("105.263") and re.holding.cost == inr("2100.000")


# --------------------------------------------------------------------------------------------- clocks
D = date(2026, 11, 16)          # a Monday


def _clock() -> TradingClock:
    span = (date(2026, 11, 1), date(2026, 11, 30))
    exch = Calendar("NSE", CalendarKind.EXCHANGE, *span, holidays=frozenset({date(2026, 11, 18)}), source="fx")
    scheme = Calendar("SCHEME", CalendarKind.SCHEME, *span,
                      holidays=frozenset({date(2026, 11, 18), date(2026, 11, 19)}), source="fx")
    bank = Calendar("BANK", CalendarKind.BANK, *span, holidays=frozenset({date(2026, 11, 17)}), source="fx")
    return TradingClock(
        exch, scheme, bank,
        hours=(SessionHours(date(2010, 1, 1), time(9, 15), time(15, 30), "fx"),),
        cutoffs=(CutoffRule(date(2020, 1, 1), "fx", category="EQUITY_FUND", purchase=time(15),
                            redemption=time(15)),
                 CutoffRule(date(2026, 11, 1), "fx SID", scheme_uid="IN:MF:9", purchase=time(13, 30),
                            redemption=time(15))),
        publications=(NavPublication(date(2020, 1, 1), "fx", category="EQUITY_FUND", at=time(23)),
                      NavPublication(date(2020, 1, 1), "fx", category="OVERSEAS_FOF", at=time(10), day_offset=1)))


def test_calendars_are_separate_and_refuse_dates_they_do_not_cover():
    c = _clock()
    assert c.exchange.is_open(date(2026, 11, 19)) and not c.scheme.is_open(date(2026, 11, 19))
    assert c.settlement_day(D, 1) == date(2026, 11, 17)
    assert c.settlement_day(D, 1, CalendarKind.BANK) == date(2026, 11, 18)      # the bank is shut on the 17th
    assert not c.exchange.is_open(date(2026, 11, 21))                           # Saturday
    with pytest.raises(ClockError, match="does not cover"):
        c.exchange.is_open(date(2026, 12, 1))
    with pytest.raises(ClockError, match="does not cover"):
        c.exchange.next_open(date(2026, 11, 30))                                # would need December
    with pytest.raises(ClockError, match="not an open day"):
        c.exchange.shift(date(2026, 11, 18), 1)


def test_exchange_session_bounds():
    c = _clock()
    opened, closed = c.session(D)
    assert opened == datetime(2026, 11, 16, 9, 15, tzinfo=IST) and closed.time() == time(15, 30)
    assert c.in_session(datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc))      # 09:30 IST
    assert not c.in_session(datetime(2026, 11, 16, 15, 30, tzinfo=IST))         # the close is not in session
    assert not c.in_session(datetime(2026, 11, 18, 11, 0, tzinfo=IST))          # holiday
    with pytest.raises(ClockError):
        c.session(date(2026, 11, 18))
    with pytest.raises(ClockError):
        c.in_session(datetime(2026, 11, 16, 11, 0))                             # naive


def test_request_day_respects_cutoff_scheme_days_and_scheme_overrides():
    c = _clock()
    eq = dict(category="EQUITY_FUND")
    assert c.request_day("purchase", datetime(2026, 11, 16, 14, 59, 59, tzinfo=IST), **eq) == D
    assert c.request_day("purchase", datetime(2026, 11, 16, 15, 0, tzinfo=IST), **eq) == date(2026, 11, 17)
    # the 17th after cut-off skips two scheme holidays; a Saturday request moves to Monday
    assert c.request_day("redemption", datetime(2026, 11, 17, 16, 0, tzinfo=IST), **eq) == date(2026, 11, 20)
    assert c.request_day("purchase", datetime(2026, 11, 21, 10, 0, tzinfo=IST), **eq) == date(2026, 11, 23)
    # a scheme's own rule beats its category's
    own = dict(scheme_uid="IN:MF:9", category="EQUITY_FUND")
    assert c.request_day("purchase", datetime(2026, 11, 16, 14, 0, tzinfo=IST), **own) == date(2026, 11, 17)
    assert c.request_day("purchase", datetime(2026, 11, 16, 14, 0, tzinfo=IST), **eq) == D
    with pytest.raises(ClockError, match="universal rule is not assumed"):
        c.request_day("purchase", datetime(2026, 11, 16, 14, 0, tzinfo=IST), category="LIQUID")
    with pytest.raises(ClockError, match="exactly one"):
        CutoffRule(date(2020, 1, 1), "fx")


def test_same_day_nav_is_not_known_before_publication():
    c = _clock()
    eq = dict(category="EQUITY_FUND")
    assert c.nav_published_at(D, **eq) == datetime(2026, 11, 16, 23, 0, tzinfo=IST)
    assert not c.nav_known(D, datetime(2026, 11, 16, 14, 30, tzinfo=IST), **eq)   # a pre-cut-off decision
    assert not c.nav_known(D, datetime(2026, 11, 16, 22, 59, tzinfo=IST), **eq)
    assert c.nav_known(D, datetime(2026, 11, 16, 23, 0, tzinfo=IST), **eq)
    fof = dict(category="OVERSEAS_FOF")
    assert c.nav_published_at(date(2026, 11, 17), **fof) == datetime(2026, 11, 20, 10, 0, tzinfo=IST)
    with pytest.raises(ClockError, match="not a scheme business day"):
        c.nav_published_at(date(2026, 11, 18), **eq)


def test_stamps_keep_order_and_gate_use():
    t0 = datetime(2026, 11, 16, 23, 0, tzinfo=IST)
    s = Stamps(source_ts=t0, receipt_ts=t0 + timedelta(minutes=5), ingested_at=t0 + timedelta(minutes=6),
               available_at=t0 + timedelta(minutes=7), published_at=t0 + timedelta(minutes=1))
    assert not s.usable_at(t0 + timedelta(minutes=6)) and s.usable_at(t0 + timedelta(minutes=7))
    with pytest.raises(ClockError, match="out of order"):
        Stamps(source_ts=t0, receipt_ts=t0 - timedelta(seconds=1), ingested_at=t0, available_at=t0)
    with pytest.raises(ClockError, match="exchange timestamp"):
        Stamps(source_ts=t0, receipt_ts=t0, ingested_at=t0, available_at=t0,
               exchange_ts=t0 + timedelta(seconds=1))
    with pytest.raises(ClockError):
        Stamps(source_ts=t0.replace(tzinfo=None), receipt_ts=t0, ingested_at=t0, available_at=t0)


def test_observed_calendar_finds_holidays_and_special_sessions():
    sessions = [date(2026, 11, 2) + timedelta(n) for n in range(12)
                if (date(2026, 11, 2) + timedelta(n)).weekday() < 5]
    sessions.remove(date(2026, 11, 10))
    sessions.append(date(2026, 11, 8))                                           # a Sunday session
    cal = adapters.exchange_calendar(sessions, source="fixture prices")
    assert cal.holidays == {date(2026, 11, 10)} and cal.special_sessions == {date(2026, 11, 8)}
    assert cal.is_open(date(2026, 11, 8)) and not cal.is_open(date(2026, 11, 10))
    with pytest.raises(ClockError):
        cal.is_open(date(2026, 11, 14))


# ------------------------------------------------------------------------------- storage and adapters
@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute(SCHEMA.read_text(encoding="utf-8"))
    yield c
    c.close()


def test_master_and_calendar_round_trip_through_the_database(con):
    m = InstrumentMaster()
    m.add_identity(ident("IDX", "NIFTY", date(2010, 1, 1), cls=AssetClass.INDEX), now=NOW)
    m.add_identity(ident("E1", "ABC", date(2015, 1, 1), date(2022, 1, 31), isin="INE000A01010"), now=NOW)
    m.add_identity(ident("E2", "ABC", date(2022, 2, 1), isin="INE000A01028"), now=NOW)
    m.add_identity(ident("O1", "NIFTY26DEC25000CE", date(2026, 9, 1), cls=AssetClass.OPTION,
                         underlying="IDX"), now=NOW)
    m.add_terms(Terms("O1", date(2026, 9, 1), prov(date(2026, 9, 1)), tick="0.05", lot=75,
                      expiry=date(2026, 12, 31), strike="25000", option_type="CE"), now=NOW)
    m.add_state(StateChange("E2", date(2022, 2, 1), InstrumentState.LISTED, prov(date(2022, 2, 1))), now=NOW)
    m.add_succession(Succession("E1", "E2", date(2022, 2, 1), "ISIN_CHANGE", prov(date(2022, 2, 1)),
                                ratio=Decimal(5)), now=NOW)
    assert store.save(con, m) == {"identity": 4, "terms": 1, "state": 1, "succession": 1}
    back = store.load(con, now=NOW)
    assert back.records() == m.records()
    assert back.terms("O1", date(2026, 11, 1)).tick == Decimal("0.05")
    assert back.current("E1", date(2026, 1, 1)) == "E2"

    cal = _clock().scheme
    assert store.save_calendar(con, cal) == 2
    assert store.load_calendar(con, "SCHEME") == cal
    with pytest.raises(KeyError):
        store.load_calendar(con, "NOPE")


def test_a_stored_master_that_breaks_an_invariant_does_not_load(con):
    for uid in ("A", "B"):
        con.execute("INSERT INTO instrument_identity VALUES (?, 'EQUITY','IN','NSE','ABC',NULL,NULL,"
                    "DATE '2020-01-01',NULL,NULL,'x','h',TIMESTAMPTZ '2020-01-01 00:00:00+00',"
                    "TIMESTAMPTZ '2020-01-02 00:00:00+00')", [uid])
    with pytest.raises(InstrumentError, match="already active"):
        store.load(con, now=NOW)


def test_import_listings_successions_and_schemes(con):
    con.execute("""INSERT INTO securities VALUES
        ('INE000A01010', DATE '2020-01-01', DATE '2022-01-31', 'STK', 'active'),
        ('INE000A01028', DATE '2022-02-01', DATE '2026-11-13', 'STK', 'active'),
        ('INF000B01011', DATE '2021-01-04', DATE '2026-11-13', 'ETF', 'active'),
        ('INE111C01019', DATE '2020-01-01', DATE '2023-05-05', 'STK', 'active'),
        ('INE222D01017', DATE '2024-01-01', DATE '2026-11-13', 'STK', 'active')""")
    con.execute("""INSERT INTO security_listings VALUES
        ('INE000A01010','NSE','ABC','EQ','1', DATE '2020-01-01', DATE '2022-01-31'),
        ('INE000A01028','NSE','ABC','EQ','1', DATE '2022-02-01', DATE '2026-11-13'),
        ('INE000A01028','NSE','ABC','BE','1', DATE '2023-03-01', DATE '2023-03-31'),
        ('INF000B01011','NSE','GOLDX','EQ','2', DATE '2021-01-04', DATE '2026-11-13'),
        ('INE111C01019','NSE','GONE','EQ','3', DATE '2020-01-01', DATE '2023-05-05'),
        ('INE222D01017','NSE','GOLDX','EQ','4', DATE '2024-01-01', DATE '2026-11-13')""")
    con.execute("""INSERT INTO isin_successions VALUES
        ('INE000A01010','INE000A01028', DATE '2022-02-01','BSE+NSE', 0.2003,'corroborated','x',
         TIMESTAMPTZ '2022-02-02 00:00:00+00'),
        ('INE111C01019','INE222D01017', DATE '2024-01-01','NSE', 0.613,'corroborated','x',
         TIMESTAMPTZ '2024-01-02 00:00:00+00')""")
    m = InstrumentMaster()
    got = adapters.import_listings(con, m, now=NOW)
    assert got["imported"] == 4
    assert [r["ticker"] for r in got["refused"]] == ["GOLDX"]          # ticker reused while still active
    assert m.resolve("NSE", "GOLDX", date(2025, 1, 1)).asset_class is AssetClass.ETF
    assert m.resolve("NSE", "ABC", date(2021, 1, 1)).isin == "INE000A01010"
    assert m.resolve("NSE", "ABC", date(2023, 3, 15)).isin == "INE000A01028"   # series change is one span
    assert m.can_enter("IN:INE111C01019", date(2023, 5, 5))[0]
    assert m.can_enter("IN:INE111C01019", date(2023, 5, 8)) == (False, "UNKNOWN_IDENTITY")
    assert m.state("IN:INE111C01019", date(2023, 5, 8)) is InstrumentState.DELISTED
    # a listing first seen in 2022 is invisible to a 2021 decision
    with pytest.raises(InstrumentError):
        m.resolve("NSE", "ABC", date(2023, 1, 1), known_at=datetime(2021, 6, 1, tzinfo=timezone.utc))

    succ = adapters.import_successions(con, m, now=NOW)
    assert succ["imported"] == 1 and len(succ["refused"]) == 1         # 0.613 implies no simple ratio
    h, _ = roll_forward(Holding("IN:INE000A01010", Decimal(10), inr(5_000)), m, after=date(2021, 1, 1),
                        through=date(2026, 1, 1), whole_units=True)
    assert h.uid == "IN:INE000A01028" and h.units == 50 and h.cost == inr(5_000)

    con.execute("""INSERT INTO mf_nav VALUES
        ('120503','INF000K01AA1',NULL,'X Fund - Direct Plan - Growth','X AMC','Open Ended','Direct','Growth',
         51.23, DATE '2026-11-12','k1', TIMESTAMPTZ '2026-11-13 04:00:00+00'),
        ('120503','INF000K01AA1',NULL,'X Fund - Direct Plan - Growth','X AMC','Open Ended','Direct','Growth',
         51.60, DATE '2026-11-13','k2', TIMESTAMPTZ '2026-11-14 04:00:00+00'),
        ('120504',NULL,'INF000K01AB9','X Fund - Regular Plan - IDCW Reinvestment','X AMC','Open Ended',
         'Regular','IDCW Reinvestment', 20.10, DATE '2026-11-13','k2', TIMESTAMPTZ '2026-11-14 04:00:00+00')""")
    mf = adapters.import_schemes(con, m, now=NOW)
    assert mf == {"imported": 2, "refused": []}
    direct = m.resolve("AMFI", "120503", date(2026, 11, 13))
    regular = m.resolve("AMFI", "120504", date(2026, 11, 13))
    assert direct.uid != regular.uid and direct.asset_class is AssetClass.MUTUAL_FUND
    assert m.terms(direct.uid, date(2026, 11, 13)).mf_plan == "DIRECT"
    assert m.terms(regular.uid, date(2026, 11, 13)).mf_option == "IDCW_REINVEST"
    assert m.can_enter(direct.uid, date(2026, 11, 13)) == (False, "STATE_UNKNOWN")
    with pytest.raises(InstrumentError):                                # not known before we first saw it
        m.resolve("AMFI", "120503", date(2026, 11, 12), known_at=datetime(2026, 11, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("price_ratio,expected", [
    (0.2003, Decimal(5)), (0.5, Decimal(2)), (1.0, Decimal(1)), (0.667, Decimal("1.5")),
    (0.613, None), (0.76, None), (None, None), (0.0, None)])
def test_snap_ratio(price_ratio, expected):
    assert adapters.snap_ratio(price_ratio) == expected
