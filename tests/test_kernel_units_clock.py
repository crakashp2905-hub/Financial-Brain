from __future__ import annotations
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from financial_brain.kernel.clock import ClockError, Instants, assert_not_future, utc
from financial_brain.kernel.units import BasisPoints, CurrencyCode, INRNotional, Money, MutualFundUnits, NavPerUnit, OptionLots, OptionPremiumPerUnit, Price, Quantity, UnitError, WholeUnits
from datetime import time
from financial_brain.kernel.clock import FundTransactionCalendar, FundTransactionRule, FundTransactionType, MarketFactTimes, TradingClock
from financial_brain.kernel.closures_n import MarketCalendar
from financial_brain.kernel.units import EquityUnits, ETFUnits, NAVPerUnit, SignedOptionLots, PremiumPerUnit
IST = timezone(timedelta(hours=5, minutes=30))

def test_money_arithmetic_is_exact_decimal() -> None:
    total = Money.of('0.10', 'INR') + Money.of('0.20', 'INR')
    assert total.amount == Decimal('0.30')

def test_money_refuses_currency_mixing() -> None:
    with pytest.raises(UnitError, match='currency mismatch'):
        _ = Money.of(1, 'USD') + Money.of(1, 'INR')

def test_money_refuses_price_and_quantity_mixing() -> None:
    with pytest.raises(UnitError):
        _ = Money.of(1, 'USD') + Price(Decimal('1'), 'USD')
    with pytest.raises(UnitError):
        _ = Quantity(Decimal('1')) + Money.of(1, 'USD')

@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, 'abc', None])
def test_non_finite_or_non_numeric_amounts_rejected(bad: object) -> None:
    with pytest.raises(UnitError):
        Money.of(bad, 'USD')

def test_currency_must_be_three_upper_case_letters() -> None:
    for code in ('usd', 'US', 'USDX', '1RS'):
        with pytest.raises(UnitError):
            Money.of(1, code)

def test_float_conversion_uses_shortest_decimal() -> None:
    assert Money.of(0.1, 'USD').amount == Decimal('0.1')
    assert math.isfinite(float(Money.of(2.5, 'USD').amount))

def test_price_notional_applies_multiplier_and_currency() -> None:
    notional = Price(Decimal('250.50'), 'INR').notional(Quantity(Decimal('100')), multiplier=50)
    assert notional == Money.of('1252500', 'INR')

def test_domain_financial_units_are_explicit_and_validated() -> None:
    assert CurrencyCode('INR').value == 'INR'
    assert INRNotional.from_money(Money.of('100.25', 'INR')).amount == Decimal('100.25')
    assert WholeUnits(Decimal('-5')).units == Decimal('-5')
    assert OptionLots(2).lots == 2
    assert MutualFundUnits(Decimal('1.234567')).units == Decimal('1.234567')
    assert NavPerUnit(Decimal('42.15')).currency == 'INR'
    assert OptionPremiumPerUnit(Decimal('125.5')).amount == Decimal('125.5')
    assert BasisPoints(Decimal('12.5')).as_rate() == Decimal('0.00125')

def test_domain_financial_units_reject_mismatches() -> None:
    with pytest.raises(UnitError, match='whole units'):
        WholeUnits(Decimal('1.5'))
    with pytest.raises(UnitError, match='positive integer'):
        OptionLots(Decimal('1.5'))
    with pytest.raises(UnitError, match='INR currency'):
        INRNotional.from_money(Money.of(1, 'USD'))
    with pytest.raises(UnitError, match='denominated in INR'):
        NavPerUnit(Decimal('2'), currency='USD')

def test_price_must_be_positive() -> None:
    with pytest.raises(UnitError):
        Price(Decimal('0'), 'USD')

def test_naive_datetime_rejected() -> None:
    with pytest.raises(ClockError, match='naive'):
        utc(datetime(2026, 11, 8, 9, 15))

def test_aware_datetime_normalised_to_utc() -> None:
    ist_open = datetime(2026, 11, 9, 9, 15, tzinfo=IST)
    assert utc(ist_open) == datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)

def test_instants_must_be_ordered() -> None:
    t0 = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)
    Instants(t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2), t0 + timedelta(seconds=3))
    with pytest.raises(ClockError, match='out of order'):
        Instants(t0, t0, t0 + timedelta(seconds=5), t0 + timedelta(seconds=1))

def test_future_timestamp_refused() -> None:
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    with pytest.raises(ClockError, match='after the reference clock'):
        assert_not_future(now + timedelta(minutes=1), now)

def test_date_import_is_used() -> None:
    assert date(2026, 11, 8).isoformat() == '2026-11-08'

def test_financial_domain_units_keep_distinct_semantics() -> None:
    assert CurrencyCode('INR').code == 'INR'
    assert INRNotional('123.45').as_money() == Money.of('123.45', 'INR')
    assert isinstance(EquityUnits(5), EquityUnits)
    assert isinstance(ETFUnits(2), ETFUnits)
    assert SignedOptionLots(-3).lots == -3
    assert MutualFundUnits('1.234567').units == Decimal('1.234567')
    assert NAVPerUnit('42.10').amount == Decimal('42.10')
    assert PremiumPerUnit('18.25').currency == 'INR'
    assert BasisPoints('12.5').as_ratio() == Decimal('0.00125')

@pytest.mark.parametrize('constructor', [EquityUnits, ETFUnits, OptionLots])
@pytest.mark.parametrize('bad', [1.5, '2', True])
def test_whole_share_and_option_lot_units_reject_non_integers(constructor, bad) -> None:
    with pytest.raises(UnitError):
        constructor(bad)

def test_inr_notional_rejects_negative_amount() -> None:
    with pytest.raises(UnitError, match='cannot be negative'):
        INRNotional('-0.01')

def test_trading_clock_separates_exchange_scheme_and_bank_calendars() -> None:
    holiday = date(2026, 11, 9)
    clock = TradingClock(exchange_calendar=MarketCalendar(frozenset({holiday})), bank_settlement_calendar=MarketCalendar(), scheme_calendars={'scheme-1': MarketCalendar()}, exchange_hours={'NSE': (time(9, 15), time(15, 30))})
    assert not clock.is_exchange_session(holiday)
    assert clock.is_bank_settlement_day(holiday)
    assert clock.is_scheme_business_day('scheme-1', holiday)
    assert clock.timezone.key == 'Asia/Kolkata'
    nav_time = clock.nav_publication_at('scheme-1', holiday, time(20, 0))
    assert nav_time.tzinfo is not None and nav_time.hour == 20

def test_trading_clock_refuses_missing_scheme_calendar_and_exchange_hours() -> None:
    clock = TradingClock()
    with pytest.raises(ClockError, match='no reviewed business calendar'):
        clock.is_scheme_business_day('unknown', date(2026, 11, 9))
    with pytest.raises(ClockError, match='no session hours configured'):
        clock.exchange_is_open('NSE', datetime(2026, 11, 9, 10, tzinfo=IST))

def test_trading_clock_uses_exchange_hours_in_kolkata() -> None:
    clock = TradingClock(exchange_hours={'NSE': (time(9, 15), time(15, 30))})
    at_open = datetime(2026, 11, 9, 9, 15, tzinfo=IST)
    at_close = datetime(2026, 11, 9, 15, 30, tzinfo=IST)
    assert clock.exchange_is_open('NSE', at_open)
    assert not clock.exchange_is_open('NSE', at_close)

def test_market_fact_times_preserve_all_provenance_instants() -> None:
    received = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)
    fact_times = MarketFactTimes(received_at=received, available_at=received + timedelta(milliseconds=20), ingested_at=received + timedelta(seconds=1), source_at=received - timedelta(milliseconds=50), exchange_at=received - timedelta(milliseconds=100), published_at=received)
    fact_times.assert_usable_at(received + timedelta(seconds=2))
    assert fact_times.exchange_at == received - timedelta(milliseconds=100)
    assert fact_times.source_at == received - timedelta(milliseconds=50)

def test_market_fact_times_reject_impossible_or_not_yet_usable_data() -> None:
    received = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)
    with pytest.raises(ClockError, match='cannot precede receipt'):
        MarketFactTimes(received, received - timedelta(seconds=1), received)
    future_published = MarketFactTimes(received_at=received, available_at=received + timedelta(seconds=2), ingested_at=received + timedelta(seconds=3), published_at=received + timedelta(seconds=1))
    with pytest.raises(ClockError, match='not yet available'):
        future_published.assert_usable_at(received + timedelta(milliseconds=500))
    with pytest.raises(ClockError, match='cannot precede publication'):
        MarketFactTimes(received_at=received, available_at=received + timedelta(milliseconds=500), ingested_at=received + timedelta(seconds=1), published_at=received + timedelta(seconds=1))

def test_fund_transaction_cutoffs_are_scheme_and_action_specific() -> None:
    day = date(2026, 11, 9)
    published = datetime(2026, 11, 1, tzinfo=timezone.utc)
    calendar = FundTransactionCalendar([FundTransactionRule(scheme_id='scheme-a', transaction_type=FundTransactionType.PURCHASE.value, effective_from=day, cutoff=time(13, 0), source_kind='KIM', source_ref='https://example.invalid/scheme-a-kim.pdf', source_sha256='a' * 64, published_at=published), FundTransactionRule(scheme_id='scheme-a', transaction_type=FundTransactionType.REDEMPTION.value, effective_from=day, cutoff=time(15, 0), source_kind='AMC_NOTICE', source_ref='notice-2026-11', source_sha256='b' * 64, published_at=published)])
    clock = TradingClock(scheme_calendars={'scheme-a': MarketCalendar()}, fund_transaction_calendar=calendar)
    at_14 = datetime(2026, 11, 9, 14, 0, tzinfo=IST)
    assert clock.accepts_fund_transaction('scheme-a', FundTransactionType.PURCHASE, at_14) == (False, 'PAST_FUND_TRANSACTION_CUTOFF')
    assert clock.accepts_fund_transaction('scheme-a', FundTransactionType.REDEMPTION, at_14) == (True, 'WITHIN_FUND_TRANSACTION_CUTOFF')

def test_fund_transaction_calendar_has_no_universal_fallback() -> None:
    clock = TradingClock()
    with pytest.raises(ClockError, match='no reviewed business calendar'):
        clock.accepts_fund_transaction('unknown', FundTransactionType.PURCHASE, datetime(2026, 11, 9, 10, 0, tzinfo=IST))

def test_fund_cutoff_notice_must_be_published_before_use() -> None:
    day = date(2026, 11, 9)
    rule = FundTransactionRule(scheme_id='scheme-a', transaction_type=FundTransactionType.PURCHASE.value, effective_from=day, cutoff=time(13, 0), source_kind='SID', source_ref='sid-version-2', source_sha256='c' * 64, published_at=datetime(2026, 11, 9, 10, 0, tzinfo=IST))
    clock = TradingClock(scheme_calendars={'scheme-a': MarketCalendar()}, fund_transaction_calendar=FundTransactionCalendar([rule]))
    with pytest.raises(ClockError, match='not yet published'):
        clock.accepts_fund_transaction('scheme-a', FundTransactionType.PURCHASE, datetime(2026, 11, 9, 9, 30, tzinfo=IST))
