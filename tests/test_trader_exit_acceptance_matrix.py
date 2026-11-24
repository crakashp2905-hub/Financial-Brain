"""Table-driven cross-asset exits, costs, option identity and MF NAV acceptance matrix."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import pytest

from financial_brain.costs.india import CostModel, Segment, Side
from financial_brain.kernel.fills import OhlcBar, resolve_stop_target
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar,
    SchemeTransactionRule,
    TransactionKind,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.intraday import DayEngine, Trade
from financial_brain.paper.options import OptionPaperError, Quote, long_round_trip
from financial_brain.strategies.four_node.rules import round_stop

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 5, 0, tzinfo=UTC)
HASH = "a" * 64


@pytest.mark.parametrize(
    "side,bar,stop,target,expected",
    [
        ("BUY", OhlcBar(100, 102, 98, 100, 1000), 99, 105, ("STOP", 99)),
        ("BUY", OhlcBar(100, 106, 100, 104, 1000), 95, 105, ("TARGET", 105)),
        ("SELL", OhlcBar(100, 102, 98, 100, 1000), 101, 95, ("STOP", 101)),
        ("SELL", OhlcBar(100, 100, 94, 96, 1000), 105, 95, ("TARGET", 95)),
    ],
)
def test_long_short_stop_target_inequalities_table(side, bar, stop, target, expected):
    assert resolve_stop_target(bar, side, stop, target) == expected


@pytest.mark.parametrize(
    "side,raw,tick,expected",
    [("BUY", 99.97, 0.05, 99.95), ("SELL", 100.02, 0.05, 100.05)],
)
def test_tick_rounding_never_tightens_stop_table(side, raw, tick, expected):
    assert round_stop(raw, tick, side) == pytest.approx(expected)


@pytest.mark.parametrize(
    "side,entry,stop,exit_price,expected_r",
    [("long", 100.0, 98.0, 104.0, 2.0), ("short", 100.0, 102.0, 96.0, 2.0)],
)
def test_realized_r_multiple_uses_side_and_initial_stop_risk(side, entry, stop, exit_price, expected_r):
    engine = DayEngine("2026-11-20", strategies=())
    engine.fill = lambda _symbol, price, *, buying: (price, 0.0)
    trade = Trade("trade-1", "2026-11-20", "TEST", "NIFTY", side, 10,
                  NOW.isoformat(), entry, stop, entry + 10 if side == "long" else entry - 10)
    engine._close(trade, NOW + timedelta(minutes=1), exit_price, "target")
    assert trade.risk_inr == pytest.approx(20.0)
    assert trade.r_multiple == pytest.approx(expected_r)


@pytest.mark.parametrize("segment", [Segment.DELIVERY, Segment.INTRADAY, Segment.FUT, Segment.OPT])
@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_cost_line_items_reconcile_for_each_segment_and_side(segment, side):
    model = CostModel()
    row = model.one_way(turnover=100_000, side=side, segment=segment, include_impact=True)
    components = ("stt", "exchange", "sebi", "stamp", "brokerage", "gst", "dp", "impact")
    assert row["total"] == pytest.approx(sum(row[key] for key in components))
    assert row["total"] > 0


@pytest.mark.parametrize("identity_field", ["token", "symbol", "expiry", "lot_size"])
def test_option_exit_must_match_the_exact_entry_contract(identity_field):
    expiry = date(2026, 12, 31)
    entry = Quote(11, "NIFTY26DEC20000CE", expiry, 25, NOW, 99, 100, 500, 500)
    changes = {"token": 12, "symbol": "NIFTY26DEC20100CE", "expiry": date(2027, 1, 28), "lot_size": 50}
    values = {key: getattr(entry, key) for key in ("token", "symbol", "expiry", "lot_size")}
    values[identity_field] = changes[identity_field]
    exit_quote = Quote(values["token"], values["symbol"], values["expiry"], values["lot_size"],
                       NOW + timedelta(minutes=1), 105, 106, 500, 500)
    with pytest.raises(OptionPaperError, match="same contract"):
        long_round_trip(entry, exit_quote, lots=1)


@pytest.mark.parametrize("observed_at", [NOW, NOW + timedelta(days=1)])
def test_option_quotes_at_or_after_expiry_are_rejected(observed_at):
    quote = Quote(11, "NIFTY26NOV20000CE", date(2026, 11, 20), 25,
                  observed_at, 99, 100, 500, 500)
    with pytest.raises(OptionPaperError, match="before expiry"):
        quote.validate()


def _mf_engine() -> SchemeTransactionCalendar:
    open_days = frozenset({date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)})
    business = BusinessCalendar(
        calendar_id="FUND-A", domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 11, 20), source_as_of=date(2026, 11, 24),
        open_days=open_days, source_ref="FUND-A-KIM", source_sha256=HASH, recorded_at=NOW,
    )
    rule = SchemeTransactionRule(
        scheme_id="FUND-A", transaction_kind=TransactionKind.PURCHASE,
        effective_from=date(2026, 11, 1), effective_to=None, cutoff_local=time(15),
        before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=0,
        published_at=NOW, source_ref="FUND-A-KIM-cutoff", source_sha256=HASH,
    )
    return SchemeTransactionCalendar({"FUND-A": business}, (rule,))


@pytest.mark.parametrize(
    "hour,minute,expected_nav_date",
    [(14, 59, date(2026, 11, 20)), (15, 1, date(2026, 11, 23))],
)
def test_mutual_fund_nav_cutoff_threshold_table(hour, minute, expected_nav_date):
    received = datetime(2026, 11, 20, hour, minute, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert _mf_engine().assign_nav_date(
        "FUND-A", TransactionKind.PURCHASE, received_at=received,
    ) == expected_nav_date



